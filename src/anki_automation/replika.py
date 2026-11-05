"""Check Replika content and run the isolated Anki Desktop package builder."""

import argparse
import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import asdict
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from anki_automation.cards import CardError

DECK = "Реплика — демо"
MODES = {
    "Реплика · Контекст": "context",
    "Реплика · Пропуск": "cloze",
    "Реплика · Точный ответ": "recall",
}
PACKAGE = f"{DECK}.apkg"
PREVIEW = "Preview.html"
SOURCE_PACKAGE = "Replika.apkg"
IMAGES = {".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
AUDIO = {".m4a", ".mp3", ".ogg", ".wav"}
INLINE = {"a", "b", "br", "em", "i", "span", "strong", "u"}
BLOCK = {"div", "li", "ol", "p", "ul"}
SOUND = re.compile(r"\[sound:([^\[\]\r\n]+)\]")
CLOZE = re.compile(r"\{\{c1::([^{}]+)\}\}")


def stable_key(guid: str) -> str:
    """Derive a hidden key that stays fixed when a note's content changes."""
    return "r-" + uuid.uuid5(uuid.NAMESPACE_URL, "anki-automation/replika/" + guid).hex


def media_file(folder: Path, name: str, suffixes: set[str]) -> Path:
    """Resolve a package media basename without accepting URLs or traversal."""
    path = folder / name
    if (
        not name
        or name != Path(name).name
        or any(ch in name for ch in "\\:?#\x00")
        or path.suffix.lower() not in suffixes
        or path.is_symlink()
        or not path.is_file()
        or not path.stat().st_size
    ):
        raise CardError("Missing or invalid package media file.")
    return path


class FieldHTML(HTMLParser):
    """Check the small HTML subset supported in Replika fields."""

    def __init__(self, field: str, folder: Path) -> None:
        super().__init__()
        self.field = field
        self.folder = folder
        self.text: list[str] = []
        self.images: list[dict[str, str | None]] = []
        self.line_breaks = False
        self.open_tags: list[str] = []

    def handle_data(self, data: str) -> None:
        self.text.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "img" and self.field == "Figure":
            allowed = {"src", "alt", "width", "height"}
            if not (values.get("alt") or "").strip():
                raise CardError("Figure requires image alt text.")
            media_file(self.folder, values.get("src") or "", IMAGES)
            self.images.append(values)
        elif tag in INLINE | BLOCK:
            allowed = {"lang"}
            if tag == "a" and self.field == "Source":
                allowed.add("href")
                target = urlsplit(values.get("href") or "")
                if target.scheme != "https" or not target.hostname:
                    raise CardError("Source links must use HTTPS.")
        else:
            raise CardError(f"{self.field}: unsupported HTML element.")
        if set(values) - allowed or len(values) != len(attrs):
            raise CardError(f"{self.field}: unsupported HTML attribute.")
        self.line_breaks |= tag in BLOCK or tag == "br"
        if tag not in {"br", "img"}:
            self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in {"br", "img"}:
            raise CardError(f"{self.field}: use matching HTML tags.")
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if not self.open_tags or self.open_tags.pop() != tag:
            raise CardError(f"{self.field}: use matching HTML tags.")

    def close(self) -> None:
        super().close()
        if self.open_tags:
            raise CardError(f"{self.field}: use matching HTML tags.")


def validate_note(
    name: str, fields: dict[str, str], media: Path, *, require_source: bool = False
) -> None:
    """Reject unusable notes before exporting either artifact."""
    key = fields.get("Key", "")
    if not re.fullmatch(r"r-[0-9a-f]{32}", key):
        raise CardError("Key must be r- followed by 32 lowercase hexadecimal digits.")
    mode = MODES[name]
    parsed = {}
    for field, value in fields.items():
        if field not in {"Key", "Audio"}:
            parser = FieldHTML(field, media)
            parser.feed(value)
            parser.close()
            if "[sound:" in "".join(parser.text).lower():
                raise CardError(f"{field}: put the recording in Audio.")
            if field == "Figure" and value.strip() and not parser.images:
                raise CardError("Figure must contain an image.")
            parsed[field] = parser

    required = ("Text",) if mode == "cloze" else ("Prompt", "Answer")
    if require_source:
        required += ("Source",)
    for field in required:
        if not "".join(parsed[field].text).strip():
            raise CardError(f"{field} is required.")
    if mode == "cloze":
        value = "".join(parsed["Text"].text)
        target = list(CLOZE.finditer(value))
        if (
            len(target) != 1
            or value.count("{{") != 1
            or value.count("}}") != 1
            or not CLOZE.search(fields["Text"])
        ):
            raise CardError("Text must contain exactly one c1 deletion.")
        if not target[0][1].partition("::")[0].strip():
            raise CardError("The c1 deletion must contain an answer.")
    if mode == "recall":
        answer = parsed["Answer"]
        if answer.line_breaks or len("".join(answer.text).strip().splitlines()) != 1:
            raise CardError("Answer must fit on one line.")
        audio = fields["Audio"].strip()
        if audio:
            sound = SOUND.fullmatch(audio)
            if not sound:
                raise CardError("Audio must be one [sound:filename] reference.")
            media_file(media, sound[1], AUDIO)


def add_replika_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser("replika-build", help="Validate and rebuild Replika offline.")
    export = commands.add_parser("replika-export", help="Build an Anki package from Replika JSONL.")
    validate = commands.add_parser("replika-validate", help="Check Replika JSONL without Anki.")
    for source in (export, validate):
        source.add_argument("inputs", nargs="+", type=Path)
        for flag in ("templates", "decks", "media-dir"):
            source.add_argument(f"--{flag}", required=True, type=Path)
    for builder in (parser, export):
        flags = ["base", "output-dir", "anki-python", "anki-packages"]
        if builder is parser:
            flags.append("templates")
        for flag in flags:
            builder.add_argument(f"--{flag}", required=True, type=Path)


def run_replika(args: argparse.Namespace) -> dict[str, object]:
    """Build in a scratch directory and publish both files together."""
    from anki_automation.contract import decode_json, get_contract
    from anki_automation.replika_sources import load_sources

    records = None
    if args.command in {"replika-export", "replika-validate"}:
        records = load_sources(args.inputs, args.templates, args.decks, args.media_dir)
        if args.command == "replika-validate":
            return {"cards": len(records), "decks": sorted({note.deck for note in records})}
    base = args.base.resolve()
    if base.suffix != ".apkg" or not base.is_file():
        raise CardError("Provide an existing .apkg as --base.")
    if not (args.anki_packages / "anki").is_dir() or not args.anki_python.is_file():
        raise CardError("Provide the Anki Desktop package directory and its matching Python.")
    target = args.output_dir.resolve()
    if target.exists() or args.output_dir.is_symlink():
        raise CardError("--output-dir must be a new directory.")
    if records is None:
        for name in MODES:
            definition = args.templates / f"{name}.json"
            params = decode_json(definition.read_text(encoding="utf-8"))
            get_contract().validate("noteTypes.create", params)
    target.parent.mkdir(parents=True, exist_ok=True)
    resources = Path(__file__).with_name("resources")
    with TemporaryDirectory(prefix="replika-", dir=target.parent) as tmp:
        work = Path(tmp)
        staging = work / "output"
        staging.mkdir()
        package = PACKAGE
        if records is not None:
            package = SOURCE_PACKAGE
            staged_media = work / "media"
            staged_media.mkdir()
            for name in {name for record in records for name in record.media}:
                shutil.copyfile(args.media_dir / name, staged_media / name)
            (work / "records.json").write_text(
                json.dumps([asdict(record) for record in records], ensure_ascii=False),
                encoding="utf-8",
            )
        result = subprocess.run(  # noqa: S603 -- explicit local interpreter; no shell
            [
                str(args.anki_python.resolve()),
                str(resources / "replika_worker.py"),
                str(args.templates.resolve()),
                str(base),
                str(work),
            ],
            cwd=work,
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    (str(args.anki_packages.resolve()), str(Path(__file__).resolve().parents[1]))
                ),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise CardError(result.stderr.strip() or "Anki package build failed.")
        if not all((staging / name).is_file() for name in (package, PREVIEW)):
            raise CardError("Anki builder did not produce both outputs.")
        summary = decode_json(result.stdout)
        if not isinstance(summary, dict) or not isinstance(summary.get("cards"), int):
            raise CardError("Anki builder returned an invalid summary.")
        staging.rename(target)
    return {"cards": summary["cards"], "output_dir": str(target)}
