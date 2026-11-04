"""Test command behavior through files, output streams, and HTTP."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from anki_automation.cli import main
from tests.test_anki import rpc_server, success


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "cards.jsonl"
    path.write_text('{"term":"tea","meaning":"чай"}\n', encoding="utf-8")
    return path


def test_validate(source: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(source)]) == 0
    assert json.loads(capsys.readouterr().out) == {"cards": 1}


def test_export_and_refuse_overwrite(
    source: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "exports" / "cards.tsv"
    args = ["export", str(source), "--deck", "Test", "--output", str(target)]
    assert main(args) == 0
    original = target.read_bytes()
    assert "чай" in original.decode()
    assert b"\r" not in original
    assert json.loads(capsys.readouterr().out)["cards"] == 1
    assert main(args) == 1
    result = capsys.readouterr()
    assert result.out == ""
    assert "error" in json.loads(result.err)
    assert target.read_bytes() == original


def test_preview_has_no_network_or_credentials(
    source: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANKI_CONNECT_KEY", "private-test-value")
    with rpc_server([]) as server:
        assert main(["anki-import", str(source), "--deck", "Test", "--port", str(server.port)]) == 0
        assert server.requests == []
    output = capsys.readouterr().out
    preview = json.loads(output)
    assert preview["apply"] is False
    assert preview["method"] == "notes.createMany"
    assert preview["params"]["atomic"] is True
    assert "private-test-value" not in output


def test_explicit_apply(source: Path, capsys: pytest.CaptureFixture[str]) -> None:
    replies = [
        success(1, {"session": "s"}),
        success(2, {"items": [{"index": 0, "result": {"noteId": "123", "cardIds": ["456"]}}]}),
    ]
    with rpc_server(replies) as server:
        assert (
            main(
                [
                    "anki-import",
                    str(source),
                    "--deck",
                    "Test",
                    "--port",
                    str(server.port),
                    "--apply",
                ]
            )
            == 0
        )
    assert json.loads(capsys.readouterr().out) == {"created": 1, "note_ids": ["123"]}


def test_import_api_failure_is_nonzero(source: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reply = {"jsonrpc": "2.0", "id": 1, "error": {"code": 1004, "message": "No collection."}}
    with rpc_server([reply]) as server:
        assert (
            main(
                [
                    "anki-import",
                    str(source),
                    "--deck",
                    "Test",
                    "--port",
                    str(server.port),
                    "--apply",
                ]
            )
            == 1
        )
    result = capsys.readouterr()
    assert result.out == ""
    assert "1004" in json.loads(result.err)["error"]


def test_invalid_input_does_not_create_export(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "bad.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    target = tmp_path / "output.tsv"
    assert main(["export", str(source), "--deck", "Test", "--output", str(target)]) == 1
    assert not target.exists()
    assert "bad.jsonl:1:" in json.loads(capsys.readouterr().err)["error"]


def test_missing_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate", str(tmp_path / "absent.jsonl")]) == 1
    assert "error" in json.loads(capsys.readouterr().err)


def test_non_utf8_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "bad.jsonl"
    source.write_bytes(b"\xff")
    assert main(["validate", str(source)]) == 1
    assert "error" in json.loads(capsys.readouterr().err)


def test_module_entry_point() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "anki_automation", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.1.0"


def test_help(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        main(["--help"])
    assert "anki-import" in capsys.readouterr().out
