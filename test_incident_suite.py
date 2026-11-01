"""Offline suite checks: real isolated application routes and bounded doubles."""

import asyncio
from collections import Counter

import httpx
import pytest

from incident_suite import build_incident_cases
from tester_core import TesterCoordinator as Coordinator
from tester_http import HttpTargetGateway
from tester_interfaces import ProtocolFailure, TestContext as Context


TARGET = "https://synthetic-course.example/system"
PEER = "https://synthetic-peer.example"
SID = "synthetic-suite-session"
PRIVATE = "PRIVATE_ERROR_WITH_BEARER_AND_PAYLOAD"
ERROR = {"error": "SYNTHETIC", "message": "Synthetic test error"}
GAPS = {
    "reset.shared-fixture-restoration",
    "plan.shared-classification-labels",
    "plan.closed-state",
    "plan.server-failure-responses",
    "plan.disclosure-quality",
}
REGRESSIONS = {
    "regression.create-null-source",
    "regression.create-naive-timestamp",
    "regression.review-null-rationale",
    "regression.related-extension",
    "regression.review-lookup-before-validation",
    "regression.related-lookup-before-validation",
}


def invoke(coroutine):
    return asyncio.run(coroutine)


async def run_cases(cases, context):
    return [await case.run(context) for case in cases]


def read_only_handler(request):
    path = request.url.path
    assert request.method == "GET", "An unapproved mutation reached the transport"
    if path.endswith("/health"):
        return httpx.Response(200, json={"status": "ok"})
    if path.endswith("/incidents"):
        if request.url.params.get("state"):
            return httpx.Response(400, json=ERROR)
        return httpx.Response(200, json=[])
    return httpx.Response(404, json=ERROR)


