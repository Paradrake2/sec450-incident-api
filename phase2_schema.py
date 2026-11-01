"""Historical Phase 2 operation inventory and sanitized response validation."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry
from referencing.exceptions import NoSuchResource


_CONTRACT = (
    Path(__file__).resolve().with_name("contracts") / "phase2-v1.0.0.openapi.json"
)
_BASE_PATH = "/api/v1"
_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def load_contract() -> dict:
    """Load a fresh copy; callers cannot mutate the on-disk baseline."""
    return json.loads(_CONTRACT.read_text(encoding="utf-8"))


def operation_inventory(contract) -> list[dict]:
    """List every declared operation, including ones a target lacks."""
    operations = []
    for relative_path, path_item in contract["paths"].items():
        for method, operation in path_item.items():
            if method not in _METHODS:
                continue
            statuses = sorted(
                int(status)
                for status in operation["responses"]
                if str(status).isdigit()
            )
            operations.append(
                {
                    "method": method.upper(),
                    "path": _BASE_PATH + relative_path,
                    "requirements": list(operation.get("x-requirements", [])),
                    "successStatuses": [
                        status for status in statuses if 200 <= status < 300
                    ],
                    "errorStatuses": [
                        status for status in statuses if 400 <= status < 600
                    ],
                }
            )
    return operations


def _internal_references_only(document):
    pending = [document]
    seen = set()
    while pending:
        value = pending.pop()
        if isinstance(value, (dict, list)):
            if id(value) in seen:
                continue
            seen.add(id(value))
        if isinstance(value, dict):
            reference = value.get("$ref")
            if "$ref" in value and (
                not isinstance(reference, str) or not reference.startswith("#/")
            ):
                return False
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return True


def _resolve_response(contract, definition):
    visited = set()
    while isinstance(definition, dict) and "$ref" in definition:
        reference = definition["$ref"]
        if reference in visited or len(visited) >= 32:
            raise ValueError("Invalid response reference")
        visited.add(reference)
        definition = contract
        for part in reference[2:].split("/"):
            definition = definition[part.replace("~1", "/").replace("~0", "~")]
    if not isinstance(definition, dict):
        raise ValueError("Invalid response definition")
    return definition


def _no_remote_resource(uri):
    raise NoSuchResource(ref=uri)


def _invalid_json_number(value):
    raise ValueError("Non-finite number")


def validate_response(
    contract, method, path_template, response, expected_status: int
) -> list[str]:
    """Check one expected response without leaking payloads or fetching refs.

    Errors intentionally omit validator messages, unknown field names, response
    values, and URI text. Behavioral assertions remain the test case's job.
    """
    if not _internal_references_only(contract):
        return ["Contract references must be internal JSON pointers"]
    if type(expected_status) is not int or not 100 <= expected_status <= 599:
        return ["Expected status must be an integer HTTP status"]
    try:
        if not path_template.startswith(_BASE_PATH + "/"):
            raise KeyError
        operation = contract["paths"][path_template[len(_BASE_PATH) :]][method.lower()]
        definition = operation["responses"].get(str(expected_status))
    except (KeyError, AttributeError, TypeError):
        return ["Unknown contract operation"]
    if definition is None:
        return ["Expected status is not declared for this operation"]
    if response.status_code != expected_status:
        return ["Response status does not match expected status"]
    if expected_status == 204:
        return (
            []
            if not response.content
            else ["No-content response must have an empty body"]
        )
    try:
        definition = _resolve_response(contract, definition)
        content = definition.get("content", {})
        if not content:
            return []
        schema = content["application/json"]["schema"]
    except (KeyError, ValueError, TypeError):
        return ["Contract response definition is invalid"]
    media_type = response.headers.get(
        "content-type", response.headers.get("Content-Type", "")
    )
    if media_type.split(";", 1)[0].strip().lower() != "application/json":
        return ["JSON response Content-Type is required"]
    try:
        instance = json.loads(response.content, parse_constant=_invalid_json_number)
        json.dumps(instance, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return ["Response body is not valid JSON"]
    # Keep the complete contract root so #/components references preserve their
    # original meaning. Other OpenAPI keywords are ignored by JSON Schema.
    validation_root = {**contract, "allOf": [schema]}
    try:
        Draft202012Validator.check_schema(validation_root)
        validator = Draft202012Validator(
            validation_root,
            format_checker=FormatChecker(),
            registry=Registry(retrieve=_no_remote_resource),
        )
        if next(validator.iter_errors(instance), None) is not None:
            return ["Response body does not conform to the declared schema"]
    except Exception:
        return ["Contract schema validation could not be completed"]
    return []
