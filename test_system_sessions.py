"""Course stores and HTTP routing use synthetic, isolated data only."""

from contextlib import asynccontextmanager
import asyncio
import os
import socket

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from fastapi.responses import StreamingResponse
import httpx
import pytest
from sqlalchemy import (
    Column,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    insert,
    select,
)
from sqlalchemy.orm import Session

from course_system import _IsolatedProjectRequests, create_course_app
from system_sessions import (
    SystemSessionRegistry,
    course_postgres_url,
    course_store_factory,
)
from tester_interfaces import ProtocolFailure
from testing_protocol import SystemReport


metadata = MetaData()
items = Table(
    "synthetic_course_items",
    metadata,
    Column("id", String, primary_key=True),
    Column("value", String),
)


def initialize(engine):
    metadata.create_all(engine)


def seed(session):
    session.execute(insert(items).values(id="fixture", value="synthetic seed"))


def fake_incident_app(normal_engine):
    lifespan_calls = []

    @asynccontextmanager
    async def lifespan(app):
        lifespan_calls.append("normal startup")
        yield

    app = FastAPI(lifespan=lifespan)
    app.state.database_engine = normal_engine
    app.state.lifespan_calls = lifespan_calls

    def engine_for(request):
        return getattr(request.state, "system_test_engine", app.state.database_engine)

    @app.get("/api/v1/items")
    def listed(request: Request):
        with engine_for(request).connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    select(items).order_by(items.c.id)
                ).mappings()
            ]

    @app.post("/api/v1/items/{ident}")
    def created(ident: str, request: Request):
        with engine_for(request).begin() as connection:
            connection.execute(insert(items).values(id=ident, value="synthetic write"))
        return {"id": ident}

    @app.post("/api/v1/test/reset")
    def reset(request: Request):
        with Session(engine_for(request)) as session:
            session.execute(delete(items))
            callback = getattr(request.state, "system_test_seed", None)
            if callback is not None:
                callback(session)
            session.commit()
        return {"reset": True}

    @app.get("/api/v1/explode")
    def explode():
        raise RuntimeError("private synthetic failure")

    return app


@pytest.fixture
def isolated_system(tmp_path):
    normal = create_engine(
        "sqlite:///" + (tmp_path / "normal.db").as_posix(),
        connect_args={"check_same_thread": False},
    )
    initialize(normal)
    with normal.begin() as connection:
        connection.execute(insert(items).values(id="normal", value="normal sentinel"))
    app = fake_incident_app(normal)
    registry = SystemSessionRegistry(
        course_store_factory(temporary_root=tmp_path), initialize, seed=seed
    )
    wrapper = create_course_app(app, registry)
    yield wrapper, registry, app, normal
    registry.close()
    normal.dispose()


def setup(client, ident):
    response = client.post("/test/setup", json={"session_id": ident, "setup": {}})
    assert response.status_code == 201, response.text


def test_course_requests_never_fall_back_and_reset_restores_only_selected_store(
    isolated_system,
):
    wrapper, registry, app, normal = isolated_system
    with TestClient(wrapper) as client:
        assert client.get("/api/v1/items").status_code == 400
        setup(client, "first")
        assert client.post("/api/v1/items/first-only").status_code == 200
        setup(client, "second")
        assert client.get("/api/v1/items").json() == [
            {"id": "fixture", "value": "synthetic seed"}
        ]
        assert client.post("/api/v1/items/second-only").status_code == 200
        first_header = {"X-Test-Session-Id": "first"}
        assert {
            item["id"]
            for item in client.get("/api/v1/items", headers=first_header).json()
        } == {"fixture", "first-only"}
        assert (
            client.post("/api/v1/test/reset", headers=first_header).status_code == 200
        )
        assert client.get("/api/v1/items", headers=first_header).json() == [
            {"id": "fixture", "value": "synthetic seed"}
        ]
        assert {item["id"] for item in client.get("/api/v1/items").json()} == {
            "fixture",
            "second-only",
        }
        unknown = client.get("/api/v1/items", headers={"X-Test-Session-Id": "unknown"})
        assert unknown.status_code == 404 and unknown.json()["source"] == "system"
        valid = SystemReport.model_validate(
            client.get("/test/report", params={"session_id": "first"}).json()
        )
        assert valid.coverage.code.covered is valid.coverage.code.total is None
    assert app.state.lifespan_calls == []
    with normal.connect() as connection:
        assert connection.execute(select(items.c.id)).scalars().all() == ["normal"]