def gateway(handler, target=TARGET):
    return HttpTargetGateway((target,), transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("allowed", [(), (PEER,), (TARGET + "/wrong",)])
def test_unapproved_mutations_skip_but_safe_probes_execute(allowed):
    requests = []

    def handler(request):
        requests.append(request)
        return read_only_handler(request)

    cases = build_incident_cases(gateway(handler), isolated_reset_targets=allowed)
    results = invoke(run_cases(cases, Context(TARGET, SID)))
    assert len(results) == len(cases)
    assert len({result.test_id for result in results}) == len(cases)
    assert len(requests) == 4
    assert all(request.method == "GET" for request in requests)
    assert all(request.headers.get("authorization") is None for request in requests)
    assert [result.outcome for result in results[:4]] == ["pass"] * 4
    assert all(result.outcome == "skip" for result in results[4:])
    assert GAPS | REGRESSIONS <= {case.test_id for case in cases}


def test_read_only_feed_is_not_mistaken_for_an_incident_api_pass():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        return httpx.Response(404, json={"detail": PRIVATE})

    cases = build_incident_cases(gateway(handler), isolated_reset_targets=())
    results = invoke(run_cases(cases, Context(TARGET, SID)))
    assert len(requests) == 4
    assert [result.outcome for result in results[:4]] == ["fail"] * 4
    assert all(result.outcome == "skip" for result in results[4:])
    assert PRIVATE not in str([result.model_dump() for result in results])


def test_failed_reset_blocks_every_later_post_and_dependent_get():
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST":
            assert request.url.path.endswith("/api/v1/test/reset")
            return httpx.Response(404, json=ERROR)
        return read_only_handler(request)

    context = Context(TARGET, SID)
    # Readiness from a prior attempt must be invalidated before reset is tried.
    context.state["incident_suite"] = {"mutation_ready": True, "primary_id": "stale"}
    cases = build_incident_cases(gateway(handler), isolated_reset_targets=(TARGET,))
    results = invoke(run_cases(cases, context))
    assert len(requests) == 5
    assert sum(request.method == "POST" for request in requests) == 1
    assert results[4].test_id == "reset.isolated"
    assert results[4].outcome == "fail"
    assert all(result.outcome == "skip" for result in results[5:])
    assert "mutation_ready" not in context.state["incident_suite"]
    assert "primary_id" not in context.state["incident_suite"]


def test_independent_cases_can_run_out_of_order_without_crashing_or_writing():
    requests = []

    def handler(request):
        requests.append(request)
        return read_only_handler(request)

    cases = build_incident_cases(gateway(handler), isolated_reset_targets=(TARGET,))
    selected = [
        case
        for case in cases
        if case.test_id
        in {
            "review.primary-material",
            "get.primary-stored",
            "related.duplicate-conflict",
        }
    ]
    results = invoke(run_cases(selected, Context(TARGET, SID)))
    assert len(results) == 3
    assert all(result.outcome == "skip" for result in results)
    assert requests == []


@pytest.mark.parametrize(
    "failure", [ProtocolFailure(504, PRIVATE, "communication"), RuntimeError(PRIVATE)]
)
def test_target_failures_are_classified_without_exposing_messages(failure):
    class BrokenGateway:
        async def request(self, *args, **kwargs):
            raise failure

    cases = build_incident_cases(BrokenGateway(), isolated_reset_targets=(TARGET,))
    results = invoke(run_cases(cases, Context(TARGET, SID)))
    assert len(results) == len(cases)
    assert results[0].outcome == "error"
    assert results[0].source == (
        "communication" if isinstance(failure, ProtocolFailure) else "tester"
    )
    assert PRIVATE not in str([result.model_dump() for result in results])
    assert (
        next(result for result in results if result.test_id == "create.primary").outcome
        == "skip"
    )


def test_untrusted_response_identifier_is_not_interpolated_into_a_followup_url():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/reset"):
            return httpx.Response(204)
        return httpx.Response(
            201,
            json={
                "id": "../arbitrary-route",
                "title": "Synthetic course test: reported ransomware",
                "log": "Synthetic exercise: ransomware detected; customer data exfiltration reported.",
                "occurredAt": "2026-01-15T14:30:00Z",
                "createdAt": "2026-01-15T14:31:00Z",
                "state": "RECEIVED",
                "relatedIncidentIds": [],
            },
        )

    cases = build_incident_cases(gateway(handler), isolated_reset_targets=(TARGET,))
    selected = [
        case
        for case in cases
        if case.test_id
        in {
            "reset.isolated",
            "create.primary",
            "get.primary-stored",
            "assessment.primary-advisory",
        }
    ]
    results = invoke(run_cases(selected, Context(TARGET, SID)))
    assert [result.outcome for result in results] == ["pass", "fail", "skip", "skip"]
    assert len(requests) == 2


def test_optional_schema_fields_missing_required_behavior_fail_without_crashing():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "id": "synthetic-id",
                "title": "Synthetic",
                "log": "Synthetic",
                "occurredAt": "2026-01-15T14:30:00Z",
                "createdAt": "2026-01-15T14:31:00Z",
                "state": "POTENTIALLY_MATERIAL",
                "relatedIncidentIds": [],
            },
        )

    context = Context(TARGET, SID)
    context.state["incident_suite"] = {
        "primary_id": "synthetic-id",
        "primary_assessed": True,
    }
    cases = build_incident_cases(gateway(handler), isolated_reset_targets=())
    selected = next(
        case for case in cases if case.test_id == "get.primary-advisory-state"
    )
    result = invoke(selected.run(context))
    assert result.outcome == "fail"
    assert result.source is None


@pytest.fixture
def isolated_system(tmp_path):
    import main
    from course_system import create_course_app
    from sqlalchemy import create_engine, inspect
    from system_sessions import SystemSessionRegistry, course_store_factory

    normal = create_engine("sqlite://")
    incident_app = main.create_incident_app(database_engine=normal)
    registry = SystemSessionRegistry(
        course_store_factory(temporary_root=tmp_path), main.initialize_database
    )
    application = create_course_app(incident_app, registry)
    try:
        yield application, registry
    finally:
        registry.close()
        assert inspect(normal).get_table_names() == []
        normal.dispose()


