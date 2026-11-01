"""Validation and wire serialization for the cross-team testing protocol."""

import json

import pytest
from pydantic import ValidationError

from testing_protocol import (
    CoverageMeasure,
    ProtocolErrorBody,
    SessionResponse,
    SuiteCoverage,
    SuiteReport,
    SystemCoverage,
    SystemReport,
    SystemSetupRequest,
)
from testing_protocol import (
    TesterRunRequest as RunRequest,
)
from testing_protocol import (
    TesterSetupRequest as SetupRequest,
)
from testing_protocol import (
    TestResult as Result,
)


def unavailable_coverage():
    return {"covered": None, "total": None, "basis": "No instrumentation available"}


def report_payload():
    return {
        "session_id": " Session-ABC ",
        "target_url": "https://target.example.test",
        "results": [
            {"test_id": " health-01 ", "outcome": "pass", "message": "Healthy"},
            {
                "test_id": "read-02",
                "outcome": "error",
                "message": "Target could not be reached",
                "source": "communication",
            },
        ],
        "coverage": {
            "code": unavailable_coverage(),
            "component": {"covered": 1, "total": 2, "basis": "Component cases"},
            "system": {"covered": 0, "total": 0, "basis": "No system cases declared"},
        },
    }


def test_report_round_trip_preserves_ids_null_counts_and_result_sources():
    payload = report_payload()
    report = SuiteReport.model_validate(payload)
    assert report.model_dump() == payload
    assert json.loads(report.model_dump_json()) == payload
    assert SuiteReport.model_validate_json(report.model_dump_json()) == report
    assert report.session_id == " Session-ABC "
    assert report.results[0].test_id == " health-01 "
    assert report.coverage.system.covered == report.coverage.system.total == 0


@pytest.mark.parametrize("counts", [
    {"covered": None, "total": 10},
    {"covered": 0, "total": None},
    {"covered": -1, "total": 10},
    {"covered": 11, "total": 10},
    {"covered": True, "total": 10},
    {"covered": 1, "total": False},
    {"covered": "1", "total": 10},
    {"covered": 1, "total": "10"},
    {"covered": 1.0, "total": 10},
])
def test_coverage_rejects_unmeasurable_mixed_counts_bounds_and_coercion(counts):
    with pytest.raises(ValidationError):
        CoverageMeasure(**counts, basis="Declared code statements")


@pytest.mark.parametrize("payload", [
    {"covered": None, "total": None},
    {"covered": 0, "basis": "Declared code statements"},
    {"total": 0, "basis": "Declared code statements"},
    {"covered": 0, "total": 0, "basis": " \n "},
])
def test_coverage_requires_counts_and_explanatory_basis(payload):
    with pytest.raises(ValidationError):
        CoverageMeasure.model_validate(payload)


def test_coverage_containers_require_every_declared_measure():
    measure = unavailable_coverage()
    assert SystemCoverage(code=measure).model_dump() == {"code": measure}
    assert SystemReport(session_id="s-1", coverage={"code": measure}).model_dump() == {
        "session_id": "s-1", "coverage": {"code": measure}
    }
    with pytest.raises(ValidationError):
        SystemCoverage()
    with pytest.raises(ValidationError):
        SuiteCoverage(code=measure, component=measure)


@pytest.mark.parametrize("source", [None, "database", "", 1])
def test_error_results_require_a_classified_source(source):
    payload = {"test_id": "case-1", "outcome": "error", "message": "Case failed to execute"}
    with pytest.raises(ValidationError):
        Result.model_validate({**payload, "source": source})
    if source is None:
        with pytest.raises(ValidationError):
            Result.model_validate(payload)


@pytest.mark.parametrize("outcome", ["pass", "fail", "skip"])
def test_non_error_results_omit_only_absent_source(outcome):
    payload = {"test_id": "case-1", "outcome": outcome, "message": "Case completed"}
    result = Result.model_validate({**payload, "source": None})
    assert result.model_dump() == payload
    assert json.loads(result.model_dump_json()) == payload
    assert Result.model_validate({**payload, "source": "tester"}).model_dump() == {
        **payload, "source": "tester"
    }


def test_result_outcomes_and_protocol_error_sources_use_the_wire_enumerations():
    for source in ("tester", "system", "communication", "unknown"):
        assert ProtocolErrorBody(message="Request failed", source=source).source == source
    with pytest.raises(ValidationError):
        ProtocolErrorBody(message="Request failed", source="database")
    with pytest.raises(ValidationError):
        Result(test_id="case-1", outcome="success", message="Case completed")


def test_suite_report_rejects_duplicate_or_blank_result_ids():
    payload = report_payload()
    payload["results"][1]["test_id"] = payload["results"][0]["test_id"]
    with pytest.raises(ValidationError, match="unique"):
        SuiteReport.model_validate(payload)
    payload["results"][1]["test_id"] = " \t "
    with pytest.raises(ValidationError):
        SuiteReport.model_validate(payload)


@pytest.mark.parametrize(("model", "payload"), [
    (SystemSetupRequest, {"session_id": " \n ", "setup": {}}),
    (RunRequest, {"session_id": ""}),
    (SetupRequest, {"target_url": " \t "}),
    (SessionResponse, {"session_id": ""}),
    (ProtocolErrorBody, {"message": " \n ", "source": "tester"}),
])
def test_requests_and_response_text_reject_blank_values(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(("model", "payload"), [
    (SystemSetupRequest, {"session_id": "session-1", "setup": {"seed": 12}}),
    (RunRequest, {"session_id": "session-1"}),
    (SetupRequest, {"target_url": "https://target.example.test"}),
])
def test_setup_and_run_requests_reject_unknown_fields_without_rewriting_values(model, payload):
    assert model.model_validate(payload).model_dump() == payload
    with pytest.raises(ValidationError):
        model.model_validate({**payload, "unexpected": "value"})


def test_openapi_schema_keeps_nullable_coverage_counts_required():
    schema = SuiteReport.model_json_schema()
    measure = schema["$defs"]["CoverageMeasure"]
    assert set(measure["required"]) == {"covered", "total", "basis"}
    assert {part.get("type") for part in measure["properties"]["covered"]["anyOf"]} == {
        "integer", "null"
    }
