"""AC-05 tests: reviewer decisions, overrides, history and failure paths."""

from datetime import datetime, timezone
from json import dumps

from sqlalchemy.orm import Session

from test_api import TestClient, app, engine, new_incident  # ensures temporary DB is configured first
from main import IncidentRow, ReviewRow, backfill_legacy_reviews


def create_assessed(client, log):
    response = new_incident(client, log)
    assert response.status_code == 201
    ident = response.json()["id"]
    assert client.post(f"/api/v1/incidents/{ident}/assessment").status_code == 200
    return ident


def post_review(client, ident, decision, reviewer="legal-1", rationale=None):
    payload = {"decision": decision, "reviewer": reviewer}
    if rationale is not None:
        payload["rationale"] = rationale
    return client.post(f"/api/v1/incidents/{ident}/review", json=payload)


def test_ac05_human_not_material_overrides_positive_triage():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        ident = create_assessed(client, "Possible ransomware affecting production servers")
        assert client.get(f"/api/v1/incidents/{ident}").json()["state"] == "POTENTIALLY_MATERIAL"
        response = post_review(client, ident, "NOT_MATERIAL", rationale="Investigated, test traffic")
        assert response.status_code == 200
        incident = response.json()
        assert incident["state"] == "NOT_MATERIAL"
        assert incident["assessment"]["potentiallyMaterial"] is True
        assert incident["review"]["decision"] == "NOT_MATERIAL"
        assert incident["review"]["reviewer"] == "legal-1"
        assert incident["review"]["rationale"] == "Investigated, test traffic"
        history = client.get(f"/api/v1/incidents/{ident}/reviews")
        assert history.status_code == 200
        assert len(history.json()) == 1
        assert history.json()[0]["decision"] == "NOT_MATERIAL"
        assert history.json()[0]["id"]


def test_ac05_human_material_overrides_negative_triage():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        ident = create_assessed(client, "Ordinary server event")
        assert client.get(f"/api/v1/incidents/{ident}").json()["state"] == "UNDER_REVIEW"
        response = post_review(client, ident, "MATERIAL", rationale="Human evidence indicates major impact")
        assert response.status_code == 200
        incident = response.json()
        assert incident["assessment"]["potentiallyMaterial"] is False
        assert incident["state"] == "MATERIAL_CONFIRMED"
        assert incident["review"]["decision"] == "MATERIAL"
        assert client.post(f"/api/v1/incidents/{ident}/disclosure").status_code == 200


def test_ac05_not_material_can_escalate_and_preserve_history():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        ident = create_assessed(client, "Ordinary application log")
        assert post_review(client, ident, "NOT_MATERIAL", reviewer="reviewer-a").status_code == 200
        assert post_review(client, ident, "MATERIAL", reviewer="reviewer-b", rationale="New evidence discovered").status_code == 200
        incident = client.get(f"/api/v1/incidents/{ident}").json()
        assert incident["state"] == "MATERIAL_CONFIRMED"
        assert incident["review"]["reviewer"] == "reviewer-b"
        history = client.get(f"/api/v1/incidents/{ident}/reviews").json()
        assert [item["decision"] for item in history] == ["NOT_MATERIAL", "MATERIAL"]
        assert [item["reviewer"] for item in history] == ["reviewer-a", "reviewer-b"]
        assert history[0]["id"] != history[1]["id"]
        assert history[1]["rationale"] == "New evidence discovered"
        # Existing incident shape does not change when history is added.
        assert "reviewHistory" not in incident


def test_ac05_invalid_decisions_and_blank_reviewer_do_not_modify_state():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        ident = create_assessed(client, "Routine system log")
        before = client.get(f"/api/v1/incidents/{ident}").json()
        cases = [
            {"decision": "INVALID", "reviewer": "legal"},
            {"decision": "MATERIAL"},
            {"decision": "MATERIAL", "reviewer": "   "},
            {"reviewer": "legal"},
        ]
        for payload in cases:
            response = client.post(f"/api/v1/incidents/{ident}/review", json=payload)
            assert response.status_code == 400
            assert response.json()["error"] == "INVALID_REQUEST"
            assert client.get(f"/api/v1/incidents/{ident}").json() == before
        assert client.get(f"/api/v1/incidents/{ident}/reviews").json() == []


