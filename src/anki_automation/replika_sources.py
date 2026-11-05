"""Read Obsidian Replika source records without opening an Anki collection."""

import re
from dataclasses import dataclass
from pathlib import Path

from anki_automation.cards import CardError, check_deck
from anki_automation.contract import decode_json, get_contract
from anki_automation.replika import MODES, SOUND, FieldHTML, validate_note

CATEGORIES = {"words", "grammar", "slang"}


@dataclass(frozen=True, slots=True)
class SourceNote:
    """A validated note and its target deck."""

    note_type: str
    deck: str
    fields: dict[str, str]
    tags: tuple[str, ...]
    media: tuple[str, ...]


def load_definitions(templates: Path) -> dict[str, set[str]]:
    """Read the same field names used by the package builder."""
    result = {}
    for name, mode in MODES.items():
        path = templates / f"{name}.json"
        data = decode_json(path.read_text(encoding="utf-8"))
        get_contract().validate("noteTypes.create", data)
        if not isinstance(data, dict):
            raise CardError(f"{path}: expected an object")
        fields = [item["name"] for item in data["fields"]]
        required = {"Text", "Source"} if mode == "cloze" else {"Prompt", "Answer", "Source"}
        if mode == "recall":
            required.add("Audio")
        if (
            data["name"] != name
            or data.get("kind", "normal") != ("cloze" if mode == "cloze" else "normal")
            or fields[0] != "Key"
            or len(fields) != len(set(fields))
            or not required.issubset(fields)
        ):
            raise CardError(f"{path}: invalid Replika type or fields")
        result[name] = set(fields) - {"Key"}
    return result


def load_decks(path: Path) -> dict[str, str]:
    """Read the Obsidian deck map and reject ambiguous routes."""
    data = decode_json(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("categories"), list):
        raise CardError(f"{path}: expected a categories list")
    routes: dict[str, str] = {}
    for route in data["categories"]:
        if not isinstance(route, dict) or not isinstance(route.get("id"), str):
            raise CardError(f"{path}: invalid category")
        category = route["id"]
        if category not in CATEGORIES or category in routes:
            raise CardError(f"{path}: unexpected or repeated category")
        if not isinstance(route.get("deck"), str):
            raise CardError(f"{path}: deck must be a string")
        routes[category] = check_deck(route["deck"])
    names = list(routes.values())
    if set(routes) != CATEGORIES or len({name.casefold() for name in names}) != len(names):
        raise CardError(f"{path}: map all three categories to distinct decks")
    return routes


def source_note(
    record: object, definitions: dict[str, set[str]], routes: dict[str, str], media: Path
) -> SourceNote:
    """Resolve a record to native fields without discarding unknown input."""
    if not isinstance(record, dict) or record.keys() - {
        "key",
        "type",
        "category",
        "fields",
        "tags",
    }:
        raise CardError("expected an object with key, type, category, fields, and optional tags")
    name = next((name for name, mode in MODES.items() if mode == record.get("type")), None)
    if name is None:
        raise CardError("type must be context, cloze, or recall")
    category = record.get("category")
    if not isinstance(category, str) or category not in routes:
        raise CardError("category must be words, grammar, or slang")
    key = record.get("key")
    if not isinstance(key, str):
        raise CardError("key must be a string")
    raw = record.get("fields")
    if not isinstance(raw, dict) or raw.keys() - definitions[name]:
        raise CardError("fields must contain only fields of the selected type")
    fields = {"Key": key}
    for field in sorted(definitions[name]):
        value = raw.get(field, "")
        if not isinstance(value, str) or re.search(
            r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]", value
        ):
            raise CardError(f"{field}: expected text without control characters")
        fields[field] = value.strip()
    validate_note(name, fields, media, require_source=True)
    tags = record.get("tags", [])
    if not isinstance(tags, list) or any(
        not isinstance(tag, str)
        or not re.fullmatch(r"[\w:-]+", tag)
        or tag.casefold().startswith(("category::", "lang::"))
        for tag in tags
    ):
        raise CardError("invalid tags; language and category tags are set by the importer")
    references: set[str] = set()
    sound = SOUND.fullmatch(fields.get("Audio", ""))
    if sound:
        references.add(sound[1])
    figure = FieldHTML("Figure", media)
    figure.feed(fields.get("Figure", ""))
    figure.close()
    references.update(image["src"] or "" for image in figure.images)
    unique_tags = {tag.casefold(): tag for tag in reversed(tags)}
    return SourceNote(
        name,
        routes[category],
        fields,
        tuple(sorted({*unique_tags.values(), "lang::en", f"category::{category}"})),
        tuple(sorted(references)),
    )


def load_sources(paths: list[Path], templates: Path, decks: Path, media: Path) -> list[SourceNote]:
    """Validate all records and reject repeated keys across input files."""
    definitions = load_definitions(templates)
    routes = load_decks(decks)
    result = []
    keys: set[str] = set()
    for path in paths:
        with path.open(encoding="utf-8-sig") as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    note = source_note(decode_json(line), definitions, routes, media)
                    key = note.fields["Key"]
                    if key in keys:
                        raise CardError("duplicate key")
                    keys.add(key)
                    result.append(note)
                except ValueError as error:
                    raise CardError(f"{path}:{number}: {error}") from error
    if not result:
        raise CardError("no Replika records found")
    return result
