"""Ordered, bounded black-box checks against the recovered Phase 2 contract.

Writes are allowed only on explicitly configured isolated course-system bases.
The synthetic inputs below are local engineering fixtures, not pod-approved
classification labels or legal materiality judgments. Missing external fixtures,
failure injection, and content-quality review remain visible skipped cases.
"""

from dataclasses import dataclass
from datetime import datetime
import re
from uuid import uuid4

from phase2_schema import load_contract, validate_response
from tester_http import HttpTargetGateway
from tester_interfaces import ProtocolFailure, SuiteCase, TestContext
from tester_targets import ApprovedTargets
from testing_protocol import TestResult


BASE = "/api/v1"
DETAIL = BASE + "/incidents/{incidentId}"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)
_SYNTHETIC_TIME = "2026-01-15T09:30:00-05:00"
_PRIMARY = {
    "title": "Synthetic course test: reported ransomware",
    "log": "Synthetic exercise: ransomware detected; customer data exfiltration reported.",
    "occurredAt": _SYNTHETIC_TIME,
}
_ROUTINE = {
    "title": "Synthetic course test: routine service check",
    "log": "Routine scheduled maintenance completed successfully.",
    "occurredAt": _SYNTHETIC_TIME,
}
_REVIEW = {"decision": "MATERIAL", "reviewer": "synthetic-course-reviewer"}
_NONMATERIAL = {**_REVIEW, "decision": "NOT_MATERIAL"}


def _state(context):
    return context.state.setdefault("incident_suite", {})


def _unknown(context):
    return _state(context).setdefault("unknown_id", str(uuid4()))


def _detail(key, suffix=""):
    return lambda context: BASE + "/incidents/" + _state(context)[key] + suffix


def _unknown_detail(suffix=""):
    return lambda context: BASE + "/incidents/" + _unknown(context) + suffix


def _value(value, context):
    return value(context) if callable(value) else value


def _identifier(body):
    value = body.get("id") if isinstance(body, dict) else None
    return value if isinstance(value, str) and _SAFE_ID.fullmatch(value) else None


def _matches_incident(body, submitted):
    return (
        _identifier(body) is not None
        and body["title"] == submitted["title"]
        and body["log"] == submitted["log"]
        and datetime.fromisoformat(
            body["occurredAt"].replace("Z", "+00:00").replace("z", "+00:00")
        )
        == datetime.fromisoformat(submitted["occurredAt"])
        and body["state"] == "RECEIVED"
        and body.get("assessment") is None
        and body.get("review") is None
        and body["relatedIncidentIds"] == []
        and body.get("source") is None
    )


def _is_reviewed(body, decision, state):
    review = body.get("review")
    return (
        body["state"] == state
        and isinstance(review, dict)
        and review.get("decision") == decision
        and review.get("reviewer") == _REVIEW["reviewer"]
        and review.get("rationale") is None
    )


def _is_advisory(body, expected, state):
    assessment = body.get("assessment")
    return (
        body["state"] == state
        and body.get("review") is None
        and isinstance(assessment, dict)
        and assessment.get("potentiallyMaterial") is expected
    )


@dataclass(frozen=True)
class _Case:
    test_id: str
    method: str
    path: object
    template: str
    status: int
    requires: tuple[str, ...] = ()
    json: object = None
    params: object = None
    content: bytes | None = None
    headers: dict | None = None
    check: object = None
    save: object = None
    reset: bool = False


