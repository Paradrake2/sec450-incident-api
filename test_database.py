"""Database-selection regressions using disposable local stores only."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
from json import dumps, loads

import pytest
from fastapi import Request
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session
from starlette.datastructures import State

# Import the existing test bootstrap first, never the developer database.
from test_api import TestClient, app, new_incident
import main
from testing_database import disposable_engine


@pytest.fixture
def stores(tmp_path, monkeypatch):
    with ExitStack() as stack:
        databases = {
            name: stack.enter_context(disposable_engine(tmp_path))
            for name in ("normal", "alternate", "other")
        }
        # Preserve the old tests' imported engine while keeping these tests local.
        monkeypatch.setattr(main, "engine", databases["normal"])
        monkeypatch.setattr(app, "state", State({"database_engine": databases["normal"]}))
        yield databases


def snapshot(database):
    """Capture every persisted field, including histories and old snapshots."""
    with database.connect() as connection:
        return {
            table.name: connection.execute(
                select(table).order_by(*table.primary_key.columns)
            ).mappings().all()
            for table in main.Base.metadata.sorted_tables
        }


def seed_incident(database, ident, *, reviewer=None, related=()):
    now = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)
    review = None if reviewer is None else dumps({
        "decision": "NOT_MATERIAL", "reviewer": reviewer,
        "rationale": "Synthetic legacy decision", "reviewedAt": now.isoformat(),
    })
    with Session(database) as session:
        session.add(main.IncidentRow(
            id=ident, title=f"Synthetic {ident}", log="Routine synthetic log",
            occurred_at=now, created_at=now, source="local-test",
            state="NOT_MATERIAL" if reviewer else "RECEIVED",
            review=review, related=dumps(list(related)),
        ))
        session.commit()


def test_alternate_store_full_workflow_and_reset_preserve_normal_data(stores):
    normal, alternate = stores["normal"], stores["alternate"]
    main.initialize_database(normal)
    seed_incident(normal, "normal-sentinel")
    before = snapshot(normal)
    app.state.database_engine = alternate

    with TestClient(app) as client:
        first = new_incident(client, "Possible ransomware on production servers")
        second = new_incident(client, "Routine server event")
        assert first.status_code == second.status_code == 201
        assert set(first.json()) == {
            "id", "title", "log", "occurredAt", "createdAt", "source", "state",
            "assessment", "review", "relatedIncidentIds",
        }
        a, b = first.json()["id"], second.json()["id"]
        assert {row["id"] for row in client.get("/api/v1/incidents").json()} == {a, b}
        assert client.get(f"/api/v1/incidents/{a}").json() == first.json()
        assert client.post(f"/api/v1/incidents/{a}/assessment").json()["potentiallyMaterial"] is True
        assert client.post(f"/api/v1/incidents/{b}/assessment").json()["potentiallyMaterial"] is False
        for decision in ("NOT_MATERIAL", "MATERIAL"):
            response = client.post(f"/api/v1/incidents/{a}/review", json={
                "decision": decision, "reviewer": "local-reviewer",
            })
            assert response.status_code == 200
        history = client.get(f"/api/v1/incidents/{a}/reviews")
        assert history.status_code == 200
        assert [row["decision"] for row in history.json()] == ["NOT_MATERIAL", "MATERIAL"]
        assert [row["id"] for row in client.get(
            "/api/v1/incidents", params={"state": "MATERIAL_CONFIRMED"}
        ).json()] == [a]
        relation = client.post(f"/api/v1/incidents/{a}/related", json={"relatedIncidentId": b})
        assert relation.status_code == 200
        assert relation.json()["relatedIncidentIds"] == [b]
        assert client.get(f"/api/v1/incidents/{b}").json()["relatedIncidentIds"] == [a]
        for _ in range(2):
            draft = client.post(f"/api/v1/incidents/{a}/disclosure")
            assert draft.status_code == 200
            assert set(draft.json()) == {"incidentId", "draft", "generatedAt", "final"}
            assert draft.json()["final"] is False
        drafts = client.get(f"/api/v1/incidents/{a}/disclosures")
        assert drafts.status_code == 200
        assert len({row["id"] for row in drafts.json()}) == 2
        assert snapshot(normal) == before
        reset = client.post("/api/v1/test/reset")
        assert reset.status_code == 204 and not reset.content
        assert client.get("/api/v1/incidents").json() == []
        assert all(not rows for rows in snapshot(alternate).values())
        assert snapshot(normal) == before


def test_cross_store_ids_cannot_read_link_or_mutate_normal_incidents(stores):
    normal, alternate = stores["normal"], stores["alternate"]
    for database in (normal, alternate):
        main.initialize_database(database)
    seed_incident(normal, "normal-only")
    seed_incident(alternate, "alternate-only")
    before = snapshot(normal)
    app.state.database_engine = alternate

    with TestClient(app) as client:
        responses = [
            client.get("/api/v1/incidents/normal-only"),
            client.get("/api/v1/incidents/normal-only/reviews"),
            client.get("/api/v1/incidents/normal-only/disclosures"),
            client.post("/api/v1/incidents/normal-only/assessment"),
            client.post("/api/v1/incidents/normal-only/review", json={
                "decision": "MATERIAL", "reviewer": "local-test",
            }),
            client.post("/api/v1/incidents/normal-only/disclosure"),
            client.post("/api/v1/incidents/normal-only/related", json={
                "relatedIncidentId": "alternate-only",
            }),
            client.post("/api/v1/incidents/alternate-only/related", json={
                "relatedIncidentId": "normal-only",
            }),
        ]
        assert [response.status_code for response in responses] == [404] * len(responses)
        assert all(response.json()["error"] == "INCIDENT_NOT_FOUND" for response in responses)
        assert client.get("/api/v1/incidents/alternate-only").json()["relatedIncidentIds"] == []
        assert snapshot(normal) == before


def test_startup_creates_tables_only_in_selected_store(stores):
    assert inspect(stores["normal"]).get_table_names() == []
    assert inspect(stores["alternate"]).get_table_names() == []
    app.state.database_engine = stores["alternate"]
    with TestClient(app) as client:
        assert client.get("/api/v1/incidents").json() == []
        assert set(inspect(stores["alternate"]).get_table_names()) == set(main.Base.metadata.tables)
        assert inspect(stores["normal"]).get_table_names() == []


def test_startup_backfills_selected_store_without_touching_normal_legacy_data(stores):
    normal, alternate = stores["normal"], stores["alternate"]
    for database, reviewer in ((normal, "normal-reviewer"), (alternate, "alternate-reviewer")):
        main.Base.metadata.create_all(database)
        # Matching IDs ensure a mistaken global read cannot hide behind UUIDs.
        seed_incident(database, "legacy-a", reviewer=reviewer, related=("legacy-b",))
        seed_incident(database, "legacy-b")
    normal_before = snapshot(normal)
    alternate_before = snapshot(alternate)["incidents"]
    app.state.database_engine = alternate

    for _ in range(2):
        with TestClient(app) as client:
            reviews = client.get("/api/v1/incidents/legacy-a/reviews").json()
            assert len(reviews) == 1
            assert reviews[0]["reviewer"] == "alternate-reviewer"
            assert client.get("/api/v1/incidents/legacy-a").json()["relatedIncidentIds"] == ["legacy-b"]
            assert client.get("/api/v1/incidents/legacy-b").json()["relatedIncidentIds"] == ["legacy-a"]
        assert snapshot(normal) == normal_before
        assert snapshot(alternate)["incidents"] == alternate_before
    # Explicit maintenance calls must honor the same selected bind as startup.
    main.backfill_legacy_reviews(alternate)
    main.backfill_legacy_relations(alternate)
    after = snapshot(alternate)
    assert len(after["incident_reviews"]) == len(after["incident_relations"]) == 1
    assert loads(after["incidents"][0]["review"])["reviewer"] == "alternate-reviewer"
    assert snapshot(normal) == normal_before


def test_request_dependency_routes_concurrent_calls_without_global_engine_switch(stores, monkeypatch):
    for database in stores.values():
        main.initialize_database(database)
    seed_incident(stores["normal"], "normal-sentinel")
    normal_before = snapshot(stores["normal"])

    def select_local_store(request: Request):
        # A test-only selector, not an application or pod routing contract.
        return stores[request.headers.get("X-Local-Store", "normal")]

    monkeypatch.setitem(app.dependency_overrides, main.get_engine, select_local_store)
    with TestClient(app) as client:
        def create_in(name):
            response = client.post("/api/v1/incidents", headers={"X-Local-Store": name}, json={
                "title": f"Synthetic {name}", "log": "Routine synthetic event",
                "occurredAt": "2026-10-31T12:00:00Z",
            })
            assert response.status_code == 201
            return name, response.json()["id"]

        with ThreadPoolExecutor(max_workers=4) as executor:
            created = list(executor.map(create_in, ["alternate", "other"] * 3))
        for name in ("alternate", "other"):
            selected_ids = {ident for store, ident in created if store == name}
            response = client.get("/api/v1/incidents", headers={"X-Local-Store": name})
            assert response.status_code == 200
            assert {row["id"] for row in response.json()} == selected_ids
        assert [row["id"] for row in client.get("/api/v1/incidents").json()] == ["normal-sentinel"]
        assert main.engine is app.state.database_engine is stores["normal"]
        assert snapshot(stores["normal"]) == normal_before


def test_failed_review_rolls_back_flushed_changes_and_closes_request_session(stores, monkeypatch):
    alternate = stores["alternate"]
    main.initialize_database(alternate)
    app.state.database_engine = alternate
    opened = []
    fail_review = True

    class TrackingSession(Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.was_closed = False
            opened.append(self)

        def commit(self):
            nonlocal fail_review
            if fail_review and any(isinstance(item, main.ReviewRow) for item in self.new):
                # Fail after both the review insert and incident update reached
                # the database, proving cleanup rolls back the whole mutation.
                self.flush()
                fail_review = False
                raise RuntimeError("synthetic commit failure")
            super().commit()

        def close(self):
            super().close()
            self.was_closed = True

    monkeypatch.setattr(main, "Session", TrackingSession)
    with TestClient(app, raise_server_exceptions=False) as client:
        ident = new_incident(client).json()["id"]
        assert client.post(f"/api/v1/incidents/{ident}/assessment").status_code == 200
        before = client.get(f"/api/v1/incidents/{ident}").json()
        review = {"decision": "MATERIAL", "reviewer": "local-reviewer"}
        failed = client.post(f"/api/v1/incidents/{ident}/review", json=review)
        assert failed.status_code == 500
        assert failed.json() == {"error": "INTERNAL_ERROR", "message": "Unexpected server-side failure"}
        assert client.get(f"/api/v1/incidents/{ident}").json() == before
        assert client.get(f"/api/v1/incidents/{ident}/reviews").json() == []
        assert client.post(f"/api/v1/incidents/{ident}/review", json=review).status_code == 200
        assert len(client.get(f"/api/v1/incidents/{ident}/reviews").json()) == 1
    assert all(session.was_closed and not session.in_transaction() for session in opened)
    assert len({id(session) for session in opened}) == len(opened)
    assert alternate.pool.checkedout() == 0


def test_database_dependencies_do_not_add_wire_parameters():
    for path in app.openapi()["paths"].values():
        for operation in path.values():
            if isinstance(operation, dict):
                names = {parameter["name"] for parameter in operation.get("parameters", [])}
                assert not names.intersection({"session", "database_engine", "request"})
