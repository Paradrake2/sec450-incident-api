"""HTTP protocol behavior with injected targets; no external requests."""

import copy

from fastapi.testclient import TestClient
import pytest

from tester_app import create_tester_app
from tester_core import TesterCoordinator as Coordinator
from tester_interfaces import ProtocolFailure, SuiteCase
from testing_protocol import CoverageMeasure, TestResult as Result


TARGET = "https://approved.example/system"


class FakeGateway:
    def __init__(self, failure=None):
        self.failure = failure
        self.prepared = []
        self.coverage_calls = 0

    async def prepare(self, target_url, session_id):
        self.prepared.append((target_url, session_id))
        if self.failure is not None:
            raise self.failure
        return session_id

    async def code_coverage(self, target_url, session_id):
        self.coverage_calls += 1
        return CoverageMeasure(covered=2, total=8, basis="Synthetic target instrumentation")


def configured_app(gateway=None, case=None):
    gateway = gateway if gateway is not None else FakeGateway()

    async def passing(context):
        return Result(test_id="example", outcome="pass", message="Synthetic adapter check passed")

    service = Coordinator(gateway=gateway, cases=(SuiteCase("example", case or passing),))
    return create_tester_app(service, approved_targets=(TARGET,)), gateway


def test_setup_run_and_report_are_one_run_and_stable_http_snapshots():
    application, gateway = configured_app()
    with TestClient(application) as client:
        setup = client.post("/test/setup", json={"target_url": TARGET})
        assert setup.status_code == 201
        ident = setup.json()["session_id"]
        assert gateway.prepared == [(TARGET, ident)]
        assert client.get("/test/report", params={"session_id": ident}).status_code == 409

        run = client.post("/test/run", json={"session_id": ident})
        assert run.status_code == 200
        original = run.json()
        assert original["session_id"] == ident
        assert original["target_url"] == TARGET
        assert original["results"] == [{
            "test_id": "example", "outcome": "pass", "message": "Synthetic adapter check passed",
        }]
        assert original["coverage"]["code"]["covered"] == 2
        for measure in ("component", "system"):
            assert original["coverage"][measure]["covered"] is None
            assert original["coverage"][measure]["total"] is None
            assert original["coverage"][measure]["basis"]
        changed = copy.deepcopy(original)
        changed["results"][0]["message"] = "Caller changed its own response"
        for _ in range(2):
            assert client.get("/test/report", params={"session_id": ident}).json() == original
        assert gateway.coverage_calls == 1
        assert client.post("/test/run", json={"session_id": ident}).status_code == 409


@pytest.mark.parametrize("path,payload", [
    ("/test/setup", {}),
    ("/test/setup", {"target_url": "  "}),
    ("/test/setup", {"target_url": TARGET, "extra": "unagreed"}),
    ("/test/run", {}),
    ("/test/run", {"session_id": " "}),
])
def test_invalid_protocol_bodies_use_400_and_tester_source(path, payload):
    application, gateway = configured_app()
    with TestClient(application) as client:
        response = client.post(path, json=payload)
        assert response.status_code == 400
        assert response.json()["source"] == "tester"
        assert response.json()["message"]
        assert gateway.prepared == []


def test_invalid_run_does_not_consume_a_ready_session():
    application, _ = configured_app()
    with TestClient(application) as client:
        ident = client.post("/test/setup", json={"target_url": TARGET}).json()["session_id"]
        invalid = client.post("/test/run", json={"session_id": ident, "extra": True})
        assert invalid.status_code == 400
        assert client.post("/test/run", json={"session_id": ident}).status_code == 200


@pytest.mark.parametrize("url", [
    "https://unapproved.example/system",
    "https://approved.example/system/another",
    "https://user:secret@approved.example/system",
])
def test_unapproved_targets_are_rejected_before_preparing_any_session(url):
    application, gateway = configured_app()
    with TestClient(application) as client:
        response = client.post("/test/setup", json={"target_url": url})
        assert response.status_code == 400
        assert response.json()["source"] == "tester"
        assert "secret" not in response.text
        assert gateway.prepared == []


def test_target_setup_error_status_message_and_source_are_preserved():
    gateway = FakeGateway(ProtocolFailure(403, "Target denied synthetic access", "system"))
    application, _ = configured_app(gateway)
    with TestClient(application) as client:
        response = client.post("/test/setup", json={"target_url": TARGET})
        assert response.status_code == 403
        assert response.json() == {"message": "Target denied synthetic access", "source": "system"}
        failed_id = gateway.prepared[0][1]
        assert client.get("/test/report", params={"session_id": failed_id}).status_code == 404


def test_aborted_suite_saves_retrievable_results_and_cannot_run_again():
    async def broken(context):
        raise RuntimeError("private adapter detail")

    application, _ = configured_app(case=broken)
    with TestClient(application) as client:
        ident = client.post("/test/setup", json={"target_url": TARGET}).json()["session_id"]
        failed = client.post("/test/run", json={"session_id": ident})
        assert failed.status_code == 500
        assert failed.json()["source"] == "tester"
        assert "private adapter detail" not in failed.text
        report = client.get("/test/report", params={"session_id": ident})
        assert report.status_code == 200
        assert report.json()["results"][0]["outcome"] == "error"
        assert report.json()["results"][0]["source"] == "tester"
        assert "private adapter detail" not in report.text
        assert client.post("/test/run", json={"session_id": ident}).status_code == 409


def test_protocol_error_format_also_covers_media_type_queries_and_framework_errors():
    application, gateway = configured_app()
    with TestClient(application) as client:
        responses = [
            client.post("/test/setup", content="{}", headers={"content-type": "text/plain"}),
            client.post("/test/run", content="{", headers={"content-type": "application/json"}),
            client.get("/test/report"),
            client.get("/test/report", params={"session_id": " "}),
            client.get("/test/setup"),
            client.get("/missing"),
        ]
        assert [response.status_code for response in responses] == [400, 400, 400, 400, 405, 404]
        assert all(response.json()["source"] == "tester" for response in responses)
        assert all(response.json()["message"] for response in responses)
        assert gateway.prepared == []
        assert client.post("/test/run", json={"session_id": "unknown"}).status_code == 404
        assert client.get("/test/report", params={"session_id": "unknown"}).status_code == 404


def test_tester_openapi_contains_real_models_and_actual_validation_statuses():
    application, _ = configured_app()
    schema = application.openapi()
    assert set(schema["paths"]) == {"/test/setup", "/test/run", "/test/report"}
    for path in schema["paths"].values():
        for operation in path.values():
            responses = operation["responses"]
            assert "422" not in responses
            for status in ("400", "404", "409", "500", "default"):
                assert responses[status]["content"]["application/json"]["schema"]["$ref"].endswith("ProtocolErrorBody")
    assert schema["paths"]["/test/run"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("SuiteReport")


def test_default_mounted_tester_is_visible_but_cannot_prepare_live_targets():
    from test_api import app

    with TestClient(app) as client:
        response = client.get("/tester/openapi.json")
        assert response.status_code == 200
        assert response.json()["servers"][0]["url"] == "/tester"
        setup = client.post("/tester/test/setup", json={"target_url": TARGET})
        assert setup.status_code == 500
        assert setup.json()["source"] == "tester"
        assert "not configured" in setup.json()["message"]
        assert client.post("/tester/test/run", json={"session_id": "unknown"}).status_code == 404
        assert client.get("/api/v1/health").json() == {"status": "ok"}
        assert "/test/setup" not in app.openapi()["paths"]
