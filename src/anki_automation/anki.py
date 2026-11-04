"""A local-only client for every method in the pinned Anki-Connect API v1."""

import copy
import http.client
import json
import math
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from anki_automation.contract import ContractError, decode_json, get_contract
from anki_automation.errors import AnkiError, OperationTimeout

MAX_BODY_BYTES = 16 * 1024 * 1024


def _positive(value: float, name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise AnkiError(f"{name} must be a finite positive number")


def _api_error(
    error: dict[str, object],
    *,
    method: str | None = None,
    result: object = None,
    operation_id: str | None = None,
) -> AnkiError:
    return AnkiError(
        f"Anki API error {error.get('code')}: {error.get('message')}",
        code=cast(int, error.get("code")),
        data=error.get("data"),
        method=method,
        result=result,
        operation_id=operation_id,
    )


def _has_item_errors(value: object) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        return False
    return any(
        isinstance(item, dict) and ("error" in item or _has_item_errors(item.get("result")))
        for item in value["items"]
    )


@dataclass(slots=True)
class AnkiClient:
    """Use loopback HTTP. Validate requests; require apply=True for side effects."""

    port: int = 8765
    timeout: float = 10.0
    api_key: str | None = field(default=None, repr=False)
    _request_id: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise AnkiError("port must be between 1 and 65535")
        _positive(self.timeout, "timeout")
        if self.api_key is not None and any(char in self.api_key for char in "\r\n"):
            raise AnkiError("ANKI_CONNECT_KEY must not contain newlines")

    def call(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
        *,
        apply: bool = False,
        wait: bool = False,
        wait_timeout: float = 300.0,
        poll_interval: float = 0.25,
    ) -> object:
        """Call any catalog method. Operation handles are not completion proof."""
        contract = get_contract()
        meta = contract.method(method)
        prepared = copy.deepcopy(dict(params) if params is not None else {})
        contract.validate(method, prepared)
        if meta.requires_apply and not apply:
            raise AnkiError(f"{method} has side effects; pass apply=True", method=method)
        if wait:
            if meta.completion != "operation":
                raise AnkiError(f"{method} does not return an operation handle")
            _positive(wait_timeout, "wait_timeout")
            _positive(poll_interval, "poll_interval")
        self._pin_session(method, prepared)
        result = self._send(method, prepared)
        if wait:
            try:
                return self.wait_for_operation(
                    result["operationId"],
                    wait_timeout=wait_timeout,
                    poll_interval=poll_interval,
                    expected_method=method,
                )
            except KeyboardInterrupt:
                raise AnkiError(
                    "waiting interrupted; the operation was not cancelled",
                    operation_id=result["operationId"],
                    result=result,
                ) from None
        return result

    def _pin_session(self, method: str, params: dict[str, object]) -> None:
        contract = get_contract()
        meta = contract.method(method)
        targets = []
        if meta.scope == "collection" and meta.requires_apply:
            targets = [params]
        elif method == "api.execute":
            for command in cast(list[dict[str, Any]], params["commands"]):
                if contract.method(command["method"]).scope == "collection":
                    targets.append(command.setdefault("params", {}))
        if not targets:
            return
        sessions = {target["session"] for target in targets if "session" in target}
        if len(sessions) > 1:
            raise AnkiError("api.execute commands must use the same collection session")
        session = next(iter(sessions)) if sessions else self._send("collection.get", {})["session"]
        for target in targets:
            target.setdefault("session", session)

    def _send(self, method: str, params: dict[str, object], *, timeout: float | None = None) -> Any:
        self._request_id += 1
        try:
            body = json.dumps(
                {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params},
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (ValueError, TypeError, UnicodeError) as error:
            raise AnkiError(
                "request must contain finite, UTF-8 JSON values", method=method
            ) from error
        if len(body) > MAX_BODY_BYTES:
            raise AnkiError(
                "request exceeds the 16 MiB API limit; split the input file", method=method
            )
        headers = {"Content-Type": "application/json"}
        if self.api_key is not None:
            headers["Authorization"] = f"Bearer {self.api_key}"
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.port, timeout=self.timeout if timeout is None else timeout
        )
        try:
            connection.request("POST", "/api/v1", body=body, headers=headers)
            with connection.getresponse() as response:
                if response.status != 200:
                    raise AnkiError(f"Anki returned HTTP {response.status}", method=method)
                raw = response.read(MAX_BODY_BYTES + 1)
                if len(raw) > MAX_BODY_BYTES:
                    raise AnkiError("Anki response exceeds the 16 MiB client limit", method=method)
        except (OSError, http.client.HTTPException) as error:
            raise AnkiError(
                f"Anki transport failed for {method}. A sent write has an unknown outcome; "
                "inspect Anki before repeating it.",
                method=method,
            ) from error
        finally:
            connection.close()
        try:
            reply = decode_json(raw)
        except ContractError as error:
            raise AnkiError(
                "Anki returned invalid JSON; inspect Anki before repeating a write", method=method
            ) from error
        if (
            not isinstance(reply, dict)
            or reply.get("jsonrpc") != "2.0"
            or type(reply.get("id")) is not int
            or reply["id"] != self._request_id
            or (("result" in reply) == ("error" in reply))
        ):
            raise AnkiError("invalid JSON-RPC response; inspect Anki before repeating a write")
        if "error" in reply:
            error = reply["error"]
            if (
                not isinstance(error, dict)
                or type(error.get("code")) is not int
                or not isinstance(error.get("message"), str)
            ):
                raise AnkiError("Anki returned an invalid error record", method=method)
            raise _api_error(error, method=method)
        result = reply["result"]
        try:
            get_contract().validate(method, result, part="result")
        except ContractError as error:
            raise AnkiError(
                f"{error}; inspect Anki before repeating a write", method=method, result=result
            ) from error
        if _has_item_errors(result) or (
            method == "operations.get" and _has_item_errors(result.get("result"))
        ):
            raise AnkiError(
                f"{method} returned item errors",
                method=method,
                result=result,
                operation_id=result.get("operationId") if isinstance(result, dict) else None,
            )
        if method == "operations.get" and result["state"] in {"failed", "cancelled"}:
            raise _api_error(
                result["error"],
                method=result["method"],
                result=result,
                operation_id=result["operationId"],
            )
        return result

    def wait_for_operation(
        self,
        operation_id: str,
        *,
        wait_timeout: float = 300.0,
        poll_interval: float = 0.25,
        expected_method: str | None = None,
    ) -> object:
        """Poll a retained handle. Timeout and disconnect do not cancel server work."""
        _positive(wait_timeout, "wait_timeout")
        _positive(poll_interval, "poll_interval")
        get_contract().validate("operations.get", {"operationId": operation_id})
        deadline = time.monotonic() + wait_timeout
        last: object = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OperationTimeout(
                    "operation is still pending; retain its ID and poll again; "
                    "it was not cancelled",
                    operation_id=operation_id,
                    result=last,
                )
            try:
                operation = self._send(
                    "operations.get",
                    {"operationId": operation_id},
                    timeout=min(self.timeout, remaining),
                )
            except AnkiError as error:
                error.operation_id = operation_id
                raise
            if operation["operationId"] != operation_id or (
                expected_method is not None and operation["method"] != expected_method
            ):
                raise AnkiError(
                    "operation identity changed", operation_id=operation_id, result=operation
                )
            expected_method = operation["method"]
            last = operation
            if operation["state"] == "succeeded":
                if _has_item_errors(operation["result"]):
                    raise AnkiError(
                        "operation returned item errors",
                        operation_id=operation_id,
                        result=operation,
                    )
                return operation
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

    def iter_pages(
        self, method: str, params: Mapping[str, object] | None = None, *, max_pages: int = 100
    ) -> Iterator[dict[str, object]]:
        """Yield read pages with stable parameters, one session, and a page limit."""
        contract = get_contract()
        meta = contract.method(method)
        schema = contract.params_schema(method)
        if meta.effect != "read" or "cursor" not in schema.get("properties", {}):
            raise AnkiError(f"{method} does not support read pagination")
        if type(max_pages) is not int or max_pages < 1:
            raise AnkiError("max_pages must be a positive integer")
        prepared = copy.deepcopy(dict(params) if params is not None else {})
        contract.validate(method, prepared)
        if "session" not in prepared:
            prepared["session"] = self._send("collection.get", {})["session"]
        cursors = {prepared["cursor"]} if "cursor" in prepared else set()
        for _ in range(max_pages):
            page = cast(dict[str, Any], self.call(method, prepared))
            yield page
            cursor = page["nextCursor"]
            if cursor is None:
                return
            if cursor in cursors:
                raise AnkiError("Anki repeated a consumed cursor", method=method, result=page)
            cursors.add(cursor)
            prepared["cursor"] = cursor
        raise AnkiError("page limit reached; resume with the last page's nextCursor", result=page)

    def create_notes(self, params: dict[str, object], count: int) -> list[str]:
        """Create an atomic batch from prepared source records."""
        result = cast(
            dict[str, Any], self.call("notes.createMany", {**params, "atomic": True}, apply=True)
        )
        items = result["items"]
        if len(items) != count:
            raise AnkiError(
                "incomplete import result; inspect Anki before repeating the import", result=result
            )
        note_ids: dict[int, str] = {}
        for item in items:
            index = item["index"]
            if not 0 <= index < count or index in note_ids:
                raise AnkiError(
                    "unconfirmed import item; inspect Anki before repeating the import",
                    result=result,
                )
            note_ids[index] = item["result"]["noteId"]
        return [note_ids[index] for index in range(count)]