def test_guard_and_media_validation_work_under_parent_mount(isolated_system):
    wrapper, registry, app, normal = isolated_system
    parent = FastAPI()
    parent.mount("/system", wrapper)
    parent.mount("/", app)
    with TestClient(parent) as client:
        assert client.get("/system/api/v1/items").status_code == 400
        assert (
            client.post(
                "/system/test/setup",
                content="{}",
                headers={"content-type": "text/plain"},
            ).status_code
            == 400
        )
        response = client.post(
            "/system/test/setup", json={"session_id": "mounted", "setup": {}}
        )
        assert response.status_code == 201
        assert client.get("/system/api/v1/items").json() == [
            {"id": "fixture", "value": "synthetic seed"}
        ]
        assert client.get("/api/v1/items").json() == [
            {"id": "normal", "value": "normal sentinel"}
        ]
        assert client.get("/system/test").json()["incident_docs"] == "/system/docs"
    assert app.state.lifespan_calls == []


def test_duplicate_headers_do_not_select_an_arbitrary_store(isolated_system):
    wrapper, registry, app, normal = isolated_system
    with TestClient(wrapper) as client:
        setup(client, "first")
        setup(client, "second")
        response = client.get(
            "/api/v1/items",
            headers=[("X-Test-Session-Id", "first"), ("X-Test-Session-Id", "second")],
        )
        assert response.status_code == 400
        assert response.json()["source"] == "system"


def test_failed_project_request_releases_its_session_lease(isolated_system):
    wrapper, registry, app, normal = isolated_system
    with TestClient(wrapper, raise_server_exceptions=False) as client:
        setup(client, "failed-request")
        assert client.get("/api/v1/explode").status_code == 500
        assert client.get("/api/v1/items").status_code == 200


def test_concurrent_raw_asgi_requests_copy_shared_lifespan_state(isolated_system):
    wrapper, registry, app, normal = isolated_system
    registry.setup("first", {})
    registry.setup("second", {})
    expected = {}
    for ident in ("first", "second"):
        with registry.request_lease(ident) as engine:
            expected[ident] = engine

    shared_state = {
        "lifespan_value": object(),
        "system_test_engine": "normal engine sentinel",
        "system_test_seed": "normal seed sentinel",
    }
    original_state = shared_state.copy()

    async def scenario():
        finish = asyncio.Event()
        entered = {ident: asyncio.Event() for ident in expected}
        observed_states = {}

        async def child(scope, receive, send):
            ident = scope["headers"][0][1].decode("ascii")
            state = scope["state"]
            observed_states[ident] = state
            assert state["system_test_engine"] is expected[ident]
            assert state["system_test_seed"] is registry.seed
            entered[ident].set()
            await finish.wait()
            assert state["system_test_engine"] is expected[ident]
            state["request_private"] = ident
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            pass

        guarded = _IsolatedProjectRequests(child, registry)
        tasks = []
        try:
            for ident in expected:
                scope = {
                    "type": "http",
                    "path": "/api/v1/shared-state",
                    "root_path": "",
                    "state": shared_state,
                    "headers": [(b"x-test-session-id", ident.encode("ascii"))],
                }
                tasks.append(asyncio.create_task(guarded(scope, receive, send)))
                await asyncio.wait_for(entered[ident].wait(), 2)
                assert scope["state"] is shared_state
                assert shared_state == original_state
            assert observed_states["first"] is not observed_states["second"]
            assert all(state is not shared_state for state in observed_states.values())
        finally:
            finish.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 2)
        assert shared_state == original_state

    asyncio.run(scenario())


def test_course_lifespan_closes_stores_even_when_the_body_fails(isolated_system):
    wrapper, registry, app, normal = isolated_system
    registry.setup("cleanup-on-failure", {})

    async def scenario():
        with pytest.raises(ValueError, match="synthetic lifespan failure"):
            async with wrapper.router.lifespan_context(wrapper):
                raise ValueError("synthetic lifespan failure")

    asyncio.run(scenario())
    with pytest.raises(ProtocolFailure) as closed:
        registry.report("cleanup-on-failure")
    assert closed.value.status_code == 503


