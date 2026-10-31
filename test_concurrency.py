"""Real PostgreSQL races for incident state transitions and review history."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

# Import the disposable test database bootstrap before importing the application.
from test_api import TestClient, app, engine, new_incident
import main


pytestmark = pytest.mark.skipif(
    engine.dialect.name != "postgresql",
    reason="State-transition race checks require PostgreSQL transactions",
)


def synchronize_incident_reads(monkeypatch, incident_id):
    """Make both requests read the original state before either can update it."""
    original = main.get_incident
    both_read = Barrier(2, timeout=10)

    def get_after_both_read(session, ident):
        row = original(session, ident)
        if ident == incident_id:
            both_read.wait()
        return row

    monkeypatch.setattr(main, "get_incident", get_after_both_read)


def test_concurrent_assessments_accept_one_transition(monkeypatch):
    with TestClient(app) as client:
        assert client.post("/api/v1/test/reset").status_code == 204
        created = new_incident(client, "Possible ransomware on production servers")
        assert created.status_code == 201
        incident_id = created.json()["id"]

        with monkeypatch.context() as patch:
            synchronize_incident_reads(patch, incident_id)
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [
                    executor.submit(client.post, f"/api/v1/incidents/{incident_id}/assessment")
                    for _ in range(2)
                ]
                responses = [future.result(timeout=20) for future in futures]

        assert sorted(response.status_code for response in responses) == [200, 409]
        accepted = next(response for response in responses if response.status_code == 200)
        rejected = next(response for response in responses if response.status_code == 409)
        assert rejected.json()["error"] == "INVALID_STATE"
        stored = client.get(f"/api/v1/incidents/{incident_id}")
        assert stored.status_code == 200
        assert stored.json()["assessment"] == accepted.json()
        assert stored.json()["state"] == "POTENTIALLY_MATERIAL"
        assert stored.json()["review"] is None


def test_concurrent_material_reviews_accept_one_decision_and_history_row(monkeypatch):
    with TestClient(app) as client:
        assert client.post("/api/v1/test/reset").status_code == 204
        created = new_incident(client)
        assert created.status_code == 201
        incident_id = created.json()["id"]
        assessment = client.post(f"/api/v1/incidents/{incident_id}/assessment")
        assert assessment.status_code == 200

        def submit_review(reviewer):
            return client.post(f"/api/v1/incidents/{incident_id}/review", json={
                "decision": "MATERIAL", "reviewer": reviewer,
                "rationale": f"Synthetic decision from {reviewer}",
            })

        with monkeypatch.context() as patch:
            synchronize_incident_reads(patch, incident_id)
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(submit_review, name) for name in ("reviewer-a", "reviewer-b")]
                responses = [future.result(timeout=20) for future in futures]

        assert sorted(response.status_code for response in responses) == [200, 409]
        accepted = next(response for response in responses if response.status_code == 200)
        rejected = next(response for response in responses if response.status_code == 409)
        assert rejected.json()["error"] == "INVALID_STATE"
        winner = accepted.json()["review"]
        stored = client.get(f"/api/v1/incidents/{incident_id}")
        assert stored.status_code == 200
        assert stored.json()["state"] == "MATERIAL_CONFIRMED"
        assert stored.json()["review"] == winner
        assert stored.json()["assessment"] == assessment.json()
        history = client.get(f"/api/v1/incidents/{incident_id}/reviews")
        assert history.status_code == 200
        assert len(history.json()) == 1
        assert {key: value for key, value in history.json()[0].items() if key != "id"} == winner
