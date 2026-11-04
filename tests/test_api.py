"""Use a local protocol server to exercise dispatch, effects, and completion."""

import copy
import io
import json
from pathlib import Path

import pytest

from anki_automation.anki import AnkiClient
from anki_automation.cli import main
from anki_automation.contract import ContractError, get_contract
from anki_automation.errors import AnkiError, OperationTimeout
from tests.test_anki import collection, rpc_server, success

REQUESTS = json.loads((Path(__file__).parent / "fixtures/anki-v1-requests.json").read_text())


def operation(state: str, *, method: str = "collection.sync", **extra: object) -> dict[str, object]:
    return {
        "operationId": "op-1",
        "method": method,
        "session": "s",
        "createdAt": "2026-11-04T10:00:00Z",
        "updatedAt": "2026-11-04T10:00:01Z",
        "cancellable": False,
        "progress": None,
        "state": state,
        **extra,
    }


@pytest.mark.parametrize(
    "method", [m.name for m in get_contract().methods.values() if m.requires_apply]
)
def test_every_effect_requires_explicit_apply_without_http(method: str) -> None:
    with rpc_server([]) as server:
        with pytest.raises(AnkiError, match="apply=True"):
            AnkiClient(port=server.port).call(method, REQUESTS[method])
        assert not server.requests


def test_read_and_write_forward_real_parameters() -> None:
    replies = [
        success(1, []),
        success(2, collection()),
        success(3, {"id": "2", "name": "Language", "kind": "normal", "presetId": "1"}),
    ]
    with rpc_server(replies) as server:
        client = AnkiClient(port=server.port)
        assert client.call("decks.list") == []
        created = client.call("decks.create", {"name": "Language"}, apply=True)
        assert isinstance(created, dict)
        assert created["id"] == "2"
        assert server.requests[2]["params"] == {"name": "Language", "session": "s"}


def test_batch_pins_one_session_without_mutating_the_callers_input() -> None:
    params = {
        "commands": [
            {"method": "decks.list"},
            {"method": "decks.create", "params": {"name": "Test"}},
        ]
    }
    original = copy.deepcopy(params)
    handle = {"operationId": "op-1", "method": "api.execute", "session": "s"}
    with rpc_server([success(1, collection()), success(2, handle)]) as server:
        assert AnkiClient(port=server.port).call("api.execute", params, apply=True) == handle
        commands = server.requests[1]["params"]["commands"]
        assert all(command["params"]["session"] == "s" for command in commands)
    assert params == original


def test_mixed_batch_sessions_are_rejected_before_http() -> None:
    params = {
        "commands": [
            {"method": "decks.list", "params": {"session": session}} for session in ("a", "b")
        ]
    }
    with rpc_server([]) as server:
        with pytest.raises(AnkiError, match="same collection session"):
            AnkiClient(port=server.port).call("api.execute", params, apply=True)
        assert not server.requests


def test_explicit_batch_session_is_used_for_missing_sessions() -> None:
    params = {
        "commands": [{"method": "decks.list", "params": {"session": "s"}}, {"method": "tags.list"}]
    }
    handle = {"operationId": "op-1", "method": "api.execute", "session": "s"}
    with rpc_server([success(1, handle)]) as server:
        AnkiClient(port=server.port).call("api.execute", params, apply=True)
        assert len(server.requests) == 1
        assert server.requests[0]["params"]["commands"][1]["params"] == {"session": "s"}


def test_apply_and_wait_returns_final_record() -> None:
    handle = {"operationId": "op-1", "method": "collection.sync", "session": "s"}
    final = operation("succeeded", result={"collectionComplete": True, "mediaComplete": True})
    replies = [
        success(1, collection()),
        success(2, handle),
        success(3, operation("waitingForUser")),
        success(4, final),
    ]
    with rpc_server(replies) as server:
        result = AnkiClient(port=server.port).call(
            "collection.sync", apply=True, wait=True, poll_interval=0.001
        )
        assert result == final
        assert [r["method"] for r in server.requests] == [
            "collection.get",
            "collection.sync",
            "operations.get",
            "operations.get",
        ]