@pytest.mark.parametrize("error_response", [False, True])
def test_response_completion_is_sent_only_after_cleanup_and_lease_release(
    isolated_system, error_response
):
    wrapper, registry, app, normal = isolated_system
    registry.setup("response-boundary", {})

    async def scenario():
        events = []

        async def child(scope, receive, send):
            await send({"type": "http.response.start", "status": 500 if error_response else 200, "headers": []})
            await send({"type": "http.response.body", "body": b"first", "more_body": True})
            await send({"type": "http.response.body", "body": b"last"})
            events.append("cleanup")
            # Cleanup still owns the lease after the child emits its last body.
            with pytest.raises(ProtocolFailure):
                with registry.request_lease("response-boundary"):
                    pytest.fail("Request lease released before dependency cleanup")
            if error_response:
                raise RuntimeError("child middleware logs after sending its error response")

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                assert events == ["cleanup"]
                with registry.request_lease("response-boundary"):
                    events.append("client-completion")

        scope = {"type": "http", "path": "/api/v1/synthetic", "headers": [], "state": {}}
        guarded = _IsolatedProjectRequests(child, registry)
        if error_response:
            with pytest.raises(RuntimeError, match="child middleware"):
                await guarded(scope, receive, send)
        else:
            await guarded(scope, receive, send)
        assert events == ["cleanup", "client-completion"]

    asyncio.run(scenario())


@pytest.mark.parametrize("status,method", [(204, "POST"), (200, "GET"), (200, "HEAD")])
def test_bodyless_response_headers_wait_for_cleanup(isolated_system, status, method):
    wrapper, registry, app, normal = isolated_system
    registry.setup("bodyless", {})

    async def scenario():
        cleaned = False
        sent = []

        async def child(scope, receive, send):
            nonlocal cleaned
            await send({"type": "http.response.start", "status": status,
                        "headers": [(b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            assert not sent
            cleaned = True

        async def send(message):
            assert cleaned
            with registry.request_lease("bodyless"):
                sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b""}

        await _IsolatedProjectRequests(child, registry)(
            {"type": "http", "path": "/api/v1/synthetic", "method": method, "headers": []}, receive, send)
        assert [message["type"] for message in sent] == ["http.response.start", "http.response.body"]

    asyncio.run(scenario())


def test_streaming_content_length_cannot_signal_completion_before_cleanup(isolated_system):
    wrapper, registry, app, normal = isolated_system
    registry.setup("stream-length", {})

    async def scenario():
        cleaned = False
        sent = []

        async def child(scope, receive, send):
            nonlocal cleaned
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-length", b"3"), (b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"all", "more_body": True})
            assert sent[0]["headers"] == [(b"content-type", b"text/plain")]
            assert len(sent) == 2
            await send({"type": "http.response.body", "body": b""})
            assert len(sent) == 2
            cleaned = True

        async def send(message):
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                assert cleaned
                with registry.request_lease("stream-length"):
                    pass
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b""}

        await _IsolatedProjectRequests(child, registry)(
            {"type": "http", "path": "/api/v1/synthetic", "method": "GET", "headers": []}, receive, send)
        assert len(sent) == 3

    asyncio.run(scenario())


def test_request_lease_is_held_until_the_stream_body_finishes(isolated_system):
    wrapper, registry, app, normal = isolated_system

    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()

        @app.get("/api/v1/stream")
        async def streaming():
            async def body():
                yield b"first"
                started.set()
                await finish.wait()
                yield b"last"

            return StreamingResponse(body())

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=wrapper), base_url="http://testserver"
        ) as client:
            assert (
                await client.post(
                    "/test/setup", json={"session_id": "stream", "setup": {}}
                )
            ).status_code == 201
            task = asyncio.create_task(client.get("/api/v1/stream"))
            try:
                await asyncio.wait_for(started.wait(), 2)
                busy = await client.get("/api/v1/items")
                assert busy.status_code == 409
            finally:
                finish.set()
            assert (await task).content == b"firstlast"
            assert (await client.get("/api/v1/items")).status_code == 200

    asyncio.run(scenario())


def test_request_lease_is_held_until_request_dependency_cleanup(isolated_system):
    wrapper, registry, app, normal = isolated_system

    async def scenario():
        cleaning, finish = asyncio.Event(), asyncio.Event()

        async def dependency():
            try:
                yield
            finally:
                cleaning.set()
                await finish.wait()

        @app.get("/api/v1/cleanup")
        async def with_cleanup(prepared=Depends(dependency)):
            return {"synthetic": True}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=wrapper), base_url="http://testserver"
        ) as client:
            assert (
                await client.post(
                    "/test/setup", json={"session_id": "cleanup", "setup": {}}
                )
            ).status_code == 201
            task = asyncio.create_task(client.get("/api/v1/cleanup"))
            try:
                await asyncio.wait_for(cleaning.wait(), 2)
                assert (await client.get("/api/v1/items")).status_code == 409
            finally:
                finish.set()
            assert (await task).status_code == 200
            assert (await client.get("/api/v1/items")).status_code == 200

    asyncio.run(scenario())


