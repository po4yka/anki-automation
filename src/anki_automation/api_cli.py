"""Build method commands from the packaged protocol schema."""

import argparse
import os
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from anki_automation.anki import AnkiClient
from anki_automation.contract import ContractError, decode_json, get_contract
from anki_automation.errors import AnkiError


def _json_argument(value: str) -> object:
    try:
        return decode_json(value)
    except ContractError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _flag(name: str) -> str:
    return "--" + re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def _connection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=float, default=10.0)


def _waiting(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--wait-timeout", type=float, default=300.0)
    parser.add_argument("--poll-interval", type=float, default=0.25)


def add_api_parser(commands: argparse._SubParsersAction) -> None:
    contract = get_contract()
    parser = commands.add_parser(
        "anki", help="Use every method in the Anki-Connect API v1 catalog."
    )
    children = parser.add_subparsers(dest="api_command", required=True)
    methods = children.add_parser(
        "methods", help="List methods and their execution effects offline."
    )
    methods.add_argument("--group", default="", help="Filter by namespace, such as notes or media.")
    methods.add_argument(
        "--live", action="store_true", help="Check availability with api.describe."
    )
    _connection(methods)
    schema = children.add_parser("schema", help="Show a method's contract or the complete schema.")
    schema.add_argument("method", nargs="?", choices=list(contract.methods))
    wait = children.add_parser("wait", help="Wait for a retained operation ID; never cancels work.")
    wait.add_argument("operation_id")
    _connection(wait)
    _waiting(wait)
    call = children.add_parser("call", help="Call a catalog method with JSON parameters.")
    call.add_argument("method", choices=list(contract.methods))
    parsers = [(call, None)]
    for name, meta in contract.methods.items():
        command = children.add_parser(
            name, help=meta.summary, description=meta.summary + " " + meta.notes
        )
        for key, field_schema in contract.params_schema(name).get("properties", {}).items():
            resolved = contract.resolve(field_schema)
            field_type = resolved.get("type")
            options: dict[str, object] = {"dest": f"param_{key}", "default": argparse.SUPPRESS}
            if field_type == "boolean":
                options["action"] = argparse.BooleanOptionalAction
            else:
                options["type"] = {"string": str, "integer": int, "number": float}.get(
                    field_type, _json_argument
                )
            options["help"] = f"API parameter: {key}. See anki schema {name} for constraints."
            command.add_argument(_flag(key), **options)
        parsers.append((command, name))
    for command, name in parsers:
        source = command.add_mutually_exclusive_group()
        source.add_argument(
            "--params",
            type=_json_argument,
            default=argparse.SUPPRESS,
            help="Named parameters as a JSON object.",
        )
        source.add_argument(
            "--params-file", type=Path, help="Read JSON parameters; '-' reads stdin."
        )
        execution = command.add_mutually_exclusive_group()
        execution.add_argument(
            "--apply", action="store_true", help="Execute a method with side effects."
        )
        execution.add_argument(
            "--dry-run", action="store_true", help="Validate and preview without HTTP."
        )
        command.add_argument("--wait", action="store_true", help="Wait for operation completion.")
        command.add_argument(
            "--all-pages", action="store_true", help="Collect all pages of a read method."
        )
        command.add_argument("--max-pages", type=int, default=100)
        _connection(command)
        _waiting(command)
        if name is not None:
            command.set_defaults(method=name)


def _parameters(args: argparse.Namespace) -> dict[str, object]:
    value = getattr(args, "params", {})
    if args.params_file is not None:
        text = (
            sys.stdin.read()
            if str(args.params_file) == "-"
            else args.params_file.read_text(encoding="utf-8")
        )
        value = decode_json(text)
    if not isinstance(value, dict):
        raise ContractError("parameters must be a JSON object")
    params = dict(value)
    for key, item in vars(args).items():
        if key.startswith("param_"):
            name = key.removeprefix("param_")
            if name in params:
                raise ContractError(f"parameter supplied twice: {name}")
            params[name] = item
    return params


def run_api(args: argparse.Namespace) -> object:
    contract = get_contract()
    match args.api_command:
        case "methods":
            prefix = args.group + "." if args.group else ""
            result = {
                "provenance": contract.provenance,
                "methods": [
                    asdict(meta)
                    for name, meta in contract.methods.items()
                    if name.startswith(prefix)
                ],
            }
            if args.live:
                client = AnkiClient(
                    port=args.port,
                    timeout=args.timeout,
                    api_key=os.environ.get("ANKI_CONNECT_KEY"),
                )
                description = cast(dict[str, Any], client.call("api.describe"))
                remote = {item["name"]: item for item in description["methods"]}
                for method in result["methods"]:
                    support = remote.get(method["name"])
                    method["available"] = support["available"] if support is not None else False
                    method["reason"] = (
                        support["reason"] if support is not None else "not in runtime catalog"
                    )
                result["runtime"] = {
                    "apiMajor": description["apiMajor"],
                    "ankiVersion": description["ankiVersion"],
                    "unknown_methods": sorted(remote.keys() - contract.methods.keys()),
                }
            return result
        case "schema":
            return contract.schema if args.method is None else contract.describe(args.method)
    client = AnkiClient(
        port=args.port, timeout=args.timeout, api_key=os.environ.get("ANKI_CONNECT_KEY")
    )
    if args.api_command == "wait":
        return client.wait_for_operation(
            args.operation_id, wait_timeout=args.wait_timeout, poll_interval=args.poll_interval
        )
    params = _parameters(args)
    meta = contract.method(args.method)
    contract.validate(args.method, params)
    paginated = "cursor" in contract.params_schema(args.method).get("properties", {})
    if args.wait and (meta.completion != "operation" or args.all_pages):
        raise ContractError("--wait requires an operation method without --all-pages")
    if args.all_pages and (not paginated or meta.requires_apply):
        raise ContractError("--all-pages requires a paginated read method")
    if args.dry_run or (meta.requires_apply and not args.apply):
        return {"apply": False, **asdict(meta), "method": args.method, "params": params}
    if args.all_pages:
        pages = []
        try:
            for page in client.iter_pages(args.method, params, max_pages=args.max_pages):
                pages.append(page)
        except AnkiError as error:
            error.result = {"pages": pages, "last_result": error.result}
            raise
        return {"pages": pages}
    return client.call(
        args.method,
        params,
        apply=args.apply,
        wait=args.wait,
        wait_timeout=args.wait_timeout,
        poll_interval=args.poll_interval,
    )
