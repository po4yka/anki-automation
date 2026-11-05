"""Compare note types in Anki with template sources and apply nonstructural changes."""

import argparse
import os
from pathlib import Path
from typing import Any, cast

from anki_automation.anki import AnkiClient
from anki_automation.cards import CardError
from anki_automation.contract import decode_json, get_contract
from anki_automation.errors import AnkiError

CSS = "replika.css"
NOTE_TYPE_KEYS = ("latexHeader", "latexFooter", "latexSvg")


def add_templates_parser(commands: argparse._SubParsersAction) -> None:
    parser = commands.add_parser(
        "templates",
        help="Compare note types with template sources; --apply writes nonstructural changes.",
    )
    parser.add_argument("directory", type=Path, help=f"Folder with note type JSON and {CSS}.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create missing note types; update field settings, templates, CSS, sort fields.",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=float, default=10.0)


def load_definitions(directory: Path) -> list[dict[str, Any]]:
    """Read every definition and attach the shared stylesheet.

    Only keys a source states are managed; omitted settings keep their Anki values.
    """
    css = (directory / CSS).read_text(encoding="utf-8")
    definitions = []
    for path in sorted(directory.glob("*.json")):
        data = decode_json(path.read_text(encoding="utf-8"))
        get_contract().validate("noteTypes.create", data)
        data = cast(dict[str, Any], data)
        if path.stem != data["name"]:
            raise CardError(f"{path.name}: the file name must match the note type name.")
        if {"css", "session"} & data.keys():
            raise CardError(f"{path.name}: css comes from {CSS}; session is not a source key.")
        definitions.append({**data, "css": css})
    if not definitions:
        raise CardError(f"{directory}: no note type JSON files.")
    return definitions


def _changed(wanted: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in wanted.items() if actual.get(key) != value}


def compare(source: dict[str, Any], live: dict[str, Any] | None) -> dict[str, Any]:
    """Return the edits that would bring the live type to the source.

    Structural drift (kind, field names/order, template names/order) blocks --apply.
    """
    report: dict[str, Any] = {"name": source["name"], "structural": [], "differences": []}
    if live is None:
        return {**report, "status": "missing", "edits": [], "patch": {}}
    structural = report["structural"]
    if source.get("kind", "normal") != live["kind"]:
        structural.append("kind")
    if [f["name"] for f in source["fields"]] != [f["name"] for f in live["fields"]]:
        structural.append("fields")
    if [t["name"] for t in source["templates"]] != [t["name"] for t in live["templates"]]:
        structural.append("template names")
    edits: list[dict[str, Any]] = []
    patch: dict[str, Any] = {}
    if not structural:
        for kind, op, pairs in (
            ("field", "updateField", zip(source["fields"], live["fields"], strict=True)),
            (
                "template",
                "updateTemplate",
                zip(source["templates"], live["templates"], strict=True),
            ),
        ):
            for wanted, actual in pairs:
                changed = _changed({k: v for k, v in wanted.items() if k != "name"}, actual)
                if changed:
                    edits.append({"op": op, "name": wanted["name"], "patch": changed})
                    report["differences"].append(f"{kind} {wanted['name']}: {', '.join(changed)}")
        managed = ("css", "sortField", *NOTE_TYPE_KEYS)
        patch = _changed({key: source[key] for key in managed if key in source}, live)
        report["differences"].extend(patch)
    status = "in_sync" if not structural and not edits and not patch else "drift"
    return {**report, "status": status, "edits": edits, "patch": patch}


def _live(client: AnkiClient, name: str, session: str) -> dict[str, Any] | None:
    not_found = get_contract().schema["x-errors"]["notFound"]
    try:
        params = {"noteType": {"name": name}, "session": session}
        return cast(dict[str, Any], client.call("noteTypes.get", params))
    except AnkiError as error:
        if error.code == not_found:
            return None
        raise


def _apply(
    client: AnkiClient, source: dict[str, Any], report: dict[str, Any], session: str
) -> None:
    """Record each confirmed step so a later failure still reports earlier writes."""
    reference = {"noteType": {"name": source["name"]}, "session": session}
    if report["status"] == "missing":
        client.call("noteTypes.create", {**source, "session": session}, apply=True)
        report["applied"].append("noteTypes.create")
        return
    if report["edits"]:
        client.call("noteTypes.editStructure", {**reference, "edits": report["edits"]}, apply=True)
        report["applied"].append("noteTypes.editStructure")
    if report["patch"]:
        client.call("noteTypes.update", {**reference, "patch": report["patch"]}, apply=True)
        report["applied"].append("noteTypes.update")


def run_templates(args: argparse.Namespace) -> dict[str, Any]:
    """Compare all note types in one session before any write; never retry a write."""
    definitions = load_definitions(args.directory)
    client = AnkiClient(
        port=args.port, timeout=args.timeout, api_key=os.environ.get("ANKI_CONNECT_KEY")
    )
    # One session for reads and writes: a profile switch then fails with staleSession.
    session = cast(dict[str, Any], client.call("collection.get"))["session"]
    reports = [compare(source, _live(client, source["name"], session)) for source in definitions]
    blocked = {report["name"]: report["structural"] for report in reports if report["structural"]}
    if args.apply and blocked:
        raise CardError(
            "Kind, field, or template-name changes need a structural migration; nothing was "
            f"written: {blocked}"
        )
    if args.apply:
        for source, report in zip(definitions, reports, strict=True):
            if report["status"] == "in_sync":
                continue
            report["applied"] = []
            try:
                _apply(client, source, report, session)
            except AnkiError as error:
                error.result = {"applied_before_failure": reports, "failed": source["name"]}
                raise
    in_sync = all(report["status"] == "in_sync" or "applied" in report for report in reports)
    return {"apply": args.apply, "in_sync": in_sync, "note_types": reports}