@pytest.mark.parametrize("state", ["failed", "cancelled"])
def test_failed_operations_keep_structured_errors_and_id(state: str) -> None:
    record = operation(
        state, error={"code": 1007, "message": "Cancelled.", "data": {"type": "cancelled"}}
    )
    with rpc_server([success(1, record)]) as server:
        with pytest.raises(AnkiError) as caught:
            AnkiClient(port=server.port).wait_for_operation("op-1")
        assert caught.value.code == 1007
        assert caught.value.operation_id == "op-1"
        assert caught.value.result == record
        assert len(server.requests) == 1


def test_operation_timeout_retains_handle_and_does_not_cancel() -> None:
    pending = operation("running")
    with rpc_server([success(1, pending)]) as server:
        with pytest.raises(OperationTimeout) as caught:
            AnkiClient(port=server.port).wait_for_operation(
                "op-1", wait_timeout=0.02, poll_interval=1
            )
        assert caught.value.operation_id == "op-1"
        assert caught.value.result == pending
        assert [request["method"] for request in server.requests] == ["operations.get"]


def test_operation_identity_must_stay_stable() -> None:
    record = operation("running")
    record["operationId"] = "other-operation"
    with (
        rpc_server([success(1, record)]) as server,
        pytest.raises(AnkiError, match="identity changed"),
    ):
        AnkiClient(port=server.port).wait_for_operation("op-1")


def test_poll_transport_failure_retains_operation_id() -> None:
    with rpc_server([b""], status=500) as server:
        with pytest.raises(AnkiError) as caught:
            AnkiClient(port=server.port).wait_for_operation("op-1")
        assert caught.value.operation_id == "op-1"


def test_bulk_errors_are_failures_with_all_results_retained() -> None:
    result = {
        "items": [
            {"index": 0, "result": {"changed": True}},
            {
                "index": 1,
                "error": {"code": 1001, "message": "Not found.", "data": {"type": "notFound"}},
            },
        ]
    }
    with rpc_server([success(1, result)]) as server:
        with pytest.raises(AnkiError, match="item errors") as caught:
            AnkiClient(port=server.port).call(
                "cards.setSuspended",
                {"ids": ["1", "2"], "suspended": True, "session": "s"},
                apply=True,
            )
        assert caught.value.result == result
        assert len(server.requests) == 1


def test_ordered_operation_exposes_nested_bulk_failures() -> None:
    error = {"code": 1001, "message": "Not found.", "data": {"type": "notFound"}}
    record = operation(
        "succeeded",
        method="api.execute",
        result={
            "items": [
                {
                    "index": 0,
                    "method": "notes.delete",
                    "result": {"items": [{"index": 0, "error": error}]},
                }
            ]
        },
    )
    with rpc_server([success(1, record)]) as server:
        with pytest.raises(AnkiError, match="item errors") as caught:
            AnkiClient(port=server.port).call("operations.get", {"operationId": "op-1"})
        assert caught.value.result == record


def test_pages_keep_parameters_session_and_consumed_cursors() -> None:
    replies = [
        success(1, collection()),
        success(2, {"ids": ["1"], "nextCursor": "cursor-1"}),
        success(3, {"ids": ["2"], "nextCursor": None}),
    ]
    params = {"query": "deck:Test", "limit": 1}
    with rpc_server(replies) as server:
        pages = list(AnkiClient(port=server.port).iter_pages("notes.search", params))
        assert [page["ids"] for page in pages] == [["1"], ["2"]]
        assert server.requests[1]["params"] == {**params, "session": "s"}
        assert server.requests[2]["params"] == {**params, "session": "s", "cursor": "cursor-1"}
    assert params == {"query": "deck:Test", "limit": 1}


@pytest.mark.parametrize("max_pages", [0, -1, True])
def test_page_limits_must_be_positive_integers(max_pages: int) -> None:
    with pytest.raises(AnkiError, match="max_pages"):
        list(AnkiClient().iter_pages("notes.search", {"query": ""}, max_pages=max_pages))


