import os
import tempfile

# Isolate integration tests from developer database; set before importing main.
fd, db = tempfile.mkstemp(suffix=".db")
os.close(fd)
os.environ["DATABASE_URL"] = "sqlite:///" + db

# noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from main import app, Base, engine  # noqa: E402


def new_incident(client, log="ordinary access log"):
    return client.post("/api/v1/incidents", json={"title": "Test incident", "log": log, "occurredAt": "2026-10-16T12:00:00Z"})


def test_full_flow():
    with TestClient(app) as c:
        assert c.get("/health").json() == {"status": "ok"}
        assert c.post("/api/v1/test/reset").status_code == 204
        assert new_incident(c, "possible ransomware affecting servers").status_code == 201
        incident = new_incident(c, "possible data breach")
        assert incident.status_code == 201
        ident = incident.json()["id"]
        assert incident.json()["state"] == "RECEIVED"
        assert len(c.get("/api/v1/incidents").json()) == 2
        assessment = c.post(f"/api/v1/incidents/{ident}/assessment")
        assert assessment.status_code == 200 and assessment.json()["potentiallyMaterial"]
        assert c.get(f"/api/v1/incidents/{ident}").json()["state"] == "POTENTIALLY_MATERIAL"
        assert c.post(f"/api/v1/incidents/{ident}/disclosure").status_code == 409
        rev = c.post(f"/api/v1/incidents/{ident}/review", json={"reviewer":"legal-test", "decision":"MATERIAL"})
        assert rev.status_code == 200 and rev.json()["state"] == "MATERIAL_CONFIRMED"
        draft = c.post(f"/api/v1/incidents/{ident}/disclosure")
        assert draft.status_code == 200 and draft.json()["final"] is False
        other = c.get("/api/v1/incidents").json()[0]["id"]
        assert c.post(f"/api/v1/incidents/{ident}/related", json={"relatedIncidentId": other}).status_code == 200
        assert ident in c.get(f"/api/v1/incidents/{other}").json()["relatedIncidentIds"]
        assert c.post(f"/api/v1/incidents/{ident}/related", json={"relatedIncidentId": other}).status_code == 409
        assert c.get("/api/v1/incidents?state=MATERIAL_CONFIRMED").json()[0]["id"] == ident


def test_failures():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        assert c.get("/api/v1/incidents/missing").status_code == 404
        assert c.get("/api/v1/incidents?state=bad").status_code == 400
        assert c.post("/api/v1/incidents", data="bad", headers={"content-type":"text/plain"}).status_code == 415
        assert c.post("/api/v1/incidents", json={"title":"", "log":"", "occurredAt":"invalid"}).status_code == 400
        ident = new_incident(c).json()["id"]
        assert c.post(f"/api/v1/incidents/{ident}/review", json={"decision":"MATERIAL","reviewer":"tester"}).status_code == 409
        a = c.post(f"/api/v1/incidents/{ident}/assessment")
        assert a.json()["potentiallyMaterial"] is False
        assert c.get(f"/api/v1/incidents/{ident}").json()["state"] == "UNDER_REVIEW"
        assert c.post(f"/api/v1/incidents/{ident}/assessment").status_code == 409
        assert c.post(f"/api/v1/incidents/{ident}/review", json={"decision":"INVALID","reviewer":"tester"}).status_code == 400


def test_ac03_ransomware_assessment_is_stored():
    with TestClient(app) as c:
        c.post('/api/v1/test/reset')
        incident_id = new_incident(c, 'Possible ransomware affecting production servers').json()['id']
        response = c.post(f'/api/v1/incidents/{incident_id}/assessment')
        assert response.status_code == 200
        payload = response.json()
        assert payload['potentiallyMaterial'] is True
        assert 'ransomware' in payload['indicators']
        stored = c.get(f'/api/v1/incidents/{incident_id}').json()
        assert stored['state'] == 'POTENTIALLY_MATERIAL'
        assert stored['assessment'] == payload


def test_ac03_negated_indicator_does_not_escalate():
    with TestClient(app) as c:
        c.post('/api/v1/test/reset')
        incident_id = new_incident(c, 'No evidence of data breach or unauthorized access').json()['id']
        response = c.post(f'/api/v1/incidents/{incident_id}/assessment')
        assert response.status_code == 200
        assert response.json()['potentiallyMaterial'] is False
        assert c.get(f'/api/v1/incidents/{incident_id}').json()['state'] == 'UNDER_REVIEW'


def test_ac03_repeated_failed_admin_logins_escalate():
    with TestClient(app) as c:
        c.post('/api/v1/test/reset')
        incident_id = new_incident(c, 'Failed password for admin from 203.0.113.5, 12 attempts in 90s').json()['id']
        response = c.post(f'/api/v1/incidents/{incident_id}/assessment')
        assert response.status_code == 200
        assert response.json()['potentiallyMaterial'] is True
        assert c.get(f'/api/v1/incidents/{incident_id}').json()['state'] == 'POTENTIALLY_MATERIAL'


def test_ac03_second_assessment_rejected_without_mutating_record():
    with TestClient(app) as c:
        c.post('/api/v1/test/reset')
        incident_id = new_incident(c, 'Possible data breach').json()['id']
        c.post(f'/api/v1/incidents/{incident_id}/assessment')
        prior = c.get(f'/api/v1/incidents/{incident_id}').json()
        rejected = c.post(f'/api/v1/incidents/{incident_id}/assessment')
        assert rejected.status_code == 409
        assert c.get(f'/api/v1/incidents/{incident_id}').json() == prior
