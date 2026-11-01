"""HTTP regressions for the six recovered request-contract differences."""

import pytest

from contract_audit import run_audit
from test_api import TestClient, app, new_incident


@pytest.mark.parametrize(
    "changes",
    [
        {"source": None},
        {"occurredAt": "2026-10-31T13:00:00"},
    ],
)
def test_invalid_optional_value_or_timezone_does_not_create_an_incident(changes):
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        response = client.post(
            "/api/v1/incidents",
            json={
                "title": "Synthetic request",
                "log": "Synthetic event",
                "occurredAt": "2026-10-31T13:00:00Z",
                **changes,
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "INVALID_REQUEST"
        assert client.get("/api/v1/incidents").json() == []


def test_explicit_null_review_does_not_write_a_decision_or_history():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        ident = new_incident(client).json()["id"]
        client.post(f"/api/v1/incidents/{ident}/assessment")
        before = client.get(f"/api/v1/incidents/{ident}").json()
        response = client.post(
            f"/api/v1/incidents/{ident}/review",
            json={
                "decision": "MATERIAL",
                "reviewer": "Synthetic reviewer",
                "rationale": None,
            },
        )
        assert response.status_code == 400
        assert client.get(f"/api/v1/incidents/{ident}").json() == before
        assert client.get(f"/api/v1/incidents/{ident}/reviews").json() == []


@pytest.mark.parametrize("action", ["review", "related"])
def test_incident_lookup_precedes_decoded_body_field_validation(action):
    with TestClient(app) as client:
        response = client.post(f"/api/v1/incidents/missing/{action}", json={})
        assert response.status_code == 404
        assert response.json()["error"] == "INCIDENT_NOT_FOUND"
        wrong_media = client.post(
            f"/api/v1/incidents/missing/{action}",
            content="{}",
            headers={"content-type": "text/plain"},
        )
        assert wrong_media.status_code == 415


def test_related_extension_fields_are_accepted_without_becoming_incident_data():
    with TestClient(app) as client:
        client.post("/api/v1/test/reset")
        first, second = new_incident(client).json(), new_incident(client).json()
        response = client.post(
            f"/api/v1/incidents/{first['id']}/related",
            json={
                "relatedIncidentId": second["id"],
                "annotation": "Synthetic extension",
            },
        )
        assert response.status_code == 200
        assert response.json()["relatedIncidentIds"] == [second["id"]]
        assert "annotation" not in response.json()
        assert client.get(f"/api/v1/incidents/{second['id']}").json()[
            "relatedIncidentIds"
        ] == [first["id"]]
        assert response.json()["state"] == first["state"]


def test_real_incident_api_passes_every_observed_contract_audit_check():
    with TestClient(app) as client:
        report = run_audit(client)
        assert report["summary"]["failed"] == report["summary"]["errors"] == 0
        assert report["summary"]["passed"] >= 44
        # Fixture agreement and untested statuses are still not full conformance.
        assert report["conformanceEstablished"] is False
