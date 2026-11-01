"""Composition checks use real project routes and an injected ASGI transport."""

import asyncio
from collections import Counter

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import main
from course_fixtures import load_fixture_profile, make_fixture_seed
from delivery_app import build_delivery_app
from system_sessions import SystemSessionRegistry, course_store_factory
from tester_http import HttpTargetGateway
from tester_interfaces import ProtocolFailure
from testing_protocol import SuiteReport, SystemReport

ORIGIN = "https://synthetic-delivery.example"
TARGET = ORIGIN + "/system"
BODY = {
    "title": "Synthetic normal-store sentinel",
    "log": "Synthetic routine maintenance",
    "occurredAt": "2026-10-31T14:00:00Z",
}
GAPS = {
    "reset.shared-fixture-restoration",
    "plan.shared-classification-labels",
    "plan.closed-state",
    "plan.server-failure-responses",
    "plan.disclosure-quality",
}


class ComposedTransport(httpx.AsyncBaseTransport):
    """Resolve the construction cycle without replacing the actual gateway."""

    def __init__(self):
        self.application = None
        self.requests = []

    async def handle_async_request(self, request):
        self.requests.append(request)
        transport = httpx.ASGITransport(
            app=self.application, raise_app_exceptions=False
        )
        return await transport.handle_async_request(request)


@pytest.fixture
def delivery(tmp_path):
    normal = create_engine(
        "sqlite:///" + (tmp_path / "normal.db").as_posix(),
        connect_args={"check_same_thread": False},
    )
    registry = SystemSessionRegistry(
        course_store_factory(temporary_root=tmp_path),
        main.initialize_database,
        seed=make_fixture_seed(),
    )
    transport = ComposedTransport()
    gateway = HttpTargetGateway((TARGET,), transport=transport)
    application = build_delivery_app(
        public_base_url=ORIGIN,
        database_engine=normal,
        registry=registry,
        gateway=gateway,
        approved_targets=(TARGET,),
    )
    transport.application = application
    try:
        yield application, registry, normal, transport
    finally:
        registry.close()
        normal.dispose()


def test_composed_runner_executes_real_routes_and_preserves_normal_store(delivery):
    application, registry, normal, outbound = delivery

    async def scenario():
        async with (
            application.router.lifespan_context(application),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url=ORIGIN
            ) as client,
        ):
            created = await client.post("/api/v1/incidents", json=BODY)
            assert created.status_code == 201
            sentinel = created.json()
            prepared = await client.post(
                "/tester/test/setup", json={"target_url": TARGET}
            )
            assert prepared.status_code == 201, prepared.text
            ident = prepared.json()["session_id"]
            pending = await client.get(
                "/tester/test/report", params={"session_id": ident}
            )
            assert pending.status_code == 409
            result = await client.post("/tester/test/run", json={"session_id": ident})
            assert result.status_code == 200, result.text
            report = SuiteReport.model_validate(result.json())
            assert Counter(row.outcome for row in report.results) == {
                "pass": 41,
                "skip": 5,
            }, [(row.test_id, row.outcome, row.message) for row in report.results]
            assert {
                row.test_id for row in report.results if row.outcome == "skip"
            } == GAPS
            assert report.session_id == ident and report.target_url == TARGET
            for measure in (
                report.coverage.code,
                report.coverage.component,
                report.coverage.system,
            ):
                assert measure.covered is measure.total is None
            saved = await client.get(
                "/tester/test/report", params={"session_id": ident}
            )
            assert saved.status_code == 200 and saved.json() == result.json()
            repeated = await client.post("/tester/test/run", json={"session_id": ident})
            assert repeated.status_code == 409
            assert (
                await client.get("/tester/test/report", params={"session_id": ident})
            ).json() == saved.json()
            system = await client.get(
                "/system/test/report", params={"session_id": ident}
            )
            assert system.status_code == 200
            assert SystemReport.model_validate(system.json()).session_id == ident
            assert (await client.get("/api/v1/incidents")).json() == [sentinel]
            assert outbound.requests
            assert all(row.url.path.startswith("/system/") for row in outbound.requests)
            assert all(
                row.headers.get("x-test-session-id") == ident
                and row.headers.get("authorization") is None
                for row in outbound.requests
            )
            with Session(normal) as session:
                assert session.scalars(select(main.IncidentRow.id)).all() == [
                    sentinel["id"]
                ]
                assert session.scalars(select(main.ReviewRow)).all() == []
                assert session.scalars(select(main.DisclosureRow)).all() == []
            assert registry.report(ident).session_id == ident

    asyncio.run(scenario())


def test_course_reset_restores_candidate_fixtures_and_keeps_other_sessions(delivery):
    application, _registry, _normal, _outbound = delivery
    expected = load_fixture_profile()
    fixture_ids = {row["id"] for row in expected["incidents"]}

    async def scenario():
        async with (
            application.router.lifespan_context(application),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url=ORIGIN
            ) as client,
        ):
            assert (
                await client.post("/api/v1/incidents", json=BODY)
            ).status_code == 201
            for ident in ("first", "second"):
                response = await client.post(
                    "/system/test/setup", json={"session_id": ident, "setup": {}}
                )
                assert response.status_code == 201
            first = {"X-Test-Session-Id": "first"}
            extra = await client.post(
                "/system/api/v1/incidents", json=BODY, headers=first
            )
            assert extra.status_code == 201
            primary = expected["incidents"][0]["id"]
            path = "/system/api/v1/incidents/" + primary
            assert (
                await client.post(path + "/assessment", headers=first)
            ).status_code == 200
            assert (
                await client.post(
                    path + "/review",
                    headers=first,
                    json={"decision": "MATERIAL", "reviewer": "Synthetic reviewer"},
                )
            ).status_code == 200
            assert (
                await client.post(path + "/disclosure", headers=first)
            ).status_code == 200
            reset = await client.post("/system/api/v1/test/reset", headers=first)
            assert reset.status_code == 204
            rows = (await client.get("/system/api/v1/incidents", headers=first)).json()
            assert {row["id"] for row in rows} == fixture_ids
            assert all(
                row["state"] == "RECEIVED"
                and row["assessment"] is row["review"] is None
                and row["relatedIncidentIds"] == []
                for row in rows
            )
            assert (await client.get(path + "/reviews", headers=first)).json() == []
            assert (await client.get(path + "/disclosures", headers=first)).json() == []
            # Headerless compatibility selects the newest prepared session.
            second = (await client.get("/system/api/v1/incidents")).json()
            assert {row["id"] for row in second} == fixture_ids
            assert all(row["state"] == "RECEIVED" for row in second)
            assert len((await client.get("/api/v1/incidents")).json()) == 1

    asyncio.run(scenario())


