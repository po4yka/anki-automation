"""Source validation and export behavior."""

import csv
import io
import json
from pathlib import Path

import pytest

from anki_automation.cards import (
    NOTE_TYPE,
    CardError,
    card_from_record,
    check_deck,
    import_params,
    load_cards,
    render_tsv,
)


def test_normalize_text_and_tags() -> None:
    card = card_from_record(
        {
            "term": " cafe\u0301 ",
            "meaning": " кафе ",
            "example": "First\r\nsecond\rthird",
            "tags": ["vocabulary", "lang::en", "vocabulary"],
        }
    )
    assert card.term == "café"
    assert card.meaning == "кафе"
    assert card.example == "First\nsecond\nthird"
    assert card.source == ""
    assert card.tags == ("lang::en", "vocabulary")


@pytest.mark.parametrize(
    "record, message",
    [
        ([], "JSON object"),
        ({"term": "a", "meaning": "b", "typo": "c"}, "unknown fields"),
        ({"meaning": "b"}, "term must be a string"),
        ({"term": "a"}, "meaning must be a string"),
        ({"term": "  ", "meaning": "b"}, "term must not be empty"),
        ({"term": "a", "meaning": "  "}, "meaning must not be empty"),
        ({"term": 3, "meaning": "b"}, "term must be a string"),
        ({"term": "a", "meaning": "b", "source": None}, "source must be a string"),
        ({"term": "a", "meaning": "b", "example": []}, "example must be a string"),
        ({"term": "a\x00b", "meaning": "b"}, "invalid control"),
        ({"term": "a\ud800b", "meaning": "b"}, "surrogate"),
        ({"term": "a", "meaning": "b", "tags": "tag"}, "array of strings"),
        ({"term": "a", "meaning": "b", "tags": [None]}, "tag must be a string"),
        ({"term": "a", "meaning": "b", "tags": [""]}, "tag must not be empty"),
        ({"term": "a", "meaning": "b", "tags": ["two words"]}, "tags must contain"),
        ({"term": "a", "meaning": "b", "tags": ["<tag>"]}, "tags must contain"),
    ],
)
def test_reject_invalid_records(record: object, message: str) -> None:
    with pytest.raises(CardError, match=message):
        card_from_record(record)


def test_jsonl_merge_exact_repeats_and_preserve_order(tmp_path: Path) -> None:
    path = tmp_path / "cards.jsonl"
    path.write_text(
        '\ufeff\n{"term":"café","meaning":"кафе"}\n'
        '{"term":"cafe\\u0301","meaning":"кафе"}\n'
        '{"term":"tea","meaning":"чай"}\n',
        encoding="utf-8",
    )
    assert [card.term for card in load_cards(path)] == ["café", "tea"]


@pytest.mark.parametrize(
    "content, message",
    [
        (" \n\n", "no cards found"),
        ("{bad}\n", r"cards.jsonl:1:"),
        ('{"term":"a","term":"b","meaning":"c"}', "duplicate JSON key"),
        (
            '{"term":"a","meaning":"b"}\n{"term":"a","meaning":"c"}',
            r"cards.jsonl:2:.*conflicting",
        ),
        ('{"term":"a","meaning":"b","unknown":1}', r"cards.jsonl:1:.*unknown"),
    ],
)
def test_jsonl_error_location(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "cards.jsonl"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(CardError, match=message):
        load_cards(path)


def test_escape_source_text_and_preserve_line_breaks() -> None:
    card = card_from_record(
        {
            "term": '#<script>alert("x")</script>',
            "meaning": "one & two\nthree\tfour",
            "example": "<img src=x onerror=alert(1)>",
            "source": "<https://example.org>",
            "tags": ["lang::en"],
        }
    )
    text = render_tsv([card], "Languages::English")
    assert text.startswith(
        "#separator:Tab\n#html:true\n#notetype:Replika · Meaning\n#deck:Languages::English\n"
    )
    assert "#tags column:4\n#columns:Front\tBack\tSource\tTags\n" in text
    rows = list(csv.reader(io.StringIO("\n".join(text.splitlines()[6:])), delimiter="\t"))
    fields = card.fields()
    assert rows == [[fields["Front"], fields["Back"], fields["Source"], "lang::en"]]
    assert rows[0][0].startswith("<span>#&lt;script&gt;")
    assert "&amp; two<br>three" in rows[0][1]
    assert "<script>" not in text
    assert "<img " not in text
    assert rows[0][2] == "&lt;https://example.org&gt;"
    assert "Source:" not in rows[0][1]


@pytest.mark.parametrize(
    "source, expected",
    [
        (
            "Strength & Fitness — https://example.org/guide?a=1&b=2",
            '<a href="https://example.org/guide?a=1&amp;b=2">Strength &amp; Fitness</a>',
        ),
        ("https://example.org/guide", '<a href="https://example.org/guide">example.org</a>'),
        ("Local lesson", "Local lesson"),
        ("Unsafe — javascript:alert(1)", "Unsafe — javascript:alert(1)"),
        ("Malformed — https://[host", "Malformed — https://[host"),
        (
            'Injected — https://example.org/"onclick="alert(1)',
            "Injected — https://example.org/&quot;onclick=&quot;alert(1)",
        ),
    ],
)
def test_render_source_links(source: str, expected: str) -> None:
    card = card_from_record({"term": "squat", "meaning": "приседание", "source": source})
    assert card.fields() == {
        "Front": "<span>squat</span>",
        "Back": "<div>приседание</div>",
        "Source": expected,
    }


def test_source_field_is_empty_without_source() -> None:
    card = card_from_record({"term": "squat", "meaning": "приседание", "example": "Squat low."})
    assert card.fields() == {
        "Front": "<span>squat</span>",
        "Back": "<div>приседание</div><div><i>Squat low.</i></div>",
        "Source": "",
    }


@pytest.mark.parametrize("deck", ["", " ", "a\nb", "a\tb", "a::", "::a", "a:: ::b"])
def test_reject_invalid_decks(deck: str) -> None:
    with pytest.raises(CardError):
        check_deck(deck)


def test_import_and_export_use_identical_fields() -> None:
    card = card_from_record({"term": "cat", "meaning": "кот"})
    params = import_params([card], " Languages::English ")
    assert NOTE_TYPE == "Replika · Meaning"
    assert params == {
        "atomic": True,
        "notes": [
            {
                "deck": {"name": "Languages::English"},
                "noteType": {"name": NOTE_TYPE},
                "fields": card.fields(),
                "tags": [],
                "duplicates": {
                    "allow": False,
                    "scope": "deck",
                    "deck": {"name": "Languages::English"},
                },
            }
        ],
    }
    assert json.loads(json.dumps(params)) == params
