"""Template sources are compared with live note types before any write."""

import json
from pathlib import Path
from typing import Any

import pytest

from anki_automation.cli import main
from tests.test_anki import collection, rpc_server, success

NAME = "Test · Card"


def error(request_id: int, code: int, kind: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": kind, "data": {"type": kind}},
    }


def write_sources(
    folder: Path, *, front: str = "{{Front}}", extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    source = {
        "name": NAME,
        "kind": "normal",
        "fields": [{"name": "Front", "description": "Question"}, {"name": "Back"}],
        "sortField": "Front",
        "templates": [{"name": "Card", "questionHtml": front, "answerHtml": "{{Back}}"}],
        **(extra or {}),
    }
    (folder / f"{NAME}.json").write_text(json.dumps(source), encoding="utf-8")
    (folder / "replika.css").write_text(".replika {}", encoding="utf-8")
    return source


def field(name: str, description: str = "") -> dict[str, object]:
    return {
        "name": name,
        "fontFamily": "Arial",
        "fontSize": 20,
        "description": description,
        "sticky": False,
        "rtl": False,
        "plainText": False,
        "collapsed": False,
        "excludeFromSearch": False,
    }


def live(
    *,
    front: str = "{{Front}}",
    css: str = ".replika {}",
    names: tuple[str, ...] = ("Front", "Back"),
    description: str = "Question",
) -> dict[str, object]:
    return {
        "id": "7",
        "name": NAME,
        "kind": "normal",
        "fields": [field(name, description if name == "Front" else "") for name in names],
        "templates": [
            {
                "name": "Card",
                "questionHtml": front,
                "answerHtml": "{{Back}}",
                "browserQuestionHtml": "",
                "browserAnswerHtml": "",
                "deck": None,
                "browserFontFamily": "Arial",
                "browserFontSize": 20,
            }
        ],
        "css": css,
        "sortField": "Front",
        "latexHeader": "",
        "latexFooter": "",
        "latexSvg": False,
        "modifiedAt": "2026-11-05T12:00:00Z",
    }


def run(folder: Path, port: int, *extra: str) -> int:
    return main(["templates", str(folder), "--port", str(port), *extra])


def methods(server: Any) -> list[str]:
    return [request["method"] for request in server.requests]


def test_matching_sources_pass_without_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_sources(tmp_path)
    with rpc_server([success(1, collection()), success(2, live())]) as server:
        assert run(tmp_path, server.port) == 0
    assert methods(server) == ["collection.get", "noteTypes.get"]
    assert server.requests[1]["params"]["session"] == "s"
    assert json.loads(capsys.readouterr().out)["note_types"][0]["status"] == "in_sync"


def test_drift_fails_the_check_without_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_sources(tmp_path, front="<b>{{Front}}</b>")
    reply = live(css="", description="Old")
    with rpc_server([success(1, collection()), success(2, reply)]) as server:
        assert run(tmp_path, server.port) == 1
    assert methods(server) == ["collection.get", "noteTypes.get"]
    output = json.loads(capsys.readouterr().out)
    assert output["in_sync"] is False
    assert output["note_types"][0]["differences"] == [
        "field Front: description",
        "template Card: questionHtml",
        "css",
    ]


def test_apply_sends_every_change_in_the_read_session(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_sources(tmp_path, front="<b>{{Front}}</b>")
    updated = live(front="<b>{{Front}}</b>")
    replies = [
        success(1, collection()),
        success(2, live(css="", description="Old")),
        success(3, updated),
        success(4, updated),
    ]
    with rpc_server(replies) as server:
        assert run(tmp_path, server.port, "--apply") == 0
    assert methods(server) == [
        "collection.get",
        "noteTypes.get",
        "noteTypes.editStructure",
        "noteTypes.update",
    ]
    edit, patch = server.requests[2]["params"], server.requests[3]["params"]
    assert edit["edits"] == [
        {"op": "updateField", "name": "Front", "patch": {"description": "Question"}},
        {"op": "updateTemplate", "name": "Card", "patch": {"questionHtml": "<b>{{Front}}</b>"}},
    ]
    assert patch["patch"] == {"css": ".replika {}"}
    assert edit["session"] == patch["session"] == "s"
    output = json.loads(capsys.readouterr().out)
    assert output["in_sync"] is True
    assert output["note_types"][0]["applied"] == ["noteTypes.editStructure", "noteTypes.update"]


def test_apply_creates_a_missing_note_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_sources(tmp_path)
    replies = [success(1, collection()), error(2, 1001, "notFound"), success(3, live())]
    with rpc_server(replies) as server:
        assert run(tmp_path, server.port, "--apply") == 0
    sent = server.requests[2]
    assert sent["method"] == "noteTypes.create"
    assert sent["params"]["css"] == ".replika {}"
    assert sent["params"]["session"] == "s"
    assert sent["params"]["fields"][0] == {"name": "Front", "description": "Question"}
    assert json.loads(capsys.readouterr().out)["note_types"][0]["status"] == "missing"


def test_failure_mid_apply_reports_confirmed_steps(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_sources(tmp_path, front="<b>{{Front}}</b>")
    replies = [
        success(1, collection()),
        success(2, live(css="")),
        success(3, live(front="<b>{{Front}}</b>", css="")),
        error(4, 1003, "editorConflict"),
    ]
    with rpc_server(replies) as server:
        assert run(tmp_path, server.port, "--apply") == 1
    assert methods(server)[-1] == "noteTypes.update"
    details = json.loads(capsys.readouterr().err)
    assert details["code"] == 1003
    assert details["result"]["failed"] == NAME
    reports = details["result"]["applied_before_failure"]
    assert reports[0]["applied"] == ["noteTypes.editStructure"]


def test_structural_drift_blocks_every_write(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_sources(tmp_path, front="<b>{{Front}}</b>")
    replies = [success(1, collection()), success(2, live(names=("Front", "Back", "Extra")))]
    with rpc_server(replies) as server:
        assert run(tmp_path, server.port, "--apply") == 1
    assert methods(server) == ["collection.get", "noteTypes.get"]
    message = capsys.readouterr().err
    assert "nothing was written" in message
    assert "fields" in message


@pytest.mark.parametrize(
    ("extra", "rename", "message"),
    [
        (None, True, "must match the note type name"),
        ({"css": "x"}, False, "css comes from replika.css"),
        ({"session": "s"}, False, "session is not a source key"),
    ],
)
def test_reject_invalid_sources(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extra: dict[str, Any] | None,
    rename: bool,
    message: str,
) -> None:
    write_sources(tmp_path, extra=extra)
    if rename:
        (tmp_path / f"{NAME}.json").rename(tmp_path / "Other.json")
    assert main(["templates", str(tmp_path)]) == 1
    assert message in capsys.readouterr().err
