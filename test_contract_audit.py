"""Independent contract reporting is exercised only against an isolated app."""

from collections import Counter
import json
import os

from fastapi.testclient import TestClient
import httpx
import pytest

from contract_audit import render_markdown, run_audit
from main import app


EXPECTED_OPERATIONS = {
    ("GET", "/api/v1/health"): [],
    ("POST", "/api/v1/test/reset"): ["REQ-08"],
    ("POST", "/api/v1/incidents"): ["REQ-01", "REQ-04", "REQ-08"],
    ("GET", "/api/v1/incidents"): ["REQ-02"],
    ("GET", "/api/v1/incidents/{incidentId}"): ["REQ-02", "REQ-04"],
    ("POST", "/api/v1/incidents/{incidentId}/assessment"): [
        "REQ-03",
        "REQ-04",
        "REQ-08",
    ],
    ("POST", "/api/v1/incidents/{incidentId}/review"): ["REQ-04", "REQ-05", "REQ-08"],
    ("POST", "/api/v1/incidents/{incidentId}/related"): ["REQ-06", "REQ-08"],
    ("POST", "/api/v1/incidents/{incidentId}/disclosure"): [
        "REQ-05",
        "REQ-07",
        "REQ-08",
    ],
}


@pytest.fixture
def audit_client():
    with TestClient(app) as client:
        yield client


def operation_rows(report):
    rows = {(row["method"], row["path"]): row for row in report["operations"]}
    assert len(rows) == len(report["operations"]) == len(EXPECTED_OPERATIONS)
    assert set(rows) == set(EXPECTED_OPERATIONS)
    return rows


def test_report_accounts_for_every_common_operation_and_exact_requirements(
    audit_client,
):
    report = run_audit(audit_client)
    assert report["scope"] == "local_contract_audit"
    assert report["conformanceEstablished"] is False
    rows = operation_rows(report)
    for operation, requirements in EXPECTED_OPERATIONS.items():
        row = rows[operation]
        assert set(row["requirements"]) == set(requirements)
        assert len(row["requirements"]) == len(requirements)
        assert row["implemented"] in {"yes", "partial", "not_verified"}
        assert row["successConformance"] in {"pass", "fail", "partial", "not_tested"}
        assert row["errorConformance"] in {"pass", "fail", "partial", "not_tested"}
        assert isinstance(row["notes"], list)
        assert isinstance(row["testedErrorStatuses"], list)
        assert isinstance(row["unverifiedErrorStatuses"], list)
        for check in row["checks"]:
            assert check["id"]
            assert check["kind"] in {"success", "error"}
            assert type(check["expectedStatus"]) is int
            assert check["actualStatus"] is None or type(check["actualStatus"]) is int
            assert check["outcome"] in {"pass", "fail", "error"}
            assert isinstance(check["problems"], list)


def test_summary_counts_observed_checks_without_inventing_passes(audit_client):
    report = run_audit(audit_client)
    counts = Counter(
        check["outcome"] for row in report["operations"] for check in row["checks"]
    )
    assert report["summary"] == {
        "passed": counts["pass"],
        "failed": counts["fail"],
        "errors": counts["error"],
    }
    assert sum(counts.values()) > 0
    historical = report["historicalExample"]
    assert historical["status"] in {"matched", "mismatch", "unverified"}
    assert historical["expectedPotentiallyMaterial"] is True
    assert (
        historical["observedPotentiallyMaterial"] is None
        or type(historical["observedPotentiallyMaterial"]) is bool
    )
    assert historical["note"]


def test_unexercised_server_errors_and_missing_reset_fixtures_are_explicit(
    audit_client,
):
    rows = operation_rows(run_audit(audit_client))
    health = rows[("GET", "/api/v1/health")]
    assert 503 in {int(status) for status in health["unverifiedErrorStatuses"]}
    reset = rows[("POST", "/api/v1/test/reset")]
    assert reset["successConformance"] == "partial"
    assert reset["notes"]
    for row in rows.values():
        tested = {int(status) for status in row["testedErrorStatuses"]}
        unverified = {int(status) for status in row["unverifiedErrorStatuses"]}
        assert 500 not in tested
        if 500 in unverified:
            assert row["errorConformance"] != "pass"


