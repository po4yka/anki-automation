"""Commands for checked source records, TSV exports, and explicit Anki writes."""

import argparse
import json
import os
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from anki_automation.anki import AnkiClient, AnkiError
from anki_automation.api_cli import add_api_parser, run_api
from anki_automation.cards import CardError, import_params, load_cards, render_tsv
from anki_automation.contract import ContractError
from anki_automation.errors import OperationTimeout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare language-learning notes for Anki.")
    parser.add_argument("--version", action="version", version=version("anki-automation"))
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Check UTF-8 JSONL source records.")
    validate.add_argument("input", type=Path)
    export = commands.add_parser("export", help="Write a new TSV file for Anki text import.")
    export.add_argument("input", type=Path)
    export.add_argument("--deck", required=True)
    export.add_argument("--output", required=True, type=Path)
    anki = commands.add_parser(
        "anki-import", help="Preview an atomic API v1 import; --apply writes."
    )
    anki.add_argument("input", type=Path)
    anki.add_argument("--deck", required=True)
    anki.add_argument(
        "--apply", action="store_true", help="Write notes into the active Anki profile."
    )
    anki.add_argument("--port", type=int, default=8765)
    anki.add_argument("--timeout", type=float, default=10.0)
    add_api_parser(commands)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Return zero only after validation, export, preview, or confirmed import."""
    args = _parser().parse_args(argv)
    try:
        cards = load_cards(args.input) if args.command != "anki" else []
        match args.command:
            case "validate":
                result = {"cards": len(cards)}
            case "export":
                tsv = render_tsv(cards, args.deck)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with args.output.open("x", encoding="utf-8", newline="") as output:
                    output.write(tsv)
                result = {"cards": len(cards), "output": str(args.output)}
            case "anki-import":
                params = import_params(cards, args.deck)
                client = AnkiClient(
                    port=args.port, timeout=args.timeout, api_key=os.environ.get("ANKI_CONNECT_KEY")
                )
                if args.apply:
                    ids = client.create_notes(params, len(cards))
                    result = {"created": len(ids), "note_ids": ids}
                else:
                    result = {"apply": False, "method": "notes.createMany", "params": params}
            case "anki":
                result = run_api(args)
            case _:
                raise AssertionError("argparse accepted an unknown command")
    except (CardError, ContractError, AnkiError, OSError, UnicodeError) as error:
        details = error.as_dict() if isinstance(error, AnkiError) else {"error": str(error)}
        print(json.dumps(details, ensure_ascii=True), file=sys.stderr)
        return 3 if isinstance(error, OperationTimeout) else 1
    print(json.dumps(result, ensure_ascii=False))
    return 0
