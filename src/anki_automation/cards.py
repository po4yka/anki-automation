"""Read source records and render plain text as Anki field HTML."""

import csv
import html
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

NOTE_TYPE = "Replika · Meaning"
FIELDS = ("Front", "Back", "Source")


class CardError(ValueError):
    """Source records do not meet the card contract."""


@dataclass(frozen=True, slots=True)
class Card:
    """A reviewed expression, meaning, and optional supporting text."""

    term: str
    meaning: str
    example: str = ""
    source: str = ""
    tags: tuple[str, ...] = ()

    def fields(self) -> dict[str, str]:
        """Render escaped HTML for the NOTE_TYPE fields."""
        parts = [f"<div>{_html(self.meaning)}</div>"]
        if self.example:
            parts.append(f"<div><i>{_html(self.example)}</i></div>")
        # The wrapper also keeps a leading '#' from becoming a TSV comment.
        return {
            "Front": f"<span>{_html(self.term)}</span>",
            "Back": "".join(parts),
            "Source": _source_html(self.source) if self.source else "",
        }


def _html(value: str) -> str:
    return html.escape(value).replace("\n", "<br>")


def _source_html(source: str) -> str:
    title, _, url = source.rpartition(" — ")
    if re.search(r'[\s\\<>"]', url):
        return _html(source)
    try:
        parsed = urlsplit(url)
    except ValueError:
        return _html(source)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
        return _html(source)
    return f'<a href="{html.escape(url)}">{_html(title or parsed.hostname)}</a>'


def _text(value: object, field: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise CardError(f"{field} must be a string")
    text = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if required and not text:
        raise CardError(f"{field} must not be empty")
    if any(unicodedata.category(char) in {"Cc", "Cs"} and char not in "\n\t" for char in text):
        raise CardError(f"{field} contains an invalid control or surrogate character")
    return text


def card_from_record(record: object) -> Card:
    """Validate one JSON object; reject unknown fields and invalid tags."""
    if not isinstance(record, dict):
        raise CardError("each record must be a JSON object")
    unknown = record.keys() - {"term", "meaning", "example", "source", "tags"}
    if unknown:
        raise CardError(f"unknown fields: {', '.join(sorted(unknown))}")
    tags = record.get("tags", [])
    if not isinstance(tags, list):
        raise CardError("tags must be an array of strings")
    checked_tags: set[str] = set()
    for tag in tags:
        checked = _text(tag, "tag", required=True)
        if not re.fullmatch(r"[\w:-]+", checked):
            raise CardError(
                "tags must contain only letters, digits, underscores, colons, or hyphens"
            )
        checked_tags.add(checked)
    return Card(
        term=_text(record.get("term"), "term", required=True),
        meaning=_text(record.get("meaning"), "meaning", required=True),
        example=_text(record.get("example", ""), "example"),
        source=_text(record.get("source", ""), "source"),
        tags=tuple(sorted(checked_tags)),
    )


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CardError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_cards(path: Path) -> list[Card]:
    """Read UTF-8 JSONL; merge exact repeats and reject conflicting terms."""
    cards: dict[str, Card] = {}
    with path.open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line, object_pairs_hook=_unique_keys)
                card = card_from_record(record)
                previous = cards.get(card.term)
                if previous is not None and previous != card:
                    raise CardError("the same term has conflicting records; combine its meanings")
                cards[card.term] = card
            except (json.JSONDecodeError, CardError) as error:
                raise CardError(f"{path}:{number}: {error}") from error
    if not cards:
        raise CardError(f"{path}: no cards found")
    return list(cards.values())


def check_deck(deck: str) -> str:
    """Keep deck names safe for both Anki references and TSV headers."""
    deck = _text(deck, "deck", required=True)
    if "\n" in deck or "\t" in deck or any(not part.strip() for part in deck.split("::")):
        raise CardError("deck must have non-empty parts and contain no tabs or newlines")
    return deck


def render_tsv(cards: list[Card], deck: str) -> str:
    """Create an Anki text import with explicit headers and a tags column."""
    deck = check_deck(deck)
    output = io.StringIO(newline="")
    output.write(f"#separator:Tab\n#html:true\n#notetype:{NOTE_TYPE}\n#deck:{deck}\n")
    output.write(f"#tags column:{len(FIELDS) + 1}\n#columns:{chr(9).join(FIELDS)}\tTags\n")
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    for card in cards:
        fields = card.fields()
        writer.writerow((*(fields[name] for name in FIELDS), " ".join(card.tags)))
    return output.getvalue()


def import_params(cards: list[Card], deck: str) -> dict[str, object]:
    """Build an atomic API v1 batch without contacting Anki."""
    deck = check_deck(deck)
    return {
        "atomic": True,
        "notes": [
            {
                "deck": {"name": deck},
                "noteType": {"name": NOTE_TYPE},
                "fields": card.fields(),
                "tags": list(card.tags),
                "duplicates": {"allow": False, "scope": "deck", "deck": {"name": deck}},
            }
            for card in cards
        ],
    }
