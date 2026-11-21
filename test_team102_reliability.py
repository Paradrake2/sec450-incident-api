"""Retry and concurrency checks using synthetic incidents and disposable stores."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from main import Base, IncidentRow, create_incident_app, initialize_database, team102_source_index
from testing_database import disposable_engine


PAYLOAD = {
    "title": "Synthetic ransomware report",
    "log": "Possible ransomware on synthetic file server",
    "occurredAt": "2026-11-10T14:00:00Z",
    "source": "team102:report:501",
}


@pytest.fixture
def incident_store(tmp_path):
    with disposable_engine(tmp_path) as database:
        yield database, create_incident_app(database)


@pytest.mark.parametrize("source", [
    "team102:report:501", "team102-fixture:report:501",
])
def test_retry_returns_current_incident_without_replacing_evidence_or_history(incident_store, source):
    _, application = incident_store
    payload = {**PAYLOAD, "source": source}
    with TestClient(application) as client:
        first = client.post("/api/v1/incidents", json=payload)
        assert first.status_code == 201
        ident = first.json()["id"]
        assert client.post(f"/api/v1/incidents/{ident}/assessment").status_code == 200
        assert client.post(f"/api/v1/incidents/{ident}/review", json={
            "decision": "MATERIAL", "reviewer": "synthetic-reviewer",
        }).status_code == 200
        assert client.post(f"/api/v1/incidents/{ident}/disclosure").status_code == 200
        current = client.get(f"/api/v1/incidents/{ident}").json()
        reviews = client.get(f"/api/v1/incidents/{ident}/reviews").json()
        drafts = client.get(f"/api/v1/incidents/{ident}/disclosures").json()

        duplicate = client.post("/api/v1/incidents", json={
            **payload, "title": "Changed report title", "log": "Changed source evidence",
            "occurredAt": "2026-11-11T14:00:00Z",
        })
        assert duplicate.status_code == 200
        assert duplicate.json() == current
        assert current["state"] == "MATERIAL_CONFIRMED"
        assert client.get("/api/v1/incidents").json() == [current]
        assert client.get(f"/api/v1/incidents/{ident}/reviews").json() == reviews
        assert client.get(f"/api/v1/incidents/{ident}/disclosures").json() == drafts


def test_retry_after_restart_returns_the_same_persisted_incident(incident_store):
    database, application = incident_store
    with TestClient(application) as client:
        first = client.post("/api/v1/incidents", json=PAYLOAD)
        assert first.status_code == 201

    # A fresh application has no shared in-memory registry with the first one.
    with TestClient(create_incident_app(database)) as restarted:
        duplicate = restarted.post("/api/v1/incidents", json=PAYLOAD)
        assert duplicate.status_code == 200
        assert duplicate.json() == first.json()
        assert restarted.get("/api/v1/incidents").json() == [first.json()]


@pytest.mark.parametrize("source", [None, "syslog", "synthetic-import", "TEAM102:REPORT:501"])
def test_general_incident_sources_can_be_reused(incident_store, source):
    _, application = incident_store
    payload = {key: value for key, value in PAYLOAD.items() if key != "source"}
    if source is not None:
        payload["source"] = source
    with TestClient(application) as client:
        responses = [client.post("/api/v1/incidents", json=payload) for _ in range(2)]
        assert [response.status_code for response in responses] == [201, 201]
        assert responses[0].json()["id"] != responses[1].json()["id"]
        assert len(client.get("/api/v1/incidents").json()) == 2


def test_report_ids_and_fixture_namespace_remain_distinct(incident_store):
    _, application = incident_store
    sources = ["team102:report:501", "team102:report:502", "team102-fixture:report:501"]
    with TestClient(application) as client:
        created = [client.post("/api/v1/incidents", json={**PAYLOAD, "source": source})
                   for source in sources]
        assert [response.status_code for response in created] == [201, 201, 201]
        assert len({response.json()["id"] for response in created}) == 3
        assert {row["source"] for row in client.get("/api/v1/incidents").json()} == set(sources)


def test_database_rejects_duplicate_report_sources_even_outside_the_api(incident_store):
    database, application = incident_store
    with TestClient(application) as client:
        assert client.post("/api/v1/incidents", json=PAYLOAD).status_code == 201
        with Session(database) as session:
            original = session.scalar(select(IncidentRow))
            duplicate = IncidentRow(**{
                column.name: getattr(original, column.name)
                for column in IncidentRow.__table__.columns
            })
            duplicate.id = "different-synthetic-incident"
            session.add(duplicate)
            with pytest.raises(IntegrityError):
                session.commit()
            session.rollback()
        assert len(client.get("/api/v1/incidents").json()) == 1


def test_simultaneous_report_creates_commit_one_incident(incident_store):
    database, application = incident_store
    both_inserting = Barrier(2, timeout=10)

    def synchronize_insert(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO INCIDENTS"):
            both_inserting.wait()

    with TestClient(application) as client:
        event.listen(database, "before_cursor_execute", synchronize_insert)
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(client.post, "/api/v1/incidents", json=PAYLOAD)
                           for _ in range(2)]
                responses = [future.result(timeout=20) for future in futures]
        finally:
            event.remove(database, "before_cursor_execute", synchronize_insert)
        assert sorted(response.status_code for response in responses) == [200, 201]
        assert responses[0].json() == responses[1].json()
        assert client.get("/api/v1/incidents").json() == [responses[0].json()]


def test_failed_commit_leaves_no_incident_and_the_next_attempt_can_create_it(incident_store):
    database, application = incident_store

    def reject_commit(session):
        if session.get_bind() is database:
            session.flush()
            raise RuntimeError("synthetic commit failure")

    with TestClient(application, raise_server_exceptions=False) as client:
        event.listen(Session, "before_commit", reject_commit)
        try:
            failed = client.post("/api/v1/incidents", json=PAYLOAD)
        finally:
            event.remove(Session, "before_commit", reject_commit)
        assert failed.status_code == 500
        assert "synthetic commit failure" not in failed.text
        assert client.get("/api/v1/incidents").json() == []
        retried = client.post("/api/v1/incidents", json=PAYLOAD)
        assert retried.status_code == 201
        duplicate = client.post("/api/v1/incidents", json=PAYLOAD)
        assert duplicate.status_code == 200
        assert duplicate.json() == retried.json()


def test_explicit_import_engine_ignores_unrelated_application_database_url(tmp_path):
    # A fresh interpreter detects accidental imports of main's configured engine.
    script = """
