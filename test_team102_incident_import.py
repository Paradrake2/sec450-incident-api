"""Explicit fetch-to-incident imports use synthetic HTTP and disposable stores."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier

import httpx
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from main import IncidentRow
from migration_tools import upgrade_database
from report_import_store import SourceReportObservation, SourceReportSnapshot
from team102_client import Team102Client
from team102_contract import SourceApiError
from team102_import import import_incident
from test_team102_integration import RANSOMWARE_REPORT
from testing_database import disposable_engine


@pytest.fixture
def store(tmp_path):
    with disposable_engine(tmp_path) as engine:
        upgrade_database(engine)
        yield engine


def counts(engine):
    with Session(engine) as session:
        return tuple(session.scalar(select(func.count()).select_from(model)) for model in (
            IncidentRow, SourceReportSnapshot, SourceReportObservation,
        ))


def source(report=RANSOMWARE_REPORT):
    return httpx.MockTransport(lambda request: httpx.Response(200, json=report))


def test_realistic_team102_report_object_lines_reach_incident_log(store):
    # sec450/reports.py emits raw_lines as dictionaries with text and IDs,
    # rather than the plain strings used by older synthetic test fixtures.
    report = deepcopy(RANSOMWARE_REPORT)
    report["body"]["raw_lines"] = [
        {"raw_id": 41, "batch_id": 5, "source": "ssh_auth", "text": "failed login attempt"},
        {"raw_id": 42, "batch_id": 5, "source": "ssh_auth", "text": "another failed login attempt"},
    ]
    with Team102Client("synthetic-key", transport=source(report)) as client:
        receipt = import_incident(store, 501, client=client)
    assert receipt.created
    assert counts(store) == (1, 1, 1)
    with Session(store) as session:
        incident = session.get(IncidentRow, receipt.incident_id)
        assert incident.source == "team102:report:501"
        assert incident.log == "failed login attempt\\nanother failed login attempt".replace("\\n", "\n")
        snapshot = session.get(SourceReportSnapshot, receipt.report.snapshot_id)
        assert json.loads(snapshot.original_content)["body"]["raw_lines"] == report["body"]["raw_lines"]


def test_fetch_convert_create_preserves_raw_report_and_retry_state(store):
    with Team102Client("synthetic-key", transport=source()) as client:
        first = import_incident(store, 501, client=client)
        with Session(store) as session, session.begin():
            row = session.get(IncidentRow, first.incident_id)
            assert row.source == "team102:report:501"
            assert row.title == RANSOMWARE_REPORT["body"]["summary"]
            row.state = "UNDER_REVIEW"
        store.dispose()  # new connections still read persisted identity and state
        second = import_incident(store, 501, client=client)
    assert first.created and not second.created
    assert first.incident_id == second.incident_id
    assert first.report.snapshot_id == second.report.snapshot_id
    assert first.report.observation_id != second.report.observation_id
    assert counts(store) == (1, 1, 2)
    with Session(store) as session:
        assert session.get(IncidentRow, first.incident_id).state == "UNDER_REVIEW"
        snapshot = session.get(SourceReportSnapshot, first.report.snapshot_id)
        assert json.loads(snapshot.original_content) == RANSOMWARE_REPORT


@pytest.mark.parametrize("failure,kind", [
    (401, "authentication"), (403, "authentication"), (404, "not_found"),
    (429, "rate_limit"), (500, "upstream"), (503, "upstream"),
    (httpx.ConnectError, "transport"), (httpx.ReadTimeout, "timeout"),
])
def test_upstream_failure_leaves_no_partial_rows_and_can_retry(store, failure, kind):
    calls = []

    def fail(request):
        calls.append(request)
        if isinstance(failure, int):
            return httpx.Response(failure, text="private upstream payload", headers={"Retry-After": "3"})
        raise failure("private credentials and URL", request=request)

    with Team102Client("synthetic-key", transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(SourceApiError) as raised:
            import_incident(store, 501, client=client)
    assert raised.value.kind == kind
    assert "private" not in str(raised.value)
    assert len(calls) == 1  # callers choose when to retry
    assert counts(store) == (0, 0, 0)
    with Team102Client("synthetic-key", transport=source()) as client:
        assert import_incident(store, 501, client=client).created
    assert counts(store) == (1, 1, 1)


@pytest.mark.parametrize("body", [
    {}, {"summary": "Missing time"},
    {"summary": {"unexpected": "object"}},
    {"summary": "Bad timeline", "timeline": 42},
    {"summary": "Bad time", "timeline": [{"ts": "not-a-time"}]},
    {"summary": " ", "raw_lines": ["  "], "timeline": [{"ts": "2026-11-10T14:00:00Z"}]},
    {"summary": "Malformed log object", "raw_lines": [{"raw_id": 3}],
     "timeline": [{"ts": "2026-11-10T14:00:00Z"}]},
])
def test_unconvertible_report_leaves_no_source_or_incident_rows(store, body):
    with Team102Client("synthetic-key", transport=source({"report_id": 501, "body": body})) as client:
        with pytest.raises(SourceApiError, match="cannot be converted"):
            import_incident(store, 501, client=client)
    assert counts(store) == (0, 0, 0)


def test_wrong_upstream_report_id_leaves_no_rows(store):
    wrong = {**RANSOMWARE_REPORT, "report_id": 999}
    with Team102Client("synthetic-key", transport=source(wrong)) as client:
        with pytest.raises(SourceApiError):
            import_incident(store, 501, client=client)
    assert counts(store) == (0, 0, 0)


@pytest.mark.parametrize("table", ["incidents", "source_report_observations"])
def test_storage_failure_rolls_back_evidence_and_incident_then_retry_succeeds(store, table):
    def fail_write(connection, cursor, statement, parameters, context, executemany):
        if statement.lower().startswith("insert into " + table):
            raise RuntimeError("synthetic write failure")

    event.listen(store, "before_cursor_execute", fail_write)
    try:
        with Team102Client("synthetic-key", transport=source()) as client:
            with pytest.raises(RuntimeError, match="synthetic write failure"):
                import_incident(store, 501, client=client)
    finally:
        event.remove(store, "before_cursor_execute", fail_write)
    assert counts(store) == (0, 0, 0)
    with Team102Client("synthetic-key", transport=source()) as client:
        first = import_incident(store, 501, client=client)
        second = import_incident(store, 501, client=client)
    assert first.created and not second.created
    assert counts(store) == (1, 1, 2)


def test_commit_failure_rolls_back_the_whole_import(store):
    def reject_commit(session):
        if session.get_bind() is store:
            session.flush()
            raise RuntimeError("synthetic commit failure")

    event.listen(Session, "before_commit", reject_commit)
    try:
        with Team102Client("synthetic-key", transport=source()) as client:
            with pytest.raises(RuntimeError, match="synthetic commit failure"):
                import_incident(store, 501, client=client)
    finally:
        event.remove(Session, "before_commit", reject_commit)
    assert counts(store) == (0, 0, 0)
    with Team102Client("synthetic-key", transport=source()) as client:
        assert import_incident(store, 501, client=client).created
    assert counts(store) == (1, 1, 1)


def test_simultaneous_imports_reuse_incident_and_snapshot(store):
    ready = Barrier(2, timeout=10)

    def synchronize(connection, cursor, statement, parameters, context, executemany):
        if statement.lower().startswith("insert into source_report_snapshots"):
            ready.wait()

    def perform_import():
        with Team102Client("synthetic-key", transport=source()) as client:
            return import_incident(store, 501, client=client)

    event.listen(store, "before_cursor_execute", synchronize)
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(perform_import) for _ in range(2)]
            receipts = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(store, "before_cursor_execute", synchronize)
    assert sorted(receipt.created for receipt in receipts) == [False, True]
    assert receipts[0].incident_id == receipts[1].incident_id
    assert receipts[0].report.snapshot_id == receipts[1].report.snapshot_id
    assert counts(store) == (1, 1, 2)


def test_changed_report_keeps_new_evidence_without_overwriting_incident(store):
    with Team102Client("synthetic-key", transport=source()) as client:
        first = import_incident(store, 501, client=client)
    changed = deepcopy(RANSOMWARE_REPORT)
    changed["body"]["summary"] = "Changed source summary"
    with Team102Client("synthetic-key", transport=source(changed)) as client:
        second = import_incident(store, 501, client=client)
    assert not second.created and second.incident_id == first.incident_id
    assert second.report.created
    assert counts(store) == (1, 2, 2)
    with Session(store) as session:
        assert session.get(IncidentRow, first.incident_id).title == RANSOMWARE_REPORT["body"]["summary"]


def test_fixture_and_api_create_distinct_incidents_and_snapshots(store, tmp_path):
    fixture = tmp_path / "synthetic-report.json"
    fixture.write_text(json.dumps(RANSOMWARE_REPORT), encoding="utf-8")
    first = import_incident(store, 501, fixture=fixture)
    again = import_incident(store, 501, fixture=fixture)
    with Team102Client("synthetic-key", transport=source()) as client:
        live = import_incident(store, 501, client=client)
    assert first.created and live.created and not again.created
    assert first.incident_id == again.incident_id != live.incident_id
    assert counts(store) == (2, 2, 3)
    with Session(store) as session:
        assert session.get(IncidentRow, first.incident_id).source == "team102-fixture:report:501"
        assert session.get(SourceReportSnapshot, first.report.snapshot_id).source_system.endswith("-fixture")


def test_cli_create_incident_returns_receipt_and_safe_retry(store, tmp_path, monkeypatch, capsys):
    import team102_import

    fixture = tmp_path / "synthetic-report.json"
    fixture.write_text(json.dumps(RANSOMWARE_REPORT), encoding="utf-8")
    monkeypatch.setattr(team102_import, "dotenv_values", lambda path: {})
    monkeypatch.setenv("DATABASE_URL", store.url.render_as_string(hide_password=False))
    monkeypatch.setattr(team102_import, "create_engine", lambda *args, **kwargs: store)
    args = ["--report-id", "501", "--fixture", str(fixture), "--create-incident"]
    assert team102_import.main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert team102_import.main(args) == 0
    again = json.loads(capsys.readouterr().out)
    assert first["status"] == "imported" and again["status"] == "already_present"
    assert first["incidentId"] == again["incidentId"]
    assert first["snapshotId"] == again["snapshotId"]
    assert counts(store) == (1, 1, 2)