def test_composed_docs_and_mount_aware_media_rules(delivery):
    application, _registry, _normal, _outbound = delivery

    async def scenario():
        async with (
            application.router.lifespan_context(application),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url=ORIGIN
            ) as client,
        ):
            for path, schema_path in (
                ("/docs", "/openapi.json"),
                ("/system/docs", "/system/openapi.json"),
                ("/system/test/docs", "/system/test/openapi.json"),
                ("/tester/docs", "/tester/openapi.json"),
            ):
                response = await client.get(path)
                assert response.status_code == 200 and schema_path in response.text
            normal_schema = (await client.get("/openapi.json")).json()
            system_schema = (await client.get("/system/openapi.json")).json()
            control_schema = (await client.get("/system/test/openapi.json")).json()
            tester_schema = (await client.get("/tester/openapi.json")).json()
            assert "/api/v1/incidents" in normal_schema["paths"]
            assert "/api/v1/incidents" in system_schema["paths"]
            assert set(control_schema["paths"]) == {
                "/test",
                "/test/setup",
                "/test/report",
            }
            assert set(tester_schema["paths"]) == {
                "/test/setup",
                "/test/run",
                "/test/report",
            }
            assert {"url": "/system"} in system_schema["servers"]
            assert {"url": "/system"} in control_schema["servers"]
            assert {"url": "/tester"} in tester_schema["servers"]
            assert (
                await client.post(
                    "/system/test/setup", json={"session_id": "media", "setup": {}}
                )
            ).status_code == 201
            for path in (
                "/system/api/v1/incidents",
                "/system/api/v1/incidents/missing/review",
                "/system/api/v1/incidents/missing/related",
            ):
                response = await client.post(
                    path, content="{}", headers={"Content-Type": "text/plain"}
                )
                assert response.status_code == 415, (path, response.text)
            invalid = await client.post(
                "/system/api/v1/incidents/missing/review", json={}
            )
            assert invalid.status_code == 404
            invalid = await client.post(
                "/system/api/v1/incidents", json={**BODY, "source": None}
            )
            assert invalid.status_code == 400

    asyncio.run(scenario())


def test_course_requests_fail_closed_before_setup_and_for_unknown_header(delivery):
    application, _registry, _normal, outbound = delivery

    async def scenario():
        async with (
            application.router.lifespan_context(application),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url=ORIGIN
            ) as client,
        ):
            assert (await client.get("/health")).status_code == 200
            before = await client.post("/system/api/v1/incidents", json=BODY)
            assert before.status_code == 400 and before.json()["source"] == "system"
            unknown = await client.post(
                "/system/api/v1/incidents",
                json=BODY,
                headers={"X-Test-Session-Id": "not-prepared"},
            )
            assert unknown.status_code == 404 and unknown.json()["source"] == "system"
            assert (await client.get("/api/v1/incidents")).json() == []
            assert (
                await client.post("/tester/test/setup", json={"target_url": ORIGIN})
            ).status_code == 400
            assert outbound.requests == []

    asyncio.run(scenario())


def test_parent_lifespan_initializes_only_normal_facade_and_cleans_course_store(
    tmp_path, monkeypatch
):
    initialized = []
    initialize = main.initialize_database

    def recorded(engine):
        initialized.append(engine)
        initialize(engine)

    monkeypatch.setattr(main, "initialize_database", recorded)
    normal = create_engine(
        "sqlite:///" + (tmp_path / "lifecycle.db").as_posix(),
        connect_args={"check_same_thread": False},
    )
    registry = SystemSessionRegistry(
        course_store_factory(temporary_root=tmp_path),
        recorded,
        seed=make_fixture_seed(),
    )
    transport = ComposedTransport()
    gateway = HttpTargetGateway((TARGET,), transport=transport)
    application = build_delivery_app(
        public_base_url=ORIGIN,
        database_engine=normal,
        registry=registry,
        gateway=gateway,
        approved_targets=(TARGET,),
    )
    transport.application = application

    async def scenario():
        async with application.router.lifespan_context(application):
            assert initialized == [normal]
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url=ORIGIN
            ) as client:
                prepared = await client.post(
                    "/system/test/setup", json={"session_id": "lifecycle", "setup": {}}
                )
                assert prepared.status_code == 201
                assert len(initialized) == 2 and initialized[1] is not normal
        with pytest.raises(ProtocolFailure) as closed:
            registry.report("lifecycle")
        assert closed.value.status_code == 503
        assert list(tmp_path.glob("sec450-course-*")) == []

    try:
        asyncio.run(scenario())
    finally:
        registry.close()
        normal.dispose()
