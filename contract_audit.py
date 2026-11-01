"""Observed Phase 2 contract checks on a disposable local incident API.

The CLI never contacts a deployed service and never reads application secrets.
The library runner invokes reset: supply only an isolated synthetic test client.
Reports are partial evidence, not cross-team acceptance or measured coverage.
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import logging
import os
import sys

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from phase2_schema import load_contract, operation_inventory, validate_response
from testing_database import disposable_engine


BASE = "/api/v1"
DETAIL = BASE + "/incidents/{incidentId}"
HISTORICAL_LOG = (
    "Multiple unauthorized login attempts were followed by successful access "
    "to the employee database from an unknown external IP address."
)


def _json(response):
    try:
        return response.json() if response is not None else None
    except (ValueError, TypeError):
        return None


def _identifier(response):
    body = _json(response)
    if not isinstance(body, dict):
        return None
    value = body.get("id")
    # Our synthetic records use UUIDs. Refuse routing delimiters from a broken
    # implementation rather than turning returned values into arbitrary paths.
    if (
        isinstance(value, str)
        and value
        and len(value) <= 128
        and all(char.isascii() and (char.isalnum() or char in "_-") for char in value)
    ):
        return value
    return None


class _Collector:
    def __init__(self, client, contract):
        self.client, self.contract = client, contract
        self.rows = {
            (operation["method"], operation["path"]): {
                **operation,
                "implemented": "not_verified",
                "checks": [],
                "notes": [],
            }
            for operation in operation_inventory(contract)
        }

    def request(
        self, method, path, template, expected, ident, *, verify=None, **kwargs
    ):
        row = self.rows[(method, template)]
        response = None
        outcome = "fail"
        try:
            response = self.client.request(method, path, **kwargs)
            problems = validate_response(
                self.contract, method, template, response, expected
            )
            if not problems and verify is not None:
                problems.extend(verify(_json(response)))
            outcome = "fail" if problems else "pass"
        except Exception:
            problems = ["The local request or check could not complete"]
            outcome = "error"
        row["checks"].append(
            {
                "id": ident,
                "kind": "success" if expected < 400 else "error",
                "expectedStatus": expected,
                "actualStatus": response.status_code if response is not None else None,
                "outcome": outcome,
                "problems": problems,
            }
        )
        return response

    def finish(self, example):
        for row in self.rows.values():
            successes = [check for check in row["checks"] if check["kind"] == "success"]
            errors = [check for check in row["checks"] if check["kind"] == "error"]
            reached = any(
                check["actualStatus"] == check["expectedStatus"] for check in successes
            )
            row["implemented"] = (
                "yes" if reached else "partial" if row["checks"] else "not_verified"
            )
            tested = {check["expectedStatus"] for check in errors}
            row["testedErrorStatuses"] = sorted(tested)
            row["unverifiedErrorStatuses"] = sorted(set(row["errorStatuses"]) - tested)
            row["successConformance"] = _conformance(successes)
            row["errorConformance"] = _conformance(errors)
            if row["errorConformance"] == "pass" and row["unverifiedErrorStatuses"]:
                row["errorConformance"] = "partial"
            if row["path"] == BASE + "/test/reset":
                row["notes"].append(
                    "Shared fixture dataset not supplied; fixture restoration was not evaluated"
                )
                if row["successConformance"] == "pass":
                    row["successConformance"] = "partial"
            if not row["checks"]:
                row["notes"].append(
                    "Not exercised because required synthetic setup did not complete"
                )
        counts = Counter(
            check["outcome"] for row in self.rows.values() for check in row["checks"]
        )
        return {
            "scope": "local_contract_audit",
            "conformanceEstablished": False,
            "contractVersion": self.contract["info"]["version"],
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "passed": counts["pass"],
                "failed": counts["fail"],
                "errors": counts["error"],
            },
            "operations": list(self.rows.values()),
            "historicalExample": example,
            "limits": [
                "Local working-tree checks, not a deployed or bidirectional cross-team run",
                "Unexercised statuses remain unverified; unexpected 500/health 503 are not induced",
                "Shared fixtures, session protocol agreement and coverage inventories remain incomplete",
                "No code, component, or system coverage measured by this command",
                "Historical assessment example is diagnostic; shared classification labels remain unconfirmed",
            ],
        }


def _conformance(checks):
    if not checks:
        return "not_tested"
    return "fail" if any(check["outcome"] != "pass" for check in checks) else "pass"


def _state(expected):
    return lambda body: (
        []
        if isinstance(body, dict) and body.get("state") == expected
        else ["Unexpected incident state"]
    )


def run_audit(client, contract=None):
    """Exercise real incident operations, with reset limited by caller isolation."""
    contract = load_contract() if contract is None else contract
    audit = _Collector(client, contract)
    request = audit.request
    collection = BASE + "/incidents"
    example = {
        "status": "unverified",
        "expectedPotentiallyMaterial": True,
        "observedPotentiallyMaterial": None,
        "note": "Phase 2 Section 5.2 illustrative positive example; not a confirmed shared label set",
    }
    request("GET", BASE + "/health", BASE + "/health", 200, "health.available")
    reset = request(
        "POST", BASE + "/test/reset", BASE + "/test/reset", 204, "reset.status"
    )
    if reset is None or reset.status_code != 204:
        return audit.finish(example)

    def create(ident, **changes):
        payload = {
            "title": "Synthetic contract audit incident",
            "log": "Synthetic ransomware affecting systems",
            "occurredAt": "2026-10-31T12:00:00Z",
            "source": "local-contract-audit",
        }
        payload.update(changes)

        def stored_values(body):
            problems = _state("RECEIVED")(body)
            if any(
                body.get(name) != payload[name] for name in ("title", "log", "source")
            ):
                problems.append("Submitted incident values were not preserved")
            return problems

        return request(
            "POST",
            collection,
            collection,
            201,
            ident,
            json=payload,
            verify=stored_values,
        )

    primary, secondary, third, null_review = [
        _identifier(create("create." + name))
        for name in ("primary", "secondary", "third", "null-review")
    ]
    if not all((primary, secondary, third, null_review)):
        return audit.finish(example)

    def path(ident):
        return collection + "/" + ident

    def op(suffix):
        return DETAIL + "/" + suffix

    missing = path("contract-audit-missing")
    payload = {
        "title": "Synthetic",
        "log": "Synthetic",
        "occurredAt": "2026-10-31T12:00:00Z",
    }
    request(
        "POST",
        collection,
        collection,
        400,
        "create.missing-log",
        json={"title": "Synthetic"},
    )
    request(
        "POST",
        collection,
        collection,
        415,
        "create.media-type",
        content="{}",
        headers={"content-type": "text/plain"},
    )
    request(
        "POST",
        collection,
        collection,
        400,
        "create.source-null",
        json={**payload, "source": None},
    )
    request(
        "POST",
        collection,
        collection,
        400,
        "create.timestamp-without-zone",
        json={**payload, "occurredAt": "2026-10-31T12:00:00"},
    )
    request(
        "GET",
        collection,
        collection,
        200,
        "list.all",
        verify=lambda body: (
            []
            if {primary, secondary, third}.issubset({item["id"] for item in body})
            else ["Created incident IDs were missing from the list"]
        ),
    )
    request(
        "GET",
        collection,
        collection,
        200,
        "list.received",
        params={"state": "RECEIVED"},
        verify=lambda body: (
            []
            if isinstance(body, list)
            and body
            and all(item.get("state") == "RECEIVED" for item in body)
            else ["State filter did not preserve the requested state"]
        ),
    )
    request(
        "GET",
        collection,
        collection,
        400,
        "list.invalid-state",
        params={"state": "INVALID"},
    )
    request("GET", path(primary), DETAIL, 200, "get.created", verify=_state("RECEIVED"))
    request("GET", missing, DETAIL, 404, "get.unknown")
    request(
        "POST",
        path(primary) + "/disclosure",
        op("disclosure"),
        409,
        "disclosure.premature",
    )
    first_assessment = request(
        "POST", path(primary) + "/assessment", op("assessment"), 200, "assessment.first"
    )
    assessed_body = _json(first_assessment)
    if (
        isinstance(assessed_body, dict)
        and type(assessed_body.get("potentiallyMaterial")) is bool
    ):
        expected_state = (
            "POTENTIALLY_MATERIAL"
            if assessed_body["potentiallyMaterial"]
            else "UNDER_REVIEW"
        )
        request(
            "GET",
            path(primary),
            DETAIL,
            200,
            "get.assessed",
            verify=_state(expected_state),
        )
    request(
        "POST",
        path(primary) + "/assessment",
        op("assessment"),
        409,
        "assessment.repeat",
    )
    request(
        "POST", missing + "/assessment", op("assessment"), 404, "assessment.unknown"
    )
    material = {"decision": "MATERIAL", "reviewer": "Synthetic reviewer"}
    request(
        "POST",
        path(third) + "/review",
        op("review"),
        409,
        "review.unassessed",
        json=material,
    )
    request(
        "POST",
        path(primary) + "/review",
        op("review"),
        400,
        "review.missing-fields",
        json={},
    )
    request(
        "POST",
        missing + "/review",
        op("review"),
        404,
        "review.lookup-before-validation",
        json={},
    )
    request(
        "POST",
        path(null_review) + "/assessment",
        op("assessment"),
        200,
        "assessment.null-review-setup",
    )
    request(
        "POST",
        path(null_review) + "/review",
        op("review"),
        400,
        "review.rationale-null",
        json={**material, "rationale": None},
    )
    request(
        "POST",
        path(primary) + "/review",
        op("review"),
        200,
        "review.material",
        json=material,
        verify=_state("MATERIAL_CONFIRMED"),
    )
    request(
        "POST",
        path(primary) + "/review",
        op("review"),
        409,
        "review.confirmed-conflict",
        json=material,
    )
    request(
        "POST", missing + "/review", op("review"), 404, "review.unknown", json=material
    )
    request(
        "POST",
        path(third) + "/assessment",
        op("assessment"),
        200,
        "assessment.not-material-setup",
    )
    request(
        "POST",
        path(third) + "/review",
        op("review"),
        200,
        "review.not-material",
        json={**material, "decision": "NOT_MATERIAL"},
        verify=_state("NOT_MATERIAL"),
    )
    request(
        "POST",
        path(third) + "/review",
        op("review"),
        200,
        "review.escalation",
        json=material,
        verify=_state("MATERIAL_CONFIRMED"),
    )
    request(
        "POST",
        path(primary) + "/related",
        op("related"),
        200,
        "related.additional-property",
        json={
            "relatedIncidentId": third,
            "annotation": "Synthetic extension allowed by request schema",
        },
    )
    request(
        "POST",
        path(primary) + "/related",
        op("related"),
        200,
        "related.create",
        json={"relatedIncidentId": secondary},
        verify=lambda body: (
            []
            if secondary in body["relatedIncidentIds"]
            and body["state"] == "MATERIAL_CONFIRMED"
            else ["Relationship was not exposed or changed the incident state"]
        ),
    )
    request(
        "GET",
        path(secondary),
        DETAIL,
        200,
        "get.related-state-preserved",
        verify=_state("RECEIVED"),
    )
    request(
        "POST",
        path(primary) + "/related",
        op("related"),
        409,
        "related.duplicate",
        json={"relatedIncidentId": secondary},
    )
    request(
        "POST",
        path(primary) + "/related",
        op("related"),
        409,
        "related.self",
        json={"relatedIncidentId": primary},
    )
    request(
        "POST",
        path(primary) + "/related",
        op("related"),
        404,
        "related.unknown-target",
        json={"relatedIncidentId": "contract-audit-missing"},
    )
    request(
        "POST",
        missing + "/related",
        op("related"),
        404,
        "related.unknown-source",
        json={"relatedIncidentId": secondary},
    )
    request(
        "POST",
        path(primary) + "/related",
        op("related"),
        400,
        "related.missing-fields",
        json={},
    )
    request(
        "POST",
        missing + "/related",
        op("related"),
        404,
        "related.lookup-before-validation",
        json={},
    )
    request(
        "POST",
        path(primary) + "/disclosure",
        op("disclosure"),
        200,
        "disclosure.material",
        verify=lambda body: (
            []
            if isinstance(body, dict)
            and body.get("incidentId") == primary
            and isinstance(body.get("draft"), str)
            and body["draft"].strip()
            and body.get("final") is False
            else ["Disclosure ID, nonempty draft, or non-final marker did not conform"]
        ),
    )
    request(
        "POST", missing + "/disclosure", op("disclosure"), 404, "disclosure.unknown"
    )
    historical = _identifier(create("create.historical-example", log=HISTORICAL_LOG))
    if historical is not None:
        historical_response = request(
            "POST",
            path(historical) + "/assessment",
            op("assessment"),
            200,
            "assessment.historical-example-structure",
        )
        observed = _json(historical_response)
        if (
            historical_response is not None
            and historical_response.status_code == 200
            and isinstance(observed, dict)
            and type(observed.get("potentiallyMaterial")) is bool
        ):
            flag = observed["potentiallyMaterial"]
            example.update(
                status="matched" if flag else "mismatch",
                observedPotentiallyMaterial=flag,
            )
    return audit.finish(example)


def render_markdown(report):
    lines = [
        f"Phase 2 contract {report['contractVersion']} - local observed audit",
        "",
        "This is partial local evidence; cross-team conformance is not established.",
        "",
        "| Operation | Requirements | Implemented | Success checks | Error checks | Unverified errors |",
        "|---|---|---|---|---|---|",
    ]
    for row in report["operations"]:
        lines.append(
            f"| {row['method']} {row['path']} | {', '.join(row['requirements']) or '-'} | {row['implemented']} | {row['successConformance']} | {row['errorConformance']} | {', '.join(map(str, row['unverifiedErrorStatuses'])) or '-'} |"
        )
    lines.extend(
        [
            "",
            "Observed totals: " + json.dumps(report["summary"], sort_keys=True),
            "",
            "Findings:",
        ]
    )
    for row in report["operations"]:
        for check in row["checks"]:
            if check["outcome"] != "pass":
                status = f"expected {check['expectedStatus']}, received {check['actualStatus']}"
                lines.append(
                    f"- {check['id']} ({status}): " + "; ".join(check["problems"])
                )
        for note in row["notes"]:
            lines.append(f"- {row['method']} {row['path']}: {note}")
    lines.extend(
        [
            "",
            "Historical assessment example: " + report["historicalExample"]["status"],
            "",
        ]
    )
    lines.extend("- " + limit for limit in report["limits"])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args(argv)
    previous_url = os.environ.get("DATABASE_URL")
    loggers = [logging.getLogger(name) for name in ("sec450", "httpx")]
    previous_levels = [logger.level for logger in loggers]
    try:
        with disposable_engine() as engine:
            os.environ["DATABASE_URL"] = engine.url.render_as_string(
                hide_password=False
            )
            import main as application

            for logger in loggers:
                logger.setLevel(logging.WARNING)
            previous_engine = application.app.state.database_engine
            application.app.state.database_engine = engine
            try:
                with TestClient(
                    application.app, raise_server_exceptions=False
                ) as client:
                    report = run_audit(client)
                report["databaseBackend"] = engine.dialect.name
            finally:
                application.app.state.database_engine = previous_engine
            print(
                render_markdown(report)
                if args.format == "markdown"
                else json.dumps(report, indent=2)
            )
            return (
                1 if report["summary"]["failed"] or report["summary"]["errors"] else 0
            )
    except (ValueError, OSError, SQLAlchemyError):
        print(
            json.dumps(
                {
                    "error": "configuration",
                    "message": "Could not run the isolated audit; check dependencies and the local test database configuration",
                }
            ),
            file=sys.stderr,
        )
        return 2
    finally:
        for logger, level in zip(loggers, previous_levels):
            logger.setLevel(level)
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url


if __name__ == "__main__":
    raise SystemExit(main())
