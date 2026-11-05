"""Check Replika validation and offline output behavior."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from anki_automation.cards import CardError
from anki_automation.cli import main
from anki_automation.replika import MODES, PACKAGE, PREVIEW, stable_key, validate_note
from anki_automation.replika_sources import load_sources

CONTEXT, CLOZE, RECALL = MODES
KEY = stable_key("note-guid")


def test_stable_key_and_valid_different_dictations(tmp_path: Path) -> None:
    assert stable_key("note-guid") == KEY
    assert stable_key("other-guid") != KEY
    for guid, word in (("a", "bus"), ("b", "train")):
        (tmp_path / f"{word}.mp3").write_bytes(b"example")
        validate_note(
            RECALL,
            {
                "Key": stable_key(guid),
                "Prompt": "Write what you hear.",
                "Answer": word,
                "Audio": f"[sound:{word}.mp3]",
            },
            tmp_path,
        )


@pytest.mark.parametrize(
    ("name", "fields", "error"),
    [
        (CONTEXT, {"Key": "", "Prompt": "Say it", "Answer": "Yes"}, "Key must"),
        (CONTEXT, {"Prompt": "Say it", "Answer": "<b> </b>"}, "Answer is required"),
        (CONTEXT, {"Prompt": "Say it", "Answer": "Yes", "Figure": "Picture"}, "image"),
        (CLOZE, {"Text": "I am here"}, "exactly one c1"),
        (CLOZE, {"Text": "{{c1::a}} {{c1::b}}"}, "exactly one c1"),
        (CLOZE, {"Text": "{{c1::::hint}}"}, "contain an answer"),
        (RECALL, {"Prompt": "Say it", "Answer": "Yes<br>No", "Audio": ""}, "one line"),
        (RECALL, {"Prompt": "Say it", "Answer": "Yes", "Audio": "test"}, "one \\[sound:"),
        (RECALL, {"Prompt": "Say it", "Answer": "Yes", "Audio": "[sound:no.mp3]"}, "media"),
        (RECALL, {"Prompt": "Say it", "Answer": "Yes", "Audio": "[sound:../x.mp3]"}, "media"),
        (CONTEXT, {"Prompt": "<script>test</script>", "Answer": "Yes"}, "HTML element"),
        (CONTEXT, {"Prompt": "<b>Unclosed", "Answer": "Yes"}, "matching HTML tags"),
        (CONTEXT, {"Prompt": "</article>", "Answer": "Yes"}, "matching HTML tags"),
        (CONTEXT, {"Prompt": "Say it", "Answer": "<b/>"}, "matching HTML tags"),
        (CONTEXT, {"Prompt": "Say it", "Answer": "Yes", "Example": "[sound:x.mp3]"}, "Audio"),
    ],
)
def test_invalid_fields(name: str, fields: dict[str, str], error: str, tmp_path: Path) -> None:
    with pytest.raises(CardError, match=error):
        validate_note(name, {"Key": KEY, **fields}, tmp_path)


def test_cloze_and_local_figure(tmp_path: Path) -> None:
    validate_note(CLOZE, {"Key": KEY, "Text": "I am {{c1::here::place}}."}, tmp_path)
    (tmp_path / "map.svg").write_text("<svg/>")
    validate_note(
        CONTEXT,
        {
            "Key": KEY,
            "Prompt": "<strong>Read</strong> the map",
            "Answer": "North",
            "Figure": '<img src="map.svg" alt="A map" />',
            "Source": '<a href="https://example.org">Example</a>',
        },
        tmp_path,
    )


def test_cli_publishes_pair_only_after_worker_success(tmp_path: Path) -> None:
    templates = tmp_path / "templates"
    templates.mkdir()
    for name in MODES:
        (templates / f"{name}.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "kind": "normal",
                    "fields": [{"name": "Key"}],
                    "templates": [
                        {"name": "Card", "questionHtml": "{{Key}}", "answerHtml": "{{Key}}"}
                    ],
                    "sortField": "Key",
                }
            ),
        )
    base = tmp_path / "seed.apkg"
    base.touch()
    packages = tmp_path / "runtime"
    (packages / "anki").mkdir(parents=True)
    target = tmp_path / "result"
    args = [
        "replika-build",
        "--base",
        str(base),
        "--templates",
        str(templates),
        "--output-dir",
        str(target),
        "--anki-python",
        sys.executable,
        "--anki-packages",
        str(packages),
    ]

    def worker(command: list[str], *, cwd: Path, **_kwargs: object) -> subprocess.CompletedProcess:
        for filename in (PACKAGE, PREVIEW):
            (cwd / "output" / filename).write_text("worker output")
        return subprocess.CompletedProcess(command, 0, stdout='{"cards": 2}', stderr="")

    with patch("anki_automation.replika.subprocess.run", side_effect=worker):
        assert main(args) == 0
    assert sorted(p.name for p in target.iterdir()) == sorted([PACKAGE, PREVIEW])
    assert main(args) == 1

    fresh = tmp_path / "failed"
    args[args.index(str(target))] = str(fresh)
    with patch(
        "anki_automation.replika.subprocess.run",
        return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="Answer is required"),
    ):
        assert main(args) == 1
    assert not fresh.exists()


@pytest.fixture
def source_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    templates = tmp_path / "templates"
    templates.mkdir()
    for name, mode in MODES.items():
        fields = {
            "context": ["Key", "Prompt", "Answer", "Source", "Figure"],
            "cloze": ["Key", "Text", "Source"],
            "recall": ["Key", "Prompt", "Answer", "Source", "Audio"],
        }[mode]
        (templates / f"{name}.json").write_text(
            json.dumps(
                {
                    "name": name,
                    "kind": "cloze" if mode == "cloze" else "normal",
                    "fields": [{"name": field} for field in fields],
                    "templates": [
                        {"name": "Card", "questionHtml": "{{Key}}", "answerHtml": "{{Key}}"}
                    ],
                    "sortField": "Key",
                }
            ),
            encoding="utf-8",
        )
    decks = tmp_path / "decks.json"
    decks.write_text(
        json.dumps(
            {
                "categories": [
                    {"id": "words", "deck": "English::Words"},
                    {"id": "grammar", "deck": "English::Grammar"},
                    {"id": "slang", "deck": "English::Slang"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return templates, decks, tmp_path


def write_source(path: Path, **overrides: object) -> None:
    record = {
        "key": KEY,
        "type": "context",
        "category": "words",
        "fields": {"Prompt": "Explain the phrase", "Answer": "On time", "Source": "Lesson 1"},
    }
    path.write_text(json.dumps(record | overrides) + "\n", encoding="utf-8")


def test_source_validation_routes_three_types(
    source_inputs: tuple[Path, Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    templates, decks, media = source_inputs
    context, cloze, recall = (media / f"{mode}.jsonl" for mode in MODES.values())
    write_source(context)
    write_source(
        cloze,
        key=stable_key("cloze"),
        type="cloze",
        category="grammar",
        fields={"Text": "I left {{c1::at}} eight.", "Source": "Lesson 1"},
    )
    (media / "line.mp3").write_bytes(b"test media")
    write_source(
        recall,
        key=stable_key("recall"),
        type="recall",
        category="slang",
        fields={
            "Prompt": "Write what you hear.",
            "Answer": "FYI",
            "Audio": "[sound:line.mp3]",
            "Source": "Lesson 1",
        },
    )
    assert (
        main(
            [
                "replika-validate",
                str(context),
                str(cloze),
                str(recall),
                "--templates",
                str(templates),
                "--decks",
                str(decks),
                "--media-dir",
                str(media),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == {
        "cards": 3,
        "decks": ["English::Grammar", "English::Slang", "English::Words"],
    }


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"type": "basic"}, "type must be"),
        ({"category": "vocabulary"}, "category must be"),
        ({"fields": {"Question": "Unexpected"}}, "only fields of the selected type"),
        (
            {"fields": {"Prompt": "Explain", "Answer": "On time", "Source": "<b> </b>"}},
            "Source is required",
        ),
        (
            {
                "fields": {
                    "Prompt": "Read",
                    "Answer": "Map",
                    "Source": "Lesson 1",
                    "Figure": '<img src="missing.png" alt="Map">',
                }
            },
            "invalid package media",
        ),
        ({"tags": ["category::grammar"]}, "invalid tags"),
    ],
)
def test_source_validation_reports_bad_record(
    source_inputs: tuple[Path, Path, Path], overrides: dict[str, object], error: str
) -> None:
    templates, decks, media = source_inputs
    path = media / "bad.jsonl"
    write_source(path, **overrides)
    with pytest.raises(CardError, match=f"bad.jsonl:1: .*{error}"):
        load_sources([path], templates, decks, media)


def test_source_validation_rejects_duplicate_keys_across_files(
    source_inputs: tuple[Path, Path, Path],
) -> None:
    templates, decks, media = source_inputs
    first, second = media / "first.jsonl", media / "second.jsonl"
    write_source(first)
    write_source(second, category="grammar")
    with pytest.raises(CardError, match=r"second\.jsonl:1: duplicate key"):
        load_sources([first, second], templates, decks, media)
