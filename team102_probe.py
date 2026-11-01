"""Read one source report and emit schema evidence without payloads or writes."""

import argparse
from collections import deque
from itertools import islice
import json
import os
from pathlib import Path
import re
import sys

from dotenv import dotenv_values
from jsonschema import Draft202012Validator, FormatChecker

from contract_schemas import IncidentResponse
from team102_client import Team102Client
from team102_contract import SourceApiError, decode_source_json, validate_report_id


_SCHEMA = IncidentResponse.model_json_schema()
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())
_WRAPPERS = ("reports", "incidents", "items", "data", "report", "incident", "body")
_KNOWN_FIELDS = set(_SCHEMA["properties"]) | set(_WRAPPERS) | {
    "report_id", "query", "count", "collection_gaps", "summary", "matched_rule",
    "requester", "data_destination", "timeline", "ai_assessment", "raw_lines",
    "integrity", "previous_cluster_id",
}
for _definition in _SCHEMA.get("$defs", {}).values():
    _KNOWN_FIELDS.update(_definition.get("properties", {}))
_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _json_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    return "integer" if isinstance(value, int) else "number"


def _candidates(document):
    """Inspect only named wrappers and first items, with bounded traversal."""
    queue = deque([("$", document, 0)])
    for _ in range(12):
        if not queue:
            break
        path, value, depth = queue.popleft()
        yield path, value
        if depth >= 3:
            continue
        if isinstance(value, dict):
            for name in _WRAPPERS:
                if name in value:
                    queue.append((f"{path}.{name}", value[name], depth + 1))
        elif isinstance(value, list) and value:
            queue.append((f"{path}[0]", value[0], depth + 1))


def _safe_path(parts):
    path = "$"
    for part in parts:
        if type(part) is int:
            path += f"[{part}]"
        else:
            path += "." + (part if part in _KNOWN_FIELDS else "*")
    return path


def _guide_match(value):
    if not isinstance(value, dict) or not isinstance(value.get("body"), dict):
        return False
    try:
        validate_report_id(value.get("report_id"))
        return True
    except ValueError:
        return False


def inspect_response(raw_content: bytes) -> dict:
    """Compare a sample to our JSON Schema and the guide's minimal envelope.

    Never emit instance values, unknown field names, or validator messages.
    A guide match verifies only numeric report_id/object body, not its contents.
    """
    document = decode_source_json(raw_content)
    candidates = []
    incident_matches, guide_matches = [], []
    empty_collection = document == []
    if isinstance(document, dict) and not (
        document.keys() & (set(_SCHEMA["required"]) | {"report_id"})
    ):
        empty_collection = any(
            document.get(name) == [] for name in ("reports", "incidents", "items", "data")
        )
    for path, value in _candidates(document):
        errors = list(islice(_VALIDATOR.iter_errors(value), 33))
        conforms = not errors
        guide_conforms = _guide_match(value)
        if conforms:
            incident_matches.append(path)
        if guide_conforms:
            guide_matches.append(path)
        fields = value if isinstance(value, dict) else {}
        violations = []
        for error in errors[:32]:
            violation = {"path": _safe_path(error.absolute_path), "keyword": error.validator}
            if violation not in violations:
                violations.append(violation)
        candidates.append({
            "path": path, "type": _json_type(value),
            "knownFields": {name: _json_type(fields[name]) for name in sorted(fields.keys() & _KNOWN_FIELDS)},
            "unknownFieldCount": len(fields.keys() - _KNOWN_FIELDS),
            "incidentResponse": {
                "conforms": conforms,
                "missingRequired": sorted(set(_SCHEMA["required"]) - fields.keys()),
                "violations": violations, "violationsTruncated": len(errors) > 32,
            },
            "guideReport": {"conforms": guide_conforms},
        })
    if incident_matches and guide_matches:
        classification = "ambiguous"
    elif "$" in incident_matches:
        classification = "incident_response"
    elif incident_matches:
        classification = "incident_in_envelope"
    elif guide_matches:
        classification = "guide_report"
    else:
        classification = "no_sample" if empty_collection else "unsupported"
    return {
        "schemaSource": "contract_schemas.IncidentResponse", "scope": "one_sample_only",
        "classification": classification, "directIncidentResponse": "$" in incident_matches,
        "incidentMatches": incident_matches, "guideMatches": guide_matches,
        "candidates": candidates,
    }


def _detail_identifier(document):
    for _, value in _candidates(document):
        if not isinstance(value, dict):
            continue
        for name in ("report_id", "id"):
            identifier = value.get(name)
            if type(identifier) is int and identifier >= 0:
                identifier = str(identifier)
            if isinstance(identifier, str) and _SAFE_ID.fullmatch(identifier):
                return identifier
    return None


def probe_one(client, report_id: str | None = None) -> dict:
    """Fetch one detail explicitly, or discover one ID with limit=1 first."""
    request_count = 1
    if report_id is None:
        sample = client.read_reports_sample()
        sample_summary = inspect_response(sample.raw_content)
        identifier = _detail_identifier(decode_source_json(sample.raw_content))
        if identifier is None:
            if sample_summary["classification"] == "no_sample":
                return {**sample_summary, "requestCount": 1, "sampleSource": "list"}
            raise SourceApiError(
                "invalid_response",
                "The list has no supported report identifier; provide --report-id",
            )
        report_id = identifier
        request_count = 2
    result = client.read_report(report_id)
    return {
        **inspect_response(result.raw_content),
        "requestCount": request_count, "sampleSource": "detail",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read one Team 102 report and print a redacted schema comparison")
    parser.add_argument("--report-id", help="Optional source identifier; otherwise discover one using limit=1")
    args = parser.parse_args(argv)
    try:
        file_values = dotenv_values(Path(__file__).resolve().with_name(".env"))
        api_key = os.environ.get("SEC450_KEY", file_values.get("SEC450_KEY"))
        if not api_key:
            raise SourceApiError("configuration", "SEC450_KEY must be configured locally for API access")
        with Team102Client(api_key) as client:
            summary = probe_one(client, args.report_id)
        print(json.dumps(summary, sort_keys=True))
        if summary["incidentMatches"]:
            return 0
        return 3 if summary["classification"] == "no_sample" else 2
    except SourceApiError as exc:
        error = {"error": exc.kind, "message": exc.message}
        if exc.status_code is not None:
            error["upstreamStatus"] = exc.status_code
        if exc.retry_after_seconds is not None:
            error["retryAfterSeconds"] = exc.retry_after_seconds
        print(json.dumps(error), file=sys.stderr)
        return 1
    except (ValueError, OSError):
        print(json.dumps({
            "error": "configuration", "message": "Probe configuration or identifier is invalid",
        }), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
