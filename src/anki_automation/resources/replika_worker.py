"""Build with Anki Desktop's bundled engine in disposable collections only."""

import base64
import json
import mimetypes
import re
import sys
from html import escape
from pathlib import Path
from typing import Any

from anki.collection import Collection, DeckIdLimit, NoteIdsLimit  # ty: ignore[import-not-found]
from anki.import_export_pb2 import (  # ty: ignore[import-not-found]
    ExportAnkiPackageOptions,
    ImportAnkiPackageOptions,
    ImportAnkiPackageRequest,
)

from anki_automation.cards import CardError
from anki_automation.replika import (
    AUDIO,
    DECK,
    IMAGES,
    MODES,
    PACKAGE,
    PREVIEW,
    SOUND,
    SOURCE_PACKAGE,
    FieldHTML,
    media_file,
    stable_key,
    validate_note,
)

QUERY = f'deck:"{DECK}"'
PLAY = re.compile(r"\[anki:play:[qa]:0\]")
IMAGE = re.compile(r"""<img\b(?:[^"'<>]|"[^"]*"|'[^']*')*>""", re.IGNORECASE)
LABELS = {"words": "Слова", "grammar": "Грамматика", "slang": "Сленг и сокращения"}


def load(collection: Any, path: Path) -> None:
    collection.import_anki_package(
        ImportAnkiPackageRequest(
            package_path=str(path),
            options=ImportAnkiPackageOptions(with_scheduling=False, with_deck_configs=False),
        )
    )


def configure(collection: Any, templates: Path, css: str) -> None:
    for name in MODES:
        data = json.loads((templates / f"{name}.json").read_text(encoding="utf-8"))
        model = collection.models.by_name(name)
        if model is None or len(model["tmpls"]) != 1 or len(data["templates"]) != 1:
            raise CardError(f"The package must contain one template for {name}.")
        fields = [field["name"] for field in data["fields"]]
        existing = [field["name"] for field in model["flds"]]
        if (
            data["name"] != name
            or fields[0] != "Key"
            or fields[1:] != [field for field in existing if field != "Key"]
            or model["type"] != int(data.get("kind") == "cloze")
        ):
            raise CardError(f"Unexpected field or template layout for {name}.")
        if "Key" not in existing:
            key = collection.models.new_field("Key")
            collection.models.add_field(model, key)
            collection.models.reposition_field(model, key, 0)
        elif existing[0] != "Key":
            raise CardError(f"Key must be the first field of {name}.")
        for field, spec in zip(model["flds"], data["fields"], strict=True):
            field.update(
                description=spec.get("description", ""), plainText=spec.get("plainText", False)
            )
        model["sortf"] = fields.index(data["sortField"])
        model["css"] = css
        spec = data["templates"][0]
        model["tmpls"][0].update(
            name=spec["name"], qfmt=spec["questionHtml"], afmt=spec["answerHtml"]
        )
        collection.models.update_dict(model)


def check_notes(
    collection: Any, ids: list[int], *, assign_keys: bool = False
) -> dict[int, tuple[int, str, int]]:
    media = Path(collection.media.dir())
    cards = [collection.get_card(cid) for cid in ids]
    if not cards or len(cards) != len({card.nid for card in cards}):
        raise CardError("The package must contain exactly one card per note.")
    keys: set[str] = set()
    identity = {}
    for card in cards:
        note = card.note()
        model = card.note_type()
        name = model["name"]
        if name not in MODES:
            raise CardError("The package contains an unsupported note type.")
        if assign_keys and not note["Key"]:
            note["Key"] = stable_key(note.guid)
            collection.update_note(note)
        try:
            validate_note(name, dict(note.items()), media)
            if note["Key"] in keys:
                raise CardError("Duplicate Key.")
            keys.add(note["Key"])
            rendered = card.render_output()
            if 'class="replika"' not in rendered.question_text or (
                MODES[name] == "cloze" and 'class="cloze"' not in rendered.question_text
            ):
                raise CardError("Anki did not render a valid question.")
            if MODES[name] == "recall" and note["Audio"] and not rendered.question_av_tags:
                raise CardError("Anki did not render the recording.")
        except CardError as error:
            raise CardError(f"Note {note.id}: {error}") from error
        identity[card.id] = (note.id, note.guid, model["id"])
    return identity