def test_page_limit_retains_resume_cursor() -> None:
    page = {"ids": ["1"], "nextCursor": "next"}
    with rpc_server([success(1, page)]) as server:
        with pytest.raises(AnkiError, match="page limit reached") as caught:
            list(
                AnkiClient(port=server.port).iter_pages(
                    "notes.search", {"query": "", "session": "s"}, max_pages=1
                )
            )
        assert caught.value.result == page


def test_repeated_cursor_fails_without_reusing_it() -> None:
    page = {"ids": ["1"], "nextCursor": "consumed"}
    with rpc_server([success(1, page)]) as server:
        with pytest.raises(AnkiError, match="consumed cursor"):
            list(
                AnkiClient(port=server.port).iter_pages(
                    "notes.search", {"query": "", "session": "s", "cursor": "consumed"}
                )
            )
        assert len(server.requests) == 1


def test_unsupported_wait_and_pagination_fail_before_http() -> None:
    with pytest.raises(AnkiError, match="does not return"):
        AnkiClient().call("decks.list", wait=True)
    with pytest.raises(AnkiError, match="does not support"):
        list(AnkiClient().iter_pages("decks.list"))
    with pytest.raises(ContractError, match="unknown API"):
        AnkiClient().call("unknown")


def test_cli_method_flags_and_false_booleans(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["anki", "cards.setSuspended", "--ids", '["1"]', "--no-suspended"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["params"] == {"ids": ["1"], "suspended": False}
    assert output["apply"] is False


def test_cli_json_file_stdin_and_duplicate_parameters(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "params.json"
    path.write_text('{"name":"Test"}')
    assert main(["anki", "call", "decks.create", "--params-file", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["params"] == {"name": "Test"}
    monkeypatch.setattr("sys.stdin", io.StringIO('{"name":"Other"}'))
    assert main(["anki", "decks.create", "--params-file", "-"]) == 0
    assert json.loads(capsys.readouterr().out)["params"] == {"name": "Other"}
    assert main(["anki", "decks.create", "--params", '{"name":"Test"}', "--name", "Other"]) == 1
    assert "supplied twice" in json.loads(capsys.readouterr().err)["error"]


@pytest.mark.parametrize("value", ["null", "[]", '"text"'])
def test_cli_parameter_root_must_be_an_object(
    value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["anki", "decks.list", "--params", value]) == 1
    assert "JSON object" in json.loads(capsys.readouterr().err)["error"]


def test_cli_reads_execute_but_writes_preview_without_http(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with rpc_server([success(1, [])]) as server:
        assert main(["anki", "decks.list", "--port", str(server.port)]) == 0
        assert json.loads(capsys.readouterr().out) == []
        assert main(["anki", "decks.create", "--name", "Test", "--port", str(server.port)]) == 0
        assert json.loads(capsys.readouterr().out)["apply"] is False
        assert len(server.requests) == 1


def test_cli_wait_timeout_has_distinct_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    with rpc_server([success(1, operation("running"))]) as server:
        assert (
            main(
                [
                    "anki",
                    "wait",
                    "op-1",
                    "--port",
                    str(server.port),
                    "--wait-timeout",
                    "0.02",
                    "--poll-interval",
                    "1",
                ]
            )
            == 3
        )
        assert json.loads(capsys.readouterr().err)["operation_id"] == "op-1"


def test_cli_incomplete_pagination_keeps_received_pages(capsys: pytest.CaptureFixture[str]) -> None:
    page = {"ids": ["1"], "nextCursor": "resume"}
    with rpc_server([success(1, page)]) as server:
        assert (
            main(
                [
                    "anki",
                    "notes.search",
                    "--query",
                    "",
                    "--session",
                    "s",
                    "--all-pages",
                    "--max-pages",
                    "1",
                    "--port",
                    str(server.port),
                ]
            )
            == 1
        )
        output = json.loads(capsys.readouterr().err)
        assert output["result"]["pages"] == [page]
        assert output["result"]["last_result"]["nextCursor"] == "resume"


def test_cli_catalog_schema_and_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["anki", "methods"]) == 0
    assert len(json.loads(capsys.readouterr().out)["methods"]) == 96
    assert main(["anki", "methods", "--group", "media"]) == 0
    assert len(json.loads(capsys.readouterr().out)["methods"]) == 5
    assert main(["anki", "schema", "notes.create"]) == 0
    assert json.loads(capsys.readouterr().out)["effect"] == "write"
    assert main(["anki", "schema"]) == 0
    assert "x-methods" in json.loads(capsys.readouterr().out)
    with pytest.raises(SystemExit, match="0"):
        main(["anki", "notes.create", "--help"])
    assert "--note-type" in capsys.readouterr().out


@pytest.mark.parametrize(
    "args",
    [
        ["decks.list", "--wait"],
        ["notes.search", "--query", "", "--wait", "--all-pages"],
        ["decks.list", "--all-pages"],
    ],
)
def test_cli_invalid_modes_fail_before_http(
    args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["anki", *args]) == 1
    assert "error" in json.loads(capsys.readouterr().err)


def test_cli_invalid_json_is_an_argument_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="2"):
        main(["anki", "decks.list", "--params", '{"a":NaN}'])
    assert "non-finite" in capsys.readouterr().err


def test_cli_live_catalog_reports_missing_and_unknown_methods(
    capsys: pytest.CaptureFixture[str],
) -> None:
    description = {
        "apiMajor": 1,
        "ankiVersion": "26.09.3",
        "schema": {},
        "methods": [
            {"name": "decks.list", "available": True, "reason": None},
            {"name": "decks.create", "available": False, "reason": "read-only runtime"},
            {"name": "future.method", "available": True, "reason": None},
        ],
        "limits": get_contract().schema["x-limits"],
    }
    with rpc_server([success(1, description)]) as server:
        assert (
            main(["anki", "methods", "--live", "--group", "decks", "--port", str(server.port)]) == 0
        )
        output = json.loads(capsys.readouterr().out)
        methods = {method["name"]: method for method in output["methods"]}
        assert methods["decks.list"]["available"] is True
        assert methods["decks.create"]["available"] is False
        assert methods["decks.create"]["reason"] == "read-only runtime"
        assert methods["decks.delete"]["available"] is False
        assert output["runtime"]["unknown_methods"] == ["future.method"]
        assert "future.method" not in get_contract().methods
        assert len(server.requests) == 1


def test_cli_apply_writes_once_and_all_pages_returns_results(
    capsys: pytest.CaptureFixture[str],
) -> None:
    deck = {"id": "2", "name": "Test", "kind": "normal", "presetId": "1"}
    page = {"ids": ["1"], "nextCursor": None}
    replies = [success(1, collection()), success(2, deck)]
    with rpc_server(replies) as server:
        assert (
            main(["anki", "decks.create", "--name", "Test", "--apply", "--port", str(server.port)])
            == 0
        )
        assert json.loads(capsys.readouterr().out) == deck
        assert [request["method"] for request in server.requests] == [
            "collection.get",
            "decks.create",
        ]
    with rpc_server([success(1, collection()), success(2, page)]) as server:
        assert (
            main(
                [
                    "anki",
                    "notes.search",
                    "--query",
                    "",
                    "--limit",
                    "1",
                    "--all-pages",
                    "--port",
                    str(server.port),
                ]
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out) == {"pages": [page]}
        assert server.requests[1]["params"]["limit"] == 1


def test_interrupted_wait_keeps_accepted_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("anki_automation.anki.time.sleep", interrupt)
    handle = {"operationId": "op-1", "method": "collection.sync", "session": "s"}
    replies = [success(1, collection()), success(2, handle), success(3, operation("running"))]
    with rpc_server(replies) as server:
        with pytest.raises(AnkiError, match="interrupted") as caught:
            AnkiClient(port=server.port).call("collection.sync", apply=True, wait=True)
        assert caught.value.operation_id == "op-1"
        assert caught.value.result == handle
        assert [request["method"] for request in server.requests].count("collection.sync") == 1