class MutatingClient:
    """Modify a chosen response without making any real network request."""

    def __init__(self, client, *, broken_health=False, broken_create=False):
        self.client = client
        self.broken_health = broken_health
        self.broken_create = broken_create

    def request(self, method, path, **kwargs):
        request = httpx.Request(method, "http://testserver" + path)
        if self.broken_health and method.upper() == "GET" and path == "/api/v1/health":
            return httpx.Response(
                200, json={"status": "PRIVATE_RESPONSE_VALUE"}, request=request
            )
        if (
            self.broken_create
            and method.upper() == "POST"
            and path == "/api/v1/incidents"
        ):
            return httpx.Response(
                500, json={"error": "PRIVATE_RESPONSE_VALUE"}, request=request
            )
        return self.client.request(method, path, **kwargs)


def test_schema_violation_is_detected_without_exposing_response_values(audit_client):
    report = run_audit(MutatingClient(audit_client, broken_health=True))
    row = operation_rows(report)[("GET", "/api/v1/health")]
    assert row["successConformance"] == "fail"
    assert any(
        check["outcome"] == "fail" and check["problems"] for check in row["checks"]
    )
    assert "PRIVATE_RESPONSE_VALUE" not in json.dumps(report)
    assert "PRIVATE_RESPONSE_VALUE" not in render_markdown(report)


def test_failed_creation_preserves_inventory_and_does_not_claim_dependent_success(
    audit_client,
):
    report = run_audit(MutatingClient(audit_client, broken_create=True))
    rows = operation_rows(report)
    create = rows[("POST", "/api/v1/incidents")]
    assert create["successConformance"] == "fail"
    assert report["summary"]["failed"] >= 1
    dependent = [row for (method, path), row in rows.items() if "{incidentId}" in path]
    assert all(row["successConformance"] == "not_tested" for row in dependent)
    assert report["conformanceEstablished"] is False
    assert "PRIVATE_RESPONSE_VALUE" not in json.dumps(report)


def test_markdown_lists_each_operation_and_report_serializes_as_json(audit_client):
    report = run_audit(audit_client)
    markdown = render_markdown(report)
    assert isinstance(markdown, str)
    for method, path in EXPECTED_OPERATIONS:
        assert f"{method} {path}" in markdown
    for requirements in EXPECTED_OPERATIONS.values():
        for requirement in requirements:
            assert requirement in markdown
    assert json.loads(json.dumps(report)) == report
    assert "partial" in markdown.lower()


def test_cli_rejects_remote_target_option_before_running_audit(monkeypatch):
    import contract_audit

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid remote-target options must not start an audit")

    monkeypatch.setattr(contract_audit, "run_audit", forbidden)
    with pytest.raises(SystemExit) as error:
        contract_audit.main(["--target-url", "https://example.invalid"])
    assert error.value.code == 2


@pytest.mark.parametrize("output_format", ["json", "markdown"])
def test_cli_uses_its_disposable_store_and_restores_application_selection(
    monkeypatch, capsys, output_format
):
    import contract_audit

    original_engine = app.state.database_engine
    unrelated_url = (
        "postgresql://PRIVATE_USER:PRIVATE_PASSWORD@unrelated.invalid/production"
    )
    monkeypatch.setenv("DATABASE_URL", unrelated_url)
    exit_code = contract_audit.main(["--format", output_format])
    captured = capsys.readouterr()
    assert app.state.database_engine is original_engine
    assert os.environ["DATABASE_URL"] == unrelated_url
    assert "PRIVATE_" not in captured.out + captured.err
    if output_format == "json":
        report = json.loads(captured.out)
        operation_rows(report)
        assert report["scope"] == "local_contract_audit"
        assert report["databaseBackend"] in {"sqlite", "postgresql"}
        assert exit_code == int(
            bool(report["summary"]["failed"] or report["summary"]["errors"])
        )
    else:
        for method, path in EXPECTED_OPERATIONS:
            assert f"{method} {path}" in captured.out
        assert exit_code in {0, 1}
