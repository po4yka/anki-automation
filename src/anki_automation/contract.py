"""The pinned, packaged Anki-Connect API v1 contract."""

import json
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator


class ContractError(ValueError):
    """A method or value does not meet the pinned API contract."""


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ContractError(f"non-finite JSON number: {value}")


def decode_json(text: str | bytes) -> object:
    """Reject ambiguous object keys and non-standard numeric constants."""
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ContractError("invalid UTF-8 JSON") from error


def _open_results(value: object) -> object:
    # The API requires clients to tolerate added response properties.
    if isinstance(value, dict):
        return {
            key: _open_results(item)
            for key, item in value.items()
            if key not in {"additionalProperties", "unevaluatedProperties"}
        }
    if isinstance(value, list):
        return [_open_results(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class Method:
    """One upstream method and its execution metadata."""

    name: str
    summary: str
    scope: str
    effect: str
    completion: str
    undo: str
    notes: str

    @property
    def requires_apply(self) -> bool:
        return self.effect != "read"


class Contract:
    """Resolve methods and validate parameters without network access."""

    def __init__(self) -> None:
        resources = files("anki_automation").joinpath("resources")
        self.schema = json.loads(resources.joinpath("api-v1.schema.json").read_text())
        self.provenance = json.loads(resources.joinpath("provenance.json").read_text())
        self.definitions = self.schema["$defs"]
        self.methods = {
            name: Method(
                name=name, **{key: meta[key] for key in Method.__annotations__ if key != "name"}
            )
            for name, meta in sorted(self.schema["x-methods"].items())
        }
        self._result_definitions = _open_results(self.definitions)
        self._validators: dict[tuple[str, str], Validator] = {}

    def method(self, name: str) -> Method:
        try:
            return self.methods[name]
        except KeyError:
            raise ContractError(f"unknown API v1 method: {name}") from None

    def resolve(self, schema: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in schema:
            reference = str(schema["$ref"])
            schema = self.definitions[reference.removeprefix("#/$defs/")]
        return schema

    def params_schema(self, name: str) -> dict[str, Any]:
        self.method(name)
        return self.resolve(self.schema["x-methods"][name]["params"])

    def _validator(self, name: str, part: str) -> Validator:
        self.method(name)
        key = (name, part)
        if key not in self._validators:
            self._validators[key] = Draft202012Validator(
                {
                    "$defs": self.definitions if part == "params" else self._result_definitions,
                    **self.schema["x-methods"][name][part],
                }
            )
        return self._validators[key]

    def validate(self, name: str, value: object, *, part: str = "params") -> None:
        if part == "params":
            try:
                json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
            except (ValueError, TypeError, UnicodeError) as error:
                raise ContractError("parameters must contain finite, UTF-8 JSON values") from error
        error = next(iter(self._validator(name, part).iter_errors(value)), None)
        if error is not None:
            pointer = "/" + "/".join(str(item) for item in error.absolute_path)
            # JSON Schema messages can contain the private value being checked.
            raise ContractError(f"{name} {part}{pointer}: {error.validator} constraint failed")
        if name == "api.execute" and part == "params" and isinstance(value, dict):
            for command in value["commands"]:
                child = command["method"]
                if child in {"api.execute", "profiles.switch", "application.exit"}:
                    raise ContractError(f"api.execute cannot contain {child}")
                self.validate(child, command.get("params", {}))

    def describe(self, name: str) -> dict[str, Any]:
        """Return one method and only the definitions its schemas reference."""
        self.method(name)
        meta = self.schema["x-methods"][name]
        definitions: dict[str, object] = {}

        def visit(value: object) -> None:
            if isinstance(value, dict):
                reference = value.get("$ref")
                if isinstance(reference, str):
                    key = reference.removeprefix("#/$defs/")
                    if key not in definitions:
                        definitions[key] = self.definitions[key]
                        visit(definitions[key])
                for item in value.values():
                    visit(item)
            elif isinstance(value, list):
                for item in value:
                    visit(item)

        visit(meta)
        return {"name": name, **meta, "$defs": definitions}


@cache
def get_contract() -> Contract:
    return Contract()