def test_ac05_illegal_transitions_cannot_modify_history():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        ident = new_incident(client).json()["id"]
        assert post_review(client, ident, "MATERIAL").status_code == 409  # RECEIVED
        assert client.get(f"/api/v1/incidents/{ident}/reviews").json() == []
        assert client.post(f"/api/v1/incidents/{ident}/assessment").status_code == 200
        assert post_review(client, ident, "NOT_MATERIAL").status_code == 200
        before = client.get(f"/api/v1/incidents/{ident}").json()
        assert post_review(client, ident, "NOT_MATERIAL").status_code == 409
        assert client.get(f"/api/v1/incidents/{ident}").json() == before
        assert post_review(client, ident, "MATERIAL").status_code == 200
        before = client.get(f"/api/v1/incidents/{ident}").json()
        for decision in ("NOT_MATERIAL", "MATERIAL"):
            assert post_review(client, ident, decision).status_code == 409
            assert client.get(f"/api/v1/incidents/{ident}").json() == before
        assert len(client.get(f"/api/v1/incidents/{ident}/reviews").json()) == 2


def test_ac05_history_not_found_and_reset_deletes_history():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        assert client.get("/api/v1/incidents/does-not-exist/reviews").status_code == 404
        ident = create_assessed(client, "Routine system log")
        assert post_review(client, ident, "NOT_MATERIAL").status_code == 200
        assert client.post("/api/v1/test/reset").status_code == 204
        assert client.get(f"/api/v1/incidents/{ident}/reviews").status_code == 404
        with Session(engine) as session:
            assert session.query(ReviewRow).count() == 0


def test_ac05_history_persists_between_testclient_lifespans():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        ident = create_assessed(client, "Routine system log")
        assert post_review(client, ident, "NOT_MATERIAL").status_code == 200
    with TestClient(app) as client:
        history = client.get(f"/api/v1/incidents/{ident}/reviews")
        assert len(history.json()) == 1
        assert history.json()[0]["decision"] == "NOT_MATERIAL"


def test_ac05_backfills_legacy_review_once_and_does_not_erase_it():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        ident = create_assessed(client, "Routine system log")
        now = datetime.now(timezone.utc)
        legacy = {"decision": "NOT_MATERIAL", "reviewer": "original-reviewer",
                  "rationale": None, "reviewedAt": now.isoformat()}
        with Session(engine) as session:
            row = session.get(IncidentRow, ident)
            row.review = dumps(legacy)
            row.state = "NOT_MATERIAL"
            session.commit()
        backfill_legacy_reviews()
        backfill_legacy_reviews()
        history = client.get(f"/api/v1/incidents/{ident}/reviews").json()
        assert len(history) == 1
        assert history[0]["reviewer"] == "original-reviewer"
        assert client.get(f"/api/v1/incidents/{ident}").json()["review"] == legacy
        assert post_review(client, ident, "MATERIAL").status_code == 200
        assert len(client.get(f"/api/v1/incidents/{ident}/reviews").json()) == 2


def test_ac05_closed_incidents_reject_review_without_mutation():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        ident = create_assessed(client, "Routine system log")
        with Session(engine) as session:
            row = session.get(IncidentRow, ident)
            row.state = "CLOSED"
            session.commit()
        before = client.get(f"/api/v1/incidents/{ident}").json()
        assert post_review(client, ident, "MATERIAL").status_code == 409
        assert client.get(f"/api/v1/incidents/{ident}").json() == before
        assert client.get(f"/api/v1/incidents/{ident}/reviews").json() == []