def build_incident_cases(
    gateway: HttpTargetGateway, *, isolated_reset_targets: tuple[str, ...], allow_loopback: bool = False
) -> tuple[SuiteCase, ...]:
    """Build a stable case inventory; no network work occurs during building.

    Trust configuration must name exact isolated system bases, including any
    base path. An approved transport destination alone does not permit writes.
    Successful reset is a further prerequisite for every other POST. The
    coordinator retains unknown code/component/system coverage independently.
    """
    isolated = ApprovedTargets(isolated_reset_targets, allow_loopback=allow_loopback)
    contract = load_contract()
    cases = []

    def add(spec):
        async def run(context: TestContext):
            def result(outcome, message, source=None):
                return TestResult(
                    test_id=spec.test_id,
                    outcome=outcome,
                    message=message,
                    source=source,
                )

            state = _state(context)
            if spec.method == "POST":
                try:
                    isolated.normalize_and_require(context.target_url)
                except ProtocolFailure:
                    return result(
                        "skip",
                        "Writes require an explicitly configured isolated reset target.",
                    )
                if spec.reset:
                    # A failed repeat reset must not preserve old readiness/IDs.
                    state.clear()
                elif not state.get("mutation_ready"):
                    return result(
                        "skip", "The isolated reset prerequisite did not pass."
                    )
            if any(not state.get(key) for key in spec.requires):
                return result("skip", "A required synthetic setup case did not pass.")
            try:
                kwargs = {}
                if spec.json is not None:
                    kwargs["json"] = _value(spec.json, context)
                if spec.params is not None:
                    kwargs["params"] = _value(spec.params, context)
                if spec.content is not None:
                    kwargs["content"] = spec.content
                if spec.headers is not None:
                    kwargs["headers"] = spec.headers
                response = await gateway.request(
                    context.target_url,
                    context.session_id,
                    spec.method,
                    _value(spec.path, context),
                    **kwargs,
                )
                problems = validate_response(
                    contract, spec.method, spec.template, response, spec.status
                )
                if problems:
                    return result(
                        "fail", "Expected status or response schema did not conform."
                    )
                body = response.json() if spec.status != 204 else None
                if spec.check is not None and not spec.check(body, context):
                    return result(
                        "fail", "The response did not satisfy the case behavior."
                    )
                if spec.save is not None:
                    spec.save(body, context)
                return result(
                    "pass",
                    "Expected status, response schema, and case behavior passed.",
                )
            except ProtocolFailure as exc:
                return result(
                    "error", "The target request could not complete.", exc.source
                )
            except Exception:
                # No response values, target errors, credentials or tracebacks
                # enter the report even if an injected adapter misbehaves.
                return result("error", "The case could not complete safely.", "tester")

        cases.append(SuiteCase(spec.test_id, run))

    def gap(test_id, message):
        async def run(context):
            return TestResult(test_id=test_id, outcome="skip", message=message)

        cases.append(SuiteCase(test_id, run))

    add(_Case("health.ok", "GET", BASE + "/health", BASE + "/health", 200))
    add(_Case("list.read-only", "GET", BASE + "/incidents", BASE + "/incidents", 200))
    add(
        _Case(
            "list.invalid-state",
            "GET",
            BASE + "/incidents",
            BASE + "/incidents",
            400,
            params={"state": "INVALID_SYNTHETIC_STATE"},
        )
    )
    add(_Case("get.unknown", "GET", _unknown_detail(), DETAIL, 404))
    add(
        _Case(
            "reset.isolated",
            "POST",
            BASE + "/test/reset",
            BASE + "/test/reset",
            204,
            reset=True,
            save=lambda body, context: _state(context).update(mutation_ready=True),
        )
    )
    gap(
        "reset.shared-fixture-restoration",
        "Shared versioned fixtures and restoration expectations have not been supplied.",
    )

    add(
        _Case(
            "create.primary",
            "POST",
            BASE + "/incidents",
            BASE + "/incidents",
            201,
            json=_PRIMARY,
            check=lambda body, context: _matches_incident(body, _PRIMARY),
            save=lambda body, context: _state(context).update(
                primary_id=_identifier(body)
            ),
        )
    )
    add(
        _Case(
            "create.routine",
            "POST",
            BASE + "/incidents",
            BASE + "/incidents",
            201,
            json=_ROUTINE,
            check=lambda body, context: (
                _matches_incident(body, _ROUTINE)
                and body["id"] != _state(context).get("primary_id")
            ),
            save=lambda body, context: _state(context).update(
                routine_id=_identifier(body)
            ),
        )
    )
    add(
        _Case(
            "create.missing-log",
            "POST",
            BASE + "/incidents",
            BASE + "/incidents",
            400,
            json={"title": "Synthetic invalid input", "occurredAt": _SYNTHETIC_TIME},
        )
    )
    add(
        _Case(
            "create.wrong-media",
            "POST",
            BASE + "/incidents",
            BASE + "/incidents",
            415,
            content=b"synthetic plain text",
            headers={"Content-Type": "text/plain"},
        )
    )
    add(
        _Case(
            "regression.create-null-source",
            "POST",
            BASE + "/incidents",
            BASE + "/incidents",
            400,
            json={**_PRIMARY, "source": None},
        )
    )
    add(
        _Case(
            "regression.create-naive-timestamp",
            "POST",
            BASE + "/incidents",
            BASE + "/incidents",
            400,
            json={**_PRIMARY, "occurredAt": "2026-01-15T09:30:00"},
        )
    )
    add(
        _Case(
            "get.primary-stored",
            "GET",
            _detail("primary_id"),
            DETAIL,
            200,
            requires=("primary_id",),
            check=lambda body, context: (
                _matches_incident(body, _PRIMARY)
                and body["id"] == _state(context)["primary_id"]
            ),
        )
    )
    add(
        _Case(
            "list.received-filter",
            "GET",
            BASE + "/incidents",
            BASE + "/incidents",
            200,
            requires=("primary_id", "routine_id"),
            params={"state": "RECEIVED"},
            check=lambda body, context: (
                all(item["state"] == "RECEIVED" for item in body)
                and {_state(context)["primary_id"], _state(context)["routine_id"]}
                <= {item["id"] for item in body}
            ),
        )
    )
    add(
        _Case(
            "review.received-conflict",
            "POST",
            _detail("primary_id", "/review"),
            DETAIL + "/review",
            409,
            requires=("primary_id",),
            json=_REVIEW,
        )
    )
    add(
        _Case(
            "disclosure.received-conflict",
            "POST",
            _detail("primary_id", "/disclosure"),
            DETAIL + "/disclosure",
            409,
            requires=("primary_id",),
        )
    )
    for operation in ("assessment", "review", "related", "disclosure"):
        payload = (
            _REVIEW
            if operation == "review"
            else (
                {"relatedIncidentId": "synthetic-missing-related"}
                if operation == "related"
                else None
            )
        )
        add(
            _Case(
                operation + ".unknown",
                "POST",
                _unknown_detail("/" + operation),
                DETAIL + "/" + operation,
                404,
                json=payload,
            )
        )
    for operation in ("review", "related"):
        add(
            _Case(
                "regression." + operation + "-lookup-before-validation",
                "POST",
                _unknown_detail("/" + operation),
                DETAIL + "/" + operation,
                404,
                json={"syntheticInvalidField": True},
            )
        )

    add(
        _Case(
            "assessment.primary-advisory",
            "POST",
            _detail("primary_id", "/assessment"),
            DETAIL + "/assessment",
            200,
            requires=("primary_id",),
            check=lambda body, context: body["potentiallyMaterial"] is True,
            save=lambda body, context: _state(context).update(primary_assessed=True),
        )
    )
    add(
        _Case(
            "get.primary-advisory-state",
            "GET",
            _detail("primary_id"),
            DETAIL,
            200,
            requires=("primary_id", "primary_assessed"),
            check=lambda body, context: _is_advisory(
                body, True, "POTENTIALLY_MATERIAL"
            ),
        )
    )
    add(
        _Case(
            "assessment.repeat-conflict",
            "POST",
            _detail("primary_id", "/assessment"),
            DETAIL + "/assessment",
            409,
            requires=("primary_id", "primary_assessed"),
        )
    )
    add(
        _Case(
            "assessment.routine-advisory",
            "POST",
            _detail("routine_id", "/assessment"),
            DETAIL + "/assessment",
            200,
            requires=("routine_id",),
            check=lambda body, context: body["potentiallyMaterial"] is False,
            save=lambda body, context: _state(context).update(routine_assessed=True),
        )
    )
    add(
        _Case(
            "get.routine-advisory-state",
            "GET",
            _detail("routine_id"),
            DETAIL,
            200,
            requires=("routine_id", "routine_assessed"),
            check=lambda body, context: _is_advisory(body, False, "UNDER_REVIEW"),
        )
    )
    add(
        _Case(
            "review.invalid-decision",
            "POST",
            _detail("primary_id", "/review"),
            DETAIL + "/review",
            400,
            requires=("primary_id", "primary_assessed"),
            json={**_REVIEW, "decision": "INVALID"},
        )
    )
    add(
        _Case(
            "regression.review-null-rationale",
            "POST",
            _detail("primary_id", "/review"),
            DETAIL + "/review",
            400,
            requires=("primary_id", "primary_assessed"),
            json={**_REVIEW, "rationale": None},
        )
    )
    add(
        _Case(
            "review.primary-material",
            "POST",
            _detail("primary_id", "/review"),
            DETAIL + "/review",
            200,
            requires=("primary_id", "primary_assessed"),
            json=_REVIEW,
            check=lambda body, context: _is_reviewed(
                body, "MATERIAL", "MATERIAL_CONFIRMED"
            ),
            save=lambda body, context: _state(context).update(primary_reviewed=True),
        )
    )
    add(
        _Case(
            "review.material-downgrade-conflict",
            "POST",
            _detail("primary_id", "/review"),
            DETAIL + "/review",
            409,
            requires=("primary_id", "primary_reviewed"),
            json=_NONMATERIAL,
        )
    )
    add(
        _Case(
            "review.material-repeat-conflict",
            "POST",
            _detail("primary_id", "/review"),
            DETAIL + "/review",
            409,
            requires=("primary_id", "primary_reviewed"),
            json=_REVIEW,
        )
    )
    add(
        _Case(
            "review.routine-not-material",
            "POST",
            _detail("routine_id", "/review"),
            DETAIL + "/review",
            200,
            requires=("routine_id", "routine_assessed"),
            json=_NONMATERIAL,
            check=lambda body, context: _is_reviewed(
                body, "NOT_MATERIAL", "NOT_MATERIAL"
            ),
            save=lambda body, context: _state(context).update(routine_reviewed=True),
        )
    )
    add(
        _Case(
            "disclosure.not-material-conflict",
            "POST",
            _detail("routine_id", "/disclosure"),
            DETAIL + "/disclosure",
            409,
            requires=("routine_id", "routine_reviewed"),
        )
    )
    add(
        _Case(
            "review.routine-escalation",
            "POST",
            _detail("routine_id", "/review"),
            DETAIL + "/review",
            200,
            requires=("routine_id", "routine_reviewed"),
            json=_REVIEW,
            check=lambda body, context: _is_reviewed(
                body, "MATERIAL", "MATERIAL_CONFIRMED"
            ),
            save=lambda body, context: _state(context).update(routine_escalated=True),
        )
    )
    add(
        _Case(
            "related.self-conflict",
            "POST",
            _detail("primary_id", "/related"),
            DETAIL + "/related",
            409,
            requires=("primary_id",),
            json=lambda context: {"relatedIncidentId": _state(context)["primary_id"]},
        )
    )
    add(
        _Case(
            "related.missing-field",
            "POST",
            _detail("primary_id", "/related"),
            DETAIL + "/related",
            400,
            requires=("primary_id",),
            json={},
        )
    )
    add(
        _Case(
            "related.other-unknown",
            "POST",
            _detail("primary_id", "/related"),
            DETAIL + "/related",
            404,
            requires=("primary_id",),
            json=lambda context: {"relatedIncidentId": _unknown(context)},
        )
    )
    add(
        _Case(
            "regression.related-extension",
            "POST",
            _detail("primary_id", "/related"),
            DETAIL + "/related",
            200,
            requires=("primary_id", "routine_id", "primary_reviewed"),
            json=lambda context: {
                "relatedIncidentId": _state(context)["routine_id"],
                "syntheticExtension": {"ignored": True},
            },
            check=lambda body, context: (
                _state(context)["routine_id"] in body["relatedIncidentIds"]
                and body["state"] == "MATERIAL_CONFIRMED"
                and "syntheticExtension" not in body
            ),
            save=lambda body, context: _state(context).update(related=True),
        )
    )
    add(
        _Case(
            "related.duplicate-conflict",
            "POST",
            _detail("primary_id", "/related"),
            DETAIL + "/related",
            409,
            requires=("primary_id", "routine_id", "related"),
            json=lambda context: {"relatedIncidentId": _state(context)["routine_id"]},
        )
    )
    add(
        _Case(
            "get.related-symmetry",
            "GET",
            _detail("routine_id"),
            DETAIL,
            200,
            requires=("primary_id", "routine_id", "related", "routine_escalated"),
            check=lambda body, context: (
                _state(context)["primary_id"] in body["relatedIncidentIds"]
                and body["state"] == "MATERIAL_CONFIRMED"
            ),
        )
    )
    add(
        _Case(
            "disclosure.nonfinal-draft",
            "POST",
            _detail("primary_id", "/disclosure"),
            DETAIL + "/disclosure",
            200,
            requires=("primary_id", "primary_reviewed"),
            check=lambda body, context: (
                body["incidentId"] == _state(context)["primary_id"]
                and body["final"] is False
                and bool(body["draft"].strip())
            ),
        )
    )
    gap(
        "plan.shared-classification-labels",
        "Pod-approved positive and negative assessment fixtures remain unavailable; synthetic advisory checks do not establish shared classification conformance.",
    )
    gap(
        "plan.closed-state",
        "The common contract has no operation that reaches CLOSED; closed-state transitions were not exercised.",
    )
    gap(
        "plan.server-failure-responses",
        "Unexpected application 500 and health 503 responses are not induced by this black-box suite.",
    )
    gap(
        "plan.disclosure-quality",
        "Disclosure content and legal adequacy require human review; the automated case checks a non-final draft response only.",
    )
    return tuple(cases)