def data_uri(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def preview(collection: Any, css: str, ids: list[int]) -> str:
    media = Path(collection.media.dir())

    def image_src(match: re.Match[str]) -> str:
        field = FieldHTML("Figure", media)
        field.feed(match[0])
        field.close()
        attributes = field.images[0]
        attributes["src"] = data_uri(media_file(media, attributes["src"] or "", IMAGES))
        return (
            "<img"
            + "".join(
                f' {name}="{escape(value or "", quote=True)}"' for name, value in attributes.items()
            )
            + ">"
        )

    examples = []
    cards = sorted(
        (collection.get_card(cid) for cid in ids),
        key=lambda card: (
            list(MODES).index(card.note_type()["name"]),
            bool(dict(card.note().items()).get("Context")),
            bool(dict(card.note().items()).get("Audio")),
            card.id,
        ),
    )
    for i, card in enumerate(cards, 1):
        name = card.note_type()["name"]
        note = card.note()
        rendered = card.render_output()
        front, back = rendered.question_text, rendered.answer_text
        front = front.replace(
            "[[type:Answer]]",
            '<input aria-label="Твой ответ" type="text" autocomplete="off" spellcheck="false">',
        )
        back = back.replace("[[type:Answer]]", "").replace('id="answer"', f'id="answer-{i}"')
        sound = SOUND.fullmatch(dict(note.items()).get("Audio", "").strip())
        if sound:
            uri = data_uri(media_file(media, sound[1], AUDIO))
            player = f'<audio controls preload="metadata" src="{uri}" aria-label="Запись"></audio>'
            front, back = PLAY.sub(player, front), PLAY.sub(player, back)
        front, back = IMAGE.sub(image_src, front), IMAGE.sub(image_src, back)
        category = next(
            (
                LABELS.get(tag.removeprefix("category::"), "")
                for tag in note.tags
                if tag.startswith("category::")
            ),
            "",
        )
        caption = f" <span>· {category}</span>" if category else ""
        label = escape(name.removeprefix("Реплика · "))
        examples.append(f"""<section id="demo-{i}" class="demo-example"
         aria-labelledby="demo-title-{i}">
  <h2 id="demo-title-{i}">{i}. {label}{caption}</h2>
  {front}
  <details class="demo-answer">
    <summary>Показать ответ</summary>
    {back}
  </details>
</section>""")
    shell = Path(__file__).with_name("replika_preview.html").read_text(encoding="utf-8")
    return shell.replace("/* REPLIKA_CSS */", css).replace(
        "<!-- REPLIKA_CARDS -->", "\n".join(examples)
    )


def create_notes(collection: Any, records: list[dict[str, Any]], media: Path) -> list[int]:
    for name in {name for record in records for name in record["media"]}:
        path = media_file(media, name, AUDIO | IMAGES)
        if collection.media.add_file(str(path)) != name:
            raise CardError("A media name conflicts with the template seed.")
    ids = []
    for record in records:
        model = collection.models.by_name(record["note_type"])
        note = collection.new_note(model)
        note.guid = record["fields"]["Key"]
        for key, value in record["fields"].items():
            note[key] = value
        note.tags = record["tags"]
        if note.fields_check() != 0:
            raise CardError("Anki rejected a source note or its key conflicts with the seed.")
        collection.add_note(note, collection.decks.id(record["deck"]))
        cards = note.card_ids()
        if len(cards) != 1:
            raise CardError("A source note did not generate exactly one card.")
        ids.extend(cards)
    return ids


def verify_sources(
    collection: Any, ids: list[int], records: list[dict[str, Any]], media: Path
) -> None:
    by_key = {record["fields"]["Key"]: record for record in records}
    for cid in ids:
        card = collection.get_card(cid)
        note = card.note()
        record = by_key.pop(note["Key"], None)
        if (
            record is None
            or note.guid != note["Key"]
            or card.note_type()["name"] != record["note_type"]
            or collection.decks.name(card.did) != record["deck"]
            or dict(note.items()) != record["fields"]
            or {tag.casefold() for tag in note.tags} != {tag.casefold() for tag in record["tags"]}
        ):
            raise CardError("The imported package changed the source note or its route.")
        for name in record["media"]:
            restored = media_file(Path(collection.media.dir()), name, AUDIO | IMAGES)
            if restored.read_bytes() != (media / name).read_bytes():
                raise CardError("The imported package changed source media.")
    if by_key:
        raise CardError("Some source notes were not imported.")


def build(templates: Path, base: Path, work: Path) -> int:
    css = (templates / "replika.css").read_text(encoding="utf-8")
    source = work / "records.json"
    records = json.loads(source.read_text(encoding="utf-8")) if source.is_file() else None
    package = work / "output" / (SOURCE_PACKAGE if records is not None else PACKAGE)
    author = Collection(str(work / "author.anki2"))
    try:
        load(author, base)
        ids = list(author.find_cards(QUERY))
        cards = (author.get_card(cid) for cid in ids)
        initial = {card.id: (card.nid, card.note().guid, card.note_type()["id"]) for card in cards}
        configure(author, templates, css)
        if records is not None:
            ids = create_notes(author, records, work / "media")
        identity = check_notes(author, ids, assign_keys=records is None)
        if records is None and identity != initial:
            raise CardError("Template update changed card or note identities.")
        if records is None:
            limit = DeckIdLimit(deck_id=author.decks.id(DECK, create=False))
        else:
            limit = NoteIdsLimit(note_ids=[note_id for note_id, _, _ in identity.values()])
        author.export_anki_package(
            out_path=str(package),
            options=ExportAnkiPackageOptions(
                with_media=True, with_scheduling=False, with_deck_configs=False
            ),
            limit=limit,
        )
    finally:
        author.close()

    test = Collection(str(work / "roundtrip.anki2"))
    try:
        load(test, package)
        imported = list(test.find_cards(""))
        if check_notes(test, imported) != identity:
            raise CardError("The exported package changed card or note identities.")
        if records is not None:
            verify_sources(test, imported, records, work / "media")
        (work / "output" / PREVIEW).write_text(preview(test, css, imported), encoding="utf-8")
    finally:
        test.close()
    return len(identity)


if __name__ == "__main__":
    try:
        print(json.dumps({"cards": build(*(Path(arg) for arg in sys.argv[1:]))}))
    except (CardError, OSError, ValueError) as error:
        sys.exit(str(error))
