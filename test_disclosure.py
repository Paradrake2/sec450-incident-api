"""REQ-07 / AC-07: disclosure eligibility, content, persistence and failures."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from test_api import TestClient, app, engine, new_incident
from main import DisclosureRow, IncidentRow
from disclosure_templates import LOG_EXCERPT_LIMIT, build_draft


def make(client, log="Routine synthetic server event"):
    response = new_incident(client, log)
    assert response.status_code == 201
    return response.json()["id"]


def assessed(client, log="Possible ransomware affecting production systems"):
    incident_id = make(client, log)
    assert client.post(f"/api/v1/incidents/{incident_id}/assessment").status_code == 200
    return incident_id


def material(client, log="Possible ransomware affecting production systems", rationale="Impact requires examination"):
    ident = assessed(client, log)
    response = client.post(f"/api/v1/incidents/{ident}/review", json={
        "decision": "MATERIAL", "reviewer": "test-legal", "rationale": rationale,
    })
    assert response.status_code == 200
    assert response.json()["state"] == "MATERIAL_CONFIRMED"
    return ident


def draft(client, ident):
    return client.post(f"/api/v1/incidents/{ident}/disclosure")


def history(client, ident):
    return client.get(f"/api/v1/incidents/{ident}/disclosures")


def stored_drafts():
    with Session(engine) as session:
        return session.scalars(select(DisclosureRow)).all()


def test_ac07_material_incident_generates_nonfinal_draft_with_required_fields():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        ident = material(c, "Possible ransomware affecting production systems")
        result = draft(c, ident)
        assert result.status_code == 200
        payload = result.json()
        assert set(payload) == {"incidentId", "draft", "generatedAt", "final"}
        assert payload["incidentId"] == ident
        assert payload["final"] is False
        assert payload["generatedAt"]
        assert "FORM 8-K ITEM 1.05" in payload["draft"]
        assert "NOT AN SEC FILING" in payload["draft"]
        assert "NON-FINAL" in payload["draft"]
        assert "1. Nature" in payload["draft"]
        assert "2. Scope" in payload["draft"]
        assert "3. Timing" in payload["draft"]
        assert "4. Materiality" in payload["draft"]
        assert "5. Outstanding" in payload["draft"]
        assert "Possible ransomware" in payload["draft"]
        assert "test-legal" in payload["draft"]
        assert len(stored_drafts()) == 1


def test_ac07_cannot_generate_before_assessment():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        ident = make(c)
        before = c.get(f"/api/v1/incidents/{ident}").json()
        result = draft(c, ident)
        assert result.status_code == 409
        assert result.json()["error"] == "INVALID_STATE"
        assert c.get(f"/api/v1/incidents/{ident}").json() == before
        assert stored_drafts() == []


def test_ac07_cannot_generate_after_triage_without_human_review():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        for log in ("Ransomware affecting systems", "Routine server event"):
            ident = assessed(c, log)
            before = c.get(f"/api/v1/incidents/{ident}").json()
            assert draft(c, ident).status_code == 409
            assert c.get(f"/api/v1/incidents/{ident}").json() == before
        assert stored_drafts() == []


def test_ac07_not_material_review_prohibits_draft():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        ident = assessed(c)
        response = c.post(f"/api/v1/incidents/{ident}/review", json={
            "decision": "NOT_MATERIAL", "reviewer": "test-legal"})
        assert response.status_code == 200
        before = c.get(f"/api/v1/incidents/{ident}").json()
        assert draft(c, ident).status_code == 409
        assert c.get(f"/api/v1/incidents/{ident}").json() == before
        assert stored_drafts() == []


def test_ac07_escalated_not_material_can_generate_draft():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        ident = assessed(c, "Routine log")
        for decision in ("NOT_MATERIAL", "MATERIAL"):
            assert c.post(f"/api/v1/incidents/{ident}/review", json={
                "decision": decision, "reviewer": "test-legal"}).status_code == 200
        assert draft(c, ident).status_code == 200
        assert len(history(c, ident).json()) == 1


def test_ac07_negative_assessment_with_human_material_override_is_eligible():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        ident = material(c, "Routine server event")
        assert c.get(f"/api/v1/incidents/{ident}").json()["assessment"]["potentiallyMaterial"] is False
        assert draft(c, ident).status_code == 200


def test_ac07_unknown_incident_and_empty_history():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        assert draft(c, "not-an-incident").status_code == 404
        response = history(c, "not-an-incident")
        assert response.status_code == 404
        assert response.json()["error"] == "INCIDENT_NOT_FOUND"
        ident = make(c)
        assert history(c, ident).status_code == 200
        assert history(c, ident).json() == []


def test_ac07_multiple_drafts_are_preserved_as_distinct_versions():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        ident = material(c)
        before = c.get(f"/api/v1/incidents/{ident}").json()
        first = draft(c, ident).json()
        second = draft(c, ident).json()
        versions = history(c, ident)
        assert versions.status_code == 200
        items = versions.json()
        assert len(items) == 2
        assert len({item["id"] for item in items}) == 2
        assert all(item["incidentId"] == ident for item in items)
        assert all(item["final"] is False for item in items)
        assert {item["generatedAt"] for item in items} == {
            first["generatedAt"], second["generatedAt"]}
        assert [item["draft"] for item in items] == [first["draft"], second["draft"]]
        assert c.get(f"/api/v1/incidents/{ident}").json() == before


def test_ac07_persists_history_between_application_lifespans():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        ident = material(c)
        before = draft(c, ident).json()
    with TestClient(app) as c:
        items = history(c, ident).json()
        assert len(items) == 1
        assert items[0]["draft"] == before["draft"]
        assert items[0]["generatedAt"] == before["generatedAt"]
        assert items[0]["final"] is False


def test_ac07_reset_clears_drafts_and_incidents():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        ident = material(c)
        assert draft(c, ident).status_code == 200
        assert c.post("/api/v1/test/reset").status_code == 204
        assert stored_drafts() == []
        assert history(c, ident).status_code == 404
    with TestClient(app) as c:
        assert stored_drafts() == []


def test_ac07_marks_unverified_submitted_claims_and_identifies_gaps():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        ident = material(c, "No data loss established. Test service reported outage.")
        content = draft(c, ident).json()["draft"]
        assert "unverified" in content.lower()
        assert "NOT established" in content
        assert "not been verified or quantified" in content
        assert "Discovery date, materiality determination date" in content
        assert "Occurrence time (as submitted)" in content
        assert "Intake time (recorded by system)" in content
        assert "user-submitted, not authenticated" in content


def test_ac07_long_log_is_truncated_with_explicit_notice():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        ident = material(c, "Possible ransomware " + "a" * (LOG_EXCERPT_LIMIT + 200))
        content = draft(c, ident).json()["draft"]
        assert f"First {LOG_EXCERPT_LIMIT} characters only; remainder omitted." in content
        assert "a" * (LOG_EXCERPT_LIMIT - 24) in content
        assert "a" * (LOG_EXCERPT_LIMIT + 100) not in content


def test_ac07_multiline_logs_cannot_impersonate_template_sections():
    text = build_draft(
        incident_id="test-1", title="Example\nFAKE FINAL REPORT", log="hello\nFINAL APPROVED\nhello",
        occurred_at="2026-10-30T12:00:00Z", created_at="2026-10-30T13:00:00Z",
        source="manual\nFAKE SOURCE", review={"reviewer": "demo",
        "reviewedAt": "2026-10-30T14:00:00Z", "rationale": "approve\nFAKE FILING"},
    )
    assert "Reported title: Example FAKE FINAL REPORT" in text
    assert "Submitted source: manual FAKE SOURCE" in text
    assert "  | FINAL APPROVED" in text
    assert "  | FAKE FILING" in text
    assert "\nFINAL APPROVED\n" not in text


def test_ac07_inconsistent_material_state_without_review_is_rejected():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        ident = make(c)
        with Session(engine) as session:
            session.get(IncidentRow, ident).state = "MATERIAL_CONFIRMED"
            session.commit()
        assert draft(c, ident).status_code == 409
        assert stored_drafts() == []
