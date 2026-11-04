"""Check the catalog against pinned upstream examples, not a live Anki instance."""

import hashlib
import json
from collections import Counter
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from anki_automation.cli import main
from anki_automation.contract import Contract, ContractError, decode_json, get_contract

REQUESTS = json.loads((Path(__file__).parent / "fixtures/anki-v1-requests.json").read_text())


def test_packaged_schema_is_unmodified_and_complete() -> None:
    contract = get_contract()
    raw = files("anki_automation").joinpath("resources/api-v1.schema.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == contract.provenance["sha256"]
    assert len(contract.methods) == 96
    assert set(REQUESTS) == set(contract.methods)
    assert Counter(method.effect for method in contract.methods.values())["read"] == 40
    Draft202012Validator.check_schema(contract.schema)


@pytest.mark.parametrize("name", REQUESTS)
def test_each_official_request_has_a_working_cli_preview(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["anki", name, "--params", json.dumps(REQUESTS[name]), "--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["method"] == name
    assert result["params"] == REQUESTS[name]
    assert result["apply"] is False


@pytest.mark.parametrize("example", get_contract().schema["x-invalidExamples"])
def test_upstream_invalid_request_examples_are_rejected(example: dict[str, Any]) -> None:
    with pytest.raises(ContractError):
        get_contract().validate(example["method"], example["params"])


@pytest.mark.parametrize("example", get_contract().schema["x-examples"])
def test_upstream_response_examples_match_the_contract(example: dict[str, Any]) -> None:
    get_contract().validate(example["method"], example["response"]["result"], part="result")


def test_response_extensions_are_allowed_but_request_extensions_are_not() -> None:
    contract = get_contract()
    contract.validate(
        "collection.get",
        {"session": "s", "profile": "Test", "scheduler": "fsrs", "futureField": 1},
        part="result",
    )
    with pytest.raises(ContractError, match="additionalProperties"):
        contract.validate("collection.get", {"futureField": 1})


@pytest.mark.parametrize("method", ["api.execute", "profiles.switch", "application.exit"])
def test_forbidden_ordered_commands_fail_locally(method: str) -> None:
    with pytest.raises(ContractError, match="cannot contain"):
        get_contract().validate("api.execute", {"commands": [{"method": method}]})


def test_unknown_ordered_method_and_bad_child_parameters_fail_locally() -> None:
    with pytest.raises(ContractError, match="unknown API"):
        get_contract().validate("api.execute", {"commands": [{"method": "notes.missing"}]})
    with pytest.raises(ContractError, match=r"notes\.get params"):
        get_contract().validate("api.execute", {"commands": [{"method": "notes.get"}]})


@pytest.mark.parametrize("value", ['{"id":1,"id":2}', '{"value":NaN}', '{"value":Infinity}', "{"])
def test_ambiguous_or_nonstandard_json_is_rejected(value: str) -> None:
    with pytest.raises(ContractError):
        decode_json(value)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object(), "\ud800"])
def test_python_values_must_be_valid_json(value: object) -> None:
    with pytest.raises(ContractError, match="finite, UTF-8 JSON"):
        get_contract().validate("notes.get", {"id": value})


def test_method_description_contains_resolvable_definitions() -> None:
    description = Contract().describe("notes.create")
    assert description["effect"] == "write"
    assert "Reference" in description["$defs"]
    assert "operations_get_Result" not in description["$defs"]