def test_capacity_and_busy_states_reject_without_evicting_or_switching_stores(tmp_path):
    registry = SystemSessionRegistry(
        course_store_factory(temporary_root=tmp_path), initialize, max_sessions=1
    )
    registry.setup("retained", {})
    with pytest.raises(ProtocolFailure) as full:
        registry.setup("new", {})
    assert full.value.status_code == 503
    assert registry.report("retained").session_id == "retained"
    with registry.request_lease("retained"):
        with pytest.raises(ProtocolFailure) as busy:
            with registry.request_lease("retained"):
                pytest.fail("A busy session cannot have two requests")
        assert busy.value.status_code == 409
        with pytest.raises(RuntimeError):
            registry.close()
    registry.close()
    assert list(tmp_path.iterdir()) == []


def test_failed_setup_cleans_resources_and_does_not_replace_the_ready_session(tmp_path):
    calls = []

    def initialization(engine):
        calls.append(engine)
        if len(calls) == 2:
            raise RuntimeError("private configuration or database detail")
        initialize(engine)

    registry = SystemSessionRegistry(
        course_store_factory(temporary_root=tmp_path), initialization
    )
    registry.setup("first", {})
    with registry.request_lease() as first_engine:
        pass
    with pytest.raises(ProtocolFailure) as failed:
        registry.setup("retry", {})
    assert failed.value.status_code == 500
    assert "private" not in failed.value.message
    with registry.request_lease() as selected:
        assert selected is first_engine
    with pytest.raises(ProtocolFailure) as absent:
        registry.report("retry")
    assert absent.value.status_code == 404
    assert len(list(tmp_path.iterdir())) == 1
    assert registry.setup("retry", {}).session_id == "retry"
    registry.close()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"session_id": " ", "setup": {}},
        {"session_id": "x", "setup": []},
        {"session_id": "x", "setup": {"database_url": "private"}},
        {"session_id": "x", "setup": {}, "extra": True},
    ],
)
def test_invalid_control_requests_create_no_store(isolated_system, payload):
    wrapper, registry, app, normal = isolated_system
    with TestClient(wrapper) as client:
        response = client.post("/test/setup", json=payload)
        assert response.status_code == 400
        assert response.json()["source"] == "system"
        assert client.get("/test/report", params={"session_id": "x"}).status_code == 404


def test_system_openapi_and_duplicate_setup_are_explicit(isolated_system):
    wrapper, registry, app, normal = isolated_system
    with TestClient(wrapper) as client:
        setup(client, "known")
        assert (
            client.post(
                "/test/setup", json={"session_id": "known", "setup": {}}
            ).status_code
            == 409
        )
        assert client.get("/test/report").status_code == 400
        schema = client.get("/test/openapi.json").json()
        assert set(schema["paths"]) == {"/test", "/test/setup", "/test/report"}
        for path in ("/test/setup", "/test/report"):
            for operation in schema["paths"][path].values():
                assert "422" not in operation["responses"]


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg://u:p@127.0.0.1:55432/sec450_test",
        "postgres://u:p@localhost/test_course",
        "postgresql://u:p@db.example/sec450_test?sslmode=verify-full",
    ],
)
def test_course_postgres_explicit_test_database_urls_are_accepted(url):
    assert course_postgres_url(url).get_backend_name() == "postgresql"


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///normal.db",
        "postgresql:///sec450_test",
        "postgresql://u:p@db.example/company",
        "postgresql://u:p@db.example/course_test",
        "postgresql://u:p@localhost/course_test?options=-csearch_path=public",
        "postgresql://u:p@localhost/course_test?host=company.example",
        "postgresql://u:p@localhost/course_test?hostaddr=203.0.113.1",
        "postgresql://u:p@localhost:0/course_test",
        "postgresql://u:p@localhost:65536/course_test",
    ],
)
def test_course_store_guard_rejects_unsafe_or_implicit_targets_without_connecting(url):
    with pytest.raises(ValueError) as error:
        course_postgres_url(url)
    assert url not in str(error.value)


