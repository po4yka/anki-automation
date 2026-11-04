"""Exercise API v1 over a real local HTTP transport, without an Anki collection."""

import json
import threading
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anki_automation.anki import AnkiClient, AnkiError
from anki_automation.cards import card_from_record, import_params


@dataclass
class Server:
    port: int
    requests: list[dict[str, object]]
    authorizations: list[str | None]
    paths: list[str]


@contextmanager
def rpc_server(replies: Sequence[object], *, status: int = 200) -> Generator[Server]:
    requests: list[dict[str, object]] = []
    authorizations: list[str | None] = []
    paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            data = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append(json.loads(data))
            authorizations.append(self.headers.get("Authorization"))
            paths.append(self.path)
            reply = replies[len(requests) - 1]
            raw = reply if isinstance(reply, bytes) else json.dumps(reply).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            yield Server(httpd.server_port, requests, authorizations, paths)
        finally:
            httpd.shutdown()
            thread.join(timeout=5)


def success(request_id: int, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def test_atomic_import_pins_session_and_orders_results() -> None:
    card = card_from_record({"term": "café", "meaning": "кафе"})
    params = import_params([card, card], "Languages::English")
    replies = [
        success(1, {"session": "collection-session"}),
        success(
            2,
            {
                "items": [
                    {"index": 1, "result": {"noteId": "102", "cardIds": ["202"]}},
                    {"index": 0, "result": {"noteId": "101", "cardIds": ["201"]}},
                ]
            },
        ),
    ]
    with rpc_server(replies) as server:
        client = AnkiClient(port=server.port, api_key="test-auth-value")
        assert client.create_notes(params, 2) == ["101", "102"]
        assert server.requests == [
            {"jsonrpc": "2.0", "id": 1, "method": "collection.get", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "notes.createMany",
                "params": {**params, "session": "collection-session"},
            },
        ]
        assert server.paths == ["/api/v1", "/api/v1"]
        assert server.authorizations == ["Bearer test-auth-value"] * 2
        assert "test-auth-value" not in repr(client)


@pytest.mark.parametrize(
    "reply",
    [
        b"not-json",
        b"\xff",
        [],
        {"jsonrpc": "1.0", "id": 1, "result": {}},
        {"jsonrpc": "2.0", "id": 2, "result": {}},
        {"jsonrpc": "2.0", "id": True, "result": {}},
        {"jsonrpc": "2.0", "id": 1},
        {"jsonrpc": "2.0", "id": 1, "result": {}, "error": {}},
        {"jsonrpc": "2.0", "id": 1, "error": "broken"},
    ],
)
def test_reject_invalid_envelopes_without_retry(reply: object) -> None:
    with rpc_server([reply]) as server:
        with pytest.raises(AnkiError):
            AnkiClient(port=server.port).call("api.describe")
        assert len(server.requests) == 1


def test_report_structured_api_error_without_retry() -> None:
    reply = {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": 1002, "message": "Duplicate note.", "data": {"type": "validationFailed"}},
    }
    with rpc_server([reply]) as server:
        with pytest.raises(AnkiError, match="1002: Duplicate note"):
            AnkiClient(port=server.port).call("notes.createMany", {"atomic": True, "notes": []})
        assert len(server.requests) == 1


@pytest.mark.parametrize("status", [301, 401, 403, 413, 500])
def test_http_status_is_checked_before_json(status: int) -> None:
    with rpc_server([b""], status=status) as server:
        with pytest.raises(AnkiError, match=f"HTTP {status}"):
            AnkiClient(port=server.port).call("collection.get")
        assert len(server.requests) == 1


@pytest.mark.parametrize("collection", [None, {}, {"session": 1}, {"session": ""}])
def test_invalid_session_stops_before_write(collection: object) -> None:
    with rpc_server([success(1, collection)]) as server:
        with pytest.raises(AnkiError, match="session"):
            AnkiClient(port=server.port).create_notes({}, 1)
        assert len(server.requests) == 1


@pytest.mark.parametrize(
    "result",
    [
        None,
        {},
        {"items": []},
        {"items": [None]},
        {"items": [{"index": True, "result": {"noteId": "1"}}]},
        {"items": [{"index": 1, "result": {"noteId": "1"}}]},
        {"items": [{"index": 0, "error": {"code": 1002}}]},
        {"items": [{"index": 0, "result": {"noteId": 1}}]},
        {"items": [{"index": 0, "result": {"noteId": ""}}]},
        {"items": [{"index": 0, "result": {"noteId": "\uff11\uff12\uff13"}}]},
    ],
)
def test_unconfirmed_import_does_not_report_success(result: object) -> None:
    with rpc_server([success(1, {"session": "s"}), success(2, result)]) as server:
        with pytest.raises(AnkiError, match="inspect Anki"):
            AnkiClient(port=server.port).create_notes({}, 1)
        assert len(server.requests) == 2


def test_reject_repeated_indexes() -> None:
    item = {"index": 0, "result": {"noteId": "1"}}
    with (
        rpc_server([success(1, {"session": "s"}), success(2, {"items": [item, item]})]) as server,
        pytest.raises(AnkiError, match="unconfirmed import item"),
    ):
        AnkiClient(port=server.port).create_notes({}, 2)


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_reject_invalid_port(port: int) -> None:
    with pytest.raises(AnkiError, match="port"):
        AnkiClient(port=port)


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_reject_invalid_timeout(timeout: float) -> None:
    with pytest.raises(AnkiError, match="timeout"):
        AnkiClient(timeout=timeout)


def test_reject_header_injection() -> None:
    with pytest.raises(AnkiError, match="newlines"):
        AnkiClient(api_key="bad\r\nInjected: value")


def test_request_and_response_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("anki_automation.anki.MAX_BODY_BYTES", 150)
    with pytest.raises(AnkiError, match="request exceeds"):
        AnkiClient().call("notes.createMany", {"large": "x" * 200})
    with (
        rpc_server([b"x" * 151]) as server,
        pytest.raises(AnkiError, match="response exceeds"),
    ):
        AnkiClient(port=server.port).call("collection.get")


def test_closed_listener_is_a_transport_error() -> None:
    with rpc_server([]) as server:
        port = server.port
    with pytest.raises(AnkiError, match="outcome is unknown"):
        AnkiClient(port=port, timeout=0.1).call("notes.createMany")