def test_full_suite_uses_actual_isolated_routes_and_keeps_coverage_unknown(
    isolated_system,
):
    application, registry = isolated_system
    requests = []

    class RecordingTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self.transport = httpx.ASGITransport(
                app=application, raise_app_exceptions=False
            )

        async def handle_async_request(self, request):
            requests.append(request)
            return await self.transport.handle_async_request(request)

    # ASGI apps are trusted offline injection, so no sockets/DNS are involved.
    target = "https://synthetic-course.example"
    adapter = HttpTargetGateway((target,), transport=RecordingTransport())
    cases = build_incident_cases(adapter, isolated_reset_targets=(target,))
    coordinator = Coordinator(adapter, cases, run_timeout=30)

    async def run():
        session = await coordinator.setup(target)
        return await coordinator.run(session.session_id)

    report = invoke(run())
    outcomes = Counter(result.outcome for result in report.results)
    assert outcomes == {"pass": len(cases) - len(GAPS), "skip": len(GAPS)}, [
        (result.test_id, result.outcome, result.message)
        for result in report.results
        if result.outcome not in {"pass", "skip"}
    ]
    assert {
        result.test_id for result in report.results if result.outcome == "skip"
    } == GAPS
    assert set(REGRESSIONS) <= {
        result.test_id for result in report.results if result.outcome == "pass"
    }
    assert len(report.results) == len(cases) == len({case.test_id for case in cases})
    assert report.coverage.code.covered is None and report.coverage.code.total is None
    assert (
        report.coverage.component.covered is None
        and report.coverage.component.total is None
    )
    assert (
        report.coverage.system.covered is None and report.coverage.system.total is None
    )
    assert all(request.headers.get("authorization") is None for request in requests)
    assert all(
        request.headers.get("x-test-session-id") == report.session_id
        for request in requests
    )
    seen = set()
    for request in requests:
        path = request.url.path
        if not path.startswith("/api/v1/"):
            continue
        if path.startswith("/api/v1/incidents/"):
            parts = path.split("/")
            parts[4] = "{incidentId}"
            path = "/".join(parts)
        seen.add((request.method, path))
    assert seen == {
        ("GET", "/api/v1/health"),
        ("POST", "/api/v1/test/reset"),
        ("POST", "/api/v1/incidents"),
        ("GET", "/api/v1/incidents"),
        ("GET", "/api/v1/incidents/{incidentId}"),
        ("POST", "/api/v1/incidents/{incidentId}/assessment"),
        ("POST", "/api/v1/incidents/{incidentId}/review"),
        ("POST", "/api/v1/incidents/{incidentId}/related"),
        ("POST", "/api/v1/incidents/{incidentId}/disclosure"),
    }
    assert registry.report(report.session_id).coverage.code.covered is None
    assert coordinator.report(report.session_id) == report


def test_normalized_exact_base_is_allowed_but_child_or_parent_paths_are_not():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(204)

    cases = build_incident_cases(
        gateway(handler),
        isolated_reset_targets=("https://SYNTHETIC-COURSE.example:443/system/",),
    )
    reset = next(case for case in cases if case.test_id == "reset.isolated")
    assert invoke(reset.run(Context(TARGET, SID))).outcome == "pass"
    assert invoke(reset.run(Context(TARGET + "/child", SID))).outcome == "skip"
    assert (
        invoke(reset.run(Context("https://synthetic-course.example", SID))).outcome
        == "skip"
    )
    assert len(requests) == 1


def test_invalid_isolation_configuration_fails_before_any_io():
    with pytest.raises(ValueError):
        build_incident_cases(
            gateway(read_only_handler), isolated_reset_targets=[TARGET]
        )
    with pytest.raises(ValueError):
        build_incident_cases(
            gateway(read_only_handler), isolated_reset_targets=(TARGET + "?unsafe=1",)
        )
