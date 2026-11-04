"""A bounded, local-only Anki-Connect API v1 client."""

import http.client
import json
import math
from dataclasses import dataclass, field

MAX_BODY_BYTES = 16 * 1024 * 1024


class AnkiError(RuntimeError):
    """The transport or API did not confirm a successful result."""


@dataclass(slots=True)
class AnkiClient:
    """Use the IPv4 loopback listener; never proxy, redirect, or retry writes."""

    port: int = 8765
    timeout: float = 10.0
    api_key: str | None = field(default=None, repr=False)
    _request_id: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not 1 <= self.port <= 65535:
            raise AnkiError("port must be between 1 and 65535")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise AnkiError("timeout must be a finite positive number")
        if self.api_key is not None and any(char in self.api_key for char in "\r\n"):
            raise AnkiError("ANKI_CONNECT_KEY must not contain newlines")

    def call(self, method: str, params: dict[str, object] | None = None) -> object:
        """Call one completed method and validate its JSON-RPC envelope."""
        self._request_id += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params or {}},
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if len(body) > MAX_BODY_BYTES:
            raise AnkiError("request exceeds the 16 MiB API limit; split the input file")
        headers = {"Content-Type": "application/json"}
        if self.api_key is not None:
            headers["Authorization"] = f"Bearer {self.api_key}"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout)
        try:
            connection.request("POST", "/api/v1", body=body, headers=headers)
            response = connection.getresponse()
            if response.status != 200:
                raise AnkiError(f"Anki returned HTTP {response.status}")
            raw = response.read(MAX_BODY_BYTES + 1)
            if len(raw) > MAX_BODY_BYTES:
                raise AnkiError("Anki response exceeds the 16 MiB client limit")
        except (OSError, http.client.HTTPException) as error:
            raise AnkiError(
                "Anki transport failed. Check the local listener. If a write was sent, "
                "its outcome is unknown; inspect Anki before running it again."
            ) from error
        finally:
            connection.close()
        try:
            reply = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise AnkiError(
                "Anki returned invalid JSON; inspect Anki before repeating a write"
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
            if not isinstance(error, dict):
                raise AnkiError("Anki returned an invalid error record")
            raise AnkiError(f"Anki API error {error.get('code')}: {error.get('message')}")
        return reply["result"]

    def create_notes(self, params: dict[str, object], count: int) -> list[str]:
        """Pin the active collection session and create one atomic batch."""
        collection = self.call("collection.get")
        if not isinstance(collection, dict) or not isinstance(collection.get("session"), str):
            raise AnkiError("Anki did not return a collection session")
        session = collection["session"]
        if not session:
            raise AnkiError("Anki returned an empty collection session")
        result = self.call("notes.createMany", {**params, "session": session})
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise AnkiError("invalid import result; inspect Anki before repeating the import")
        items = result["items"]
        if len(items) != count:
            raise AnkiError("incomplete import result; inspect Anki before repeating the import")
        note_ids: dict[int, str] = {}
        for item in items:
            if not isinstance(item, dict) or type(item.get("index")) is not int:
                raise AnkiError("invalid import item; inspect Anki before repeating the import")
            index = item["index"]
            created = item.get("result")
            if (
                not 0 <= index < count
                or index in note_ids
                or "error" in item
                or not isinstance(created, dict)
                or not isinstance(created.get("noteId"), str)
                or not created["noteId"].isascii()
                or not created["noteId"].isdecimal()
            ):
                raise AnkiError("unconfirmed import item; inspect Anki before repeating the import")
            note_ids[index] = created["noteId"]
        return [note_ids[index] for index in range(count)]