def test_shared_physical_database_requires_explicit_opt_in_and_still_rejects_routing():
    value = "postgresql://u:private@db.example/company?sslmode=require"
    with pytest.raises(ValueError):
        course_postgres_url(value)
    assert course_postgres_url(value, allow_shared_database=True).database == "company"
    with pytest.raises(ValueError):
        course_postgres_url(
            value + "&options=-csearch_path=public", allow_shared_database=True
        )


@pytest.mark.parametrize(
    ("host", "port", "address"),
    [
        ("localhost", None, "127.0.0.1"),
        ("192.0.2.1", 6543, "192.0.2.1"),
        ("::1", None, "::1"),
        ("db.example", None, "203.0.113.4"),
    ],
)
def test_shared_database_factory_only_creates_and_drops_its_generated_schema(
    monkeypatch,
    host,
    port,
    address,
):
    import system_sessions

    engines, statements, resolutions = [], [], []
    for name, value in {
        "PGHOST": "unrelated.example",
        "PGHOSTADDR": "198.51.100.200",
        "PGPORT": "6544",
        "PGDATABASE": "unrelated_database",
    }.items():
        monkeypatch.setenv(name, value)

    def resolve(selected_host, selected_port, **kwargs):
        resolutions.append((selected_host, selected_port, kwargs))
        assert selected_host == "db.example"
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, selected_port),
            )
        ]

    monkeypatch.setattr(system_sessions.socket, "getaddrinfo", resolve)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, statement):
            statements.append(str(statement))

    class Engine:
        def __init__(self, url, options):
            self.url = url
            self.options = options
            self.disposed = False

        def connect(self):
            return Connection()

        def dispose(self):
            self.disposed = True

    def create(url, **kwargs):
        engine = Engine(url, kwargs)
        engines.append(engine)
        return engine

    monkeypatch.setattr(system_sessions, "create_engine", create)
    authority = "[" + host + "]" if ":" in host else host
    if port is not None:
        authority += ":" + str(port)
    factory = course_store_factory(
        "postgresql://u:private@" + authority + "/company?sslmode=require",
        allow_shared_database=True,
    )
    with factory() as engine:
        assert engine.url.database == "company"
        assert engine.url.query["sslmode"] == "require"
        assert engine.url.query["connect_timeout"] == "5"
        assert engine.url.host == host
        assert engine.url.port == (port or 5432)
        options = engine.url.query["options"]
        assert (
            options.startswith("-csearch_path=sec450_course_")
            and "public" not in options
        )
        generated = options.split("=", 2)[-1]
        assert statements == ["CREATE SCHEMA " + generated]
    assert statements == [
        "CREATE SCHEMA " + generated,
        "DROP SCHEMA " + generated + " CASCADE",
    ]
    assert all(engine.disposed for engine in engines)
    assert len(engines) == 2
    for engine in engines:
        assert engine.options["connect_args"] == {
            "host": host,
            "hostaddr": address,
            "port": port or 5432,
            "dbname": "company",
        }
    assert len(resolutions) == (1 if host == "db.example" else 0)


def test_course_host_resolution_failure_is_sanitized_and_creates_no_engine(monkeypatch):
    import system_sessions

    def resolve(*args, **kwargs):
        raise socket.gaierror("private DNS details")

    def unexpected_engine(*args, **kwargs):
        pytest.fail("An unresolved target cannot create a database engine")

    monkeypatch.setattr(system_sessions.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(system_sessions, "create_engine", unexpected_engine)
    factory = course_store_factory(
        "postgresql://u:private@db.example/course_test?sslmode=require"
    )
    with pytest.raises(ValueError) as error:
        with factory():
            pytest.fail("An unresolved target cannot provide a store")
    assert "private" not in str(error.value)


def test_omitted_course_url_never_reads_application_or_pytest_database_settings(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", "postgresql://private@company.example/company")
    monkeypatch.setenv(
        "TEST_DATABASE_URL", "postgresql://private@company.example/test_company"
    )
    with course_store_factory(temporary_root=tmp_path)() as engine:
        assert engine.dialect.name == "sqlite"
    assert list(tmp_path.iterdir()) == []


def test_explicit_course_postgres_schema_has_no_public_fallback():
    value = os.environ.get("TEST_DATABASE_URL")
    if value is None:
        pytest.skip("An explicit local PostgreSQL regression database is required")
    with course_store_factory(value)() as engine:
        assert engine.dialect.name == "postgresql"
        options = engine.url.query["options"]
        assert options.startswith("-csearch_path=sec450_course_")
        assert "public" not in options
        initialize(engine)