from datetime import datetime, timezone
from pathlib import Path
import sys
from sqlalchemy import create_engine
from incident_store import IncidentRow
from migration_tools import upgrade_database
from team102_contract import ReportFetch, report_url
from team102_import import import_incident

class SyntheticClient:
    def fetch_report(self, report_id):
        return ReportFetch(
            report_id=report_id,
            raw_content=b'{"report_id":501,"body":{"summary":"Synthetic report","timeline":[{"ts":"2026-11-10T14:00:00Z"}]}}',
            fetched_at=datetime.now(timezone.utc), source_url=report_url(report_id),
        )

database = create_engine("sqlite:///" + Path(sys.argv[1]).as_posix())
try:
    upgrade_database(database)
    first = import_incident(database, 501, client=SyntheticClient())
    duplicate = import_incident(database, 501, client=SyntheticClient())
    assert first.created and not duplicate.created
    assert first.incident_id == duplicate.incident_id
    assert "main" not in sys.modules
finally:
    database.dispose()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "supplied.db")],
        cwd=Path(__file__).resolve().parent,
        env={**os.environ, "DATABASE_URL": "invalid unrelated database URL"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("definition", [
    "CREATE INDEX uq_incidents_team102_source ON incidents (source)",
    "CREATE UNIQUE INDEX uq_incidents_team102_source ON incidents (source)",
    "CREATE UNIQUE INDEX uq_incidents_team102_source ON incidents (lower(source))",
])
def test_startup_refuses_a_malformed_existing_identity_index(tmp_path, definition):
    with disposable_engine(tmp_path) as database:
        Base.metadata.create_all(database)
        team102_source_index.drop(database)
        with database.begin() as connection:
            connection.exec_driver_sql(definition)
        with pytest.raises(ValueError, match="identity index definition differs"):
            initialize_database(database)
