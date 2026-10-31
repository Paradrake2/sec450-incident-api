"""AC-08 / Stage 3: external API contract and generated Swagger conformance.

Tests use the disposable SQLite or PostgreSQL store configured by conftest.py.
Never point this test module at the shared Render deployment: it invokes reset.
"""
from fastapi.testclient import TestClient
import pytest

from test_api import app, new_incident


CONTRACT = {
    ("get", "/api/v1/health"): 200,
    ("post", "/api/v1/test/reset"): 204,
    ("post", "/api/v1/incidents"): 201,
    ("get", "/api/v1/incidents"): 200,
    ("get", "/api/v1/incidents/{incidentId}"): 200,
    ("post", "/api/v1/incidents/{incidentId}/assessment"): 200,
    ("post", "/api/v1/incidents/{incidentId}/review"): 200,
    ("post", "/api/v1/incidents/{incidentId}/related"): 200,
    ("post", "/api/v1/incidents/{incidentId}/disclosure"): 200,
}


def test_openapi_all_nine_contract_operations_are_documented():
    schema = app.openapi()
    assert schema["openapi"].startswith("3.")
    for (method, path), success_status in CONTRACT.items():
        assert path in schema["paths"], f"Missing common-interface path {path}"
        assert method in schema["paths"][path], f"Missing {method.upper()} {path}"
        response = schema["paths"][path][method]["responses"]
        assert str(success_status) in response
        assert "422" not in response, "Swagger should document actual 400 validation errors"
    assert schema["paths"]["/api/v1/incidents/{incidentId}"]["get"]["parameters"][0]["name"] == "incidentId"


def test_swagger_declares_real_response_models_and_error_shapes():
    schema = app.openapi()
    for method, path in CONTRACT:
        operation = schema["paths"][path][method]
        for status, response in operation["responses"].items():
            if status in ("400", "404", "409", "415", "500"):
                assert response["content"]["application/json"]["schema"]
        if method == "post" and path == "/api/v1/incidents":
            assert "415" in operation["responses"]
        if "review" in path and method == "post":
            assert "400" in operation["responses"]
    incident_response = schema["paths"]["/api/v1/incidents"]["post"]["responses"]["201"]
    assert incident_response["content"]["application/json"]["schema"]["$ref"].endswith("IncidentResponse")
    review_response = schema["paths"]["/api/v1/incidents/{incidentId}/review"]["post"]["responses"]["200"]
    assert review_response["content"]["application/json"]["schema"]["$ref"].endswith("IncidentResponse")
    disclosure = schema["paths"]["/api/v1/incidents/{incidentId}/disclosure"]["post"]["responses"]["200"]
    assert disclosure["content"]["application/json"]["schema"]["$ref"].endswith("DisclosureResponse")


@pytest.mark.parametrize("path", [
    "/api/v1/incidents",
    "/api/v1/incidents/{incidentId}/review",
    "/api/v1/incidents/{incidentId}/related",
])
def test_json_only_operations_document_their_runtime_415_response(path):
    with TestClient(app) as client:
        response = client.post(
            path.replace("{incidentId}", "unknown"),
            content="not JSON", headers={"content-type": "text/plain"},
        )
        assert response.status_code == 415
        assert response.json() == {
            "error": "UNSUPPORTED_MEDIA_TYPE", "message": "Use application/json",
        }
        documented = app.openapi()["paths"][path]["post"]["responses"]["415"]
        assert documented["content"]["application/json"]["schema"]["$ref"].endswith("ErrorResponse")


def test_contract_end_to_end_each_operation_and_state_transition():
    with TestClient(app) as c:
        assert c.get("/api/v1/health").json() == {"status": "ok"}
        reset = c.post("/api/v1/test/reset")
        assert reset.status_code == 204 and not reset.content
        first = new_incident(c, "Possible ransomware on production systems")
        second = new_incident(c, "Routine synthetic log")
        assert first.status_code == second.status_code == 201
        a, b = first.json()["id"], second.json()["id"]
        assert first.json()["state"] == "RECEIVED"
        assert len(c.get("/api/v1/incidents").json()) == 2
        assert c.get(f"/api/v1/incidents/{a}").json()["id"] == a
        assess = c.post(f"/api/v1/incidents/{a}/assessment")
        assert assess.status_code == 200 and assess.json()["potentiallyMaterial"] is True
        review = c.post(f"/api/v1/incidents/{a}/review", json={
            "decision": "MATERIAL", "reviewer": "contract-test", "rationale": "Synthetic test",
        })
        assert review.status_code == 200 and review.json()["state"] == "MATERIAL_CONFIRMED"
        link = c.post(f"/api/v1/incidents/{a}/related", json={"relatedIncidentId": b})
        assert link.status_code == 200 and link.json()["relatedIncidentIds"] == [b]
        assert c.get(f"/api/v1/incidents/{b}").json()["relatedIncidentIds"] == [a]
        disclosure = c.post(f"/api/v1/incidents/{a}/disclosure")
        assert disclosure.status_code == 200
        assert disclosure.json()["incidentId"] == a and disclosure.json()["final"] is False
        assert c.get(f"/api/v1/incidents/{a}").json()["state"] == "MATERIAL_CONFIRMED"


def test_ac08_error_codes_are_structured_and_do_not_change_state():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a = new_incident(c).json()["id"]
        before = c.get(f"/api/v1/incidents/{a}").json()
        errors = [
            c.post("/api/v1/incidents", json={"title": "", "log": "", "occurredAt": "bad"}),
            c.get("/api/v1/incidents/missing"),
            c.post(f"/api/v1/incidents/{a}/review", json={"reviewer": "legal", "decision": "MATERIAL"}),
            c.post("/api/v1/incidents", data="not-json", headers={"content-type": "text/plain"}),
            c.post(f"/api/v1/incidents/{a}/related", json={"relatedIncidentId": a}),
        ]
        assert [r.status_code for r in errors] == [400, 404, 409, 415, 409]
        for response in errors:
            body = response.json()
            assert isinstance(body["error"], str) and body["error"]
            assert isinstance(body["message"], str) and body["message"]
        assert c.get(f"/api/v1/incidents/{a}").json() == before


def test_unhandled_error_is_structured_500_without_details(monkeypatch):
    import main
    with TestClient(app, raise_server_exceptions=False) as c:
        c.post("/api/v1/test/reset")
        a = new_incident(c).json()["id"]
        monkeypatch.setattr(main, "assess_log", lambda log: (_ for _ in ()).throw(RuntimeError("private stack detail")))
        response = c.post(f"/api/v1/incidents/{a}/assessment")
        assert response.status_code == 500
        assert response.json() == {"error": "INTERNAL_ERROR", "message": "Unexpected server-side failure"}
        assert "private stack detail" not in response.text
        assert c.get(f"/api/v1/incidents/{a}").json()["state"] == "RECEIVED"


def test_timestamp_text_preserved_for_legacy_reviews():
    with TestClient(app) as c:
        c.post("/api/v1/test/reset")
        a = new_incident(c).json()["id"]
        assert c.post(f"/api/v1/incidents/{a}/assessment").status_code == 200
        data = c.post(f"/api/v1/incidents/{a}/review", json={"reviewer": "auditor", "decision": "NOT_MATERIAL"}).json()
        assert data["review"]["reviewedAt"].endswith("+00:00")
        assert c.get(f"/api/v1/incidents/{a}").json()["review"] == data["review"]
