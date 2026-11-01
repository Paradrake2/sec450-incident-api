"""Dedicated course-system entry point; normal main:app is not reconfigured.

Run with uvicorn course_system:create_course_app --factory. Project requests
cannot reach main's normal engine: a prepared isolated store is required.
Headerless calls are serialized-profile calls to the newest ready session;
X-Test-Session-Id optionally addresses retained sessions explicitly.
"""

from contextlib import asynccontextmanager
import os

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from system_sessions import SESSION_HEADER, SystemSessionRegistry, course_store_factory
from tester_interfaces import ProtocolFailure
from testing_protocol import (
    ProtocolErrorBody,
    SessionResponse,
    SystemReport,
    SystemSetupRequest,
)


def _route_path(scope):
    """Normalize a mounted scope without trusting a request header or URL."""
    path = scope.get("path", "")
    root = scope.get("root_path", "")
    if root and path.startswith(root):
        suffix = path[len(root) :]
        if not suffix or suffix.startswith("/"):
            return suffix or "/"
    return path


class _IsolatedProjectRequests:
    def __init__(self, app, registry):
        self.app, self.registry = app, registry

    async def __call__(self, scope, receive, send):
        path = _route_path(scope)
        if scope["type"] != "http" or not (
            path == "/api/v1" or path.startswith("/api/v1/")
        ):
            return await self.app(scope, receive, send)
        response_start = None
        final_body = None
        bodyless = scope.get("method") == "HEAD"

        async def flush_start(*, streaming=False):
            nonlocal response_start
            if response_start is not None:
                message, response_start = response_start, None
                if streaming:
                    # A Content-Length client may finish before the terminal
                    # ASGI frame. Chunked/end-stream framing must signal the
                    # actual completion boundary after cleanup instead.
                    message["headers"] = [(name, value) for name, value in message.get("headers", [])
                                          if name.lower() != b"content-length"]
                await send(message)

        async def send_before_cleanup(message):
            nonlocal final_body, response_start, bodyless
            if message["type"] == "http.response.start":
                response_start = dict(message)
                bodyless = bodyless or message["status"] in {204, 304} or message["status"] < 200
                return
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                # The client must not observe response completion before the
                # selected request/session has finished dependency cleanup.
                # Otherwise an immediate next TCP request races this lease and
                # receives a spurious busy409 despite serialized client calls.
                final_body = dict(message)
                if bodyless:
                    final_body["body"] = b""
                return
            if message["type"] == "http.response.body":
                if bodyless:
                    return
                await flush_start(streaming=True)
            await send(message)

        try:
            values = [
                value.decode("latin1")
                for name, value in scope.get("headers", [])
                if name.lower() == SESSION_HEADER.lower().encode("ascii")
            ]
            if len(values) > 1:
                raise ProtocolFailure(
                    400, "Supply only one test session header", "system"
                )
            with self.registry.request_lease(values[0] if values else None) as engine:
                request_scope = dict(scope)
                state = dict(scope.get("state", {}))
                state["system_test_engine"] = engine
                state["system_test_seed"] = self.registry.seed
                request_scope["state"] = state
                await self.app(request_scope, receive, send_before_cleanup)
        except ProtocolFailure as exc:
            body = {
                "error": "TEST_SESSION_ERROR",
                "message": exc.message,
                "source": "system",
            }
            response = JSONResponse(status_code=exc.status_code, content=body)
            await response(scope, receive, send)
        finally:
            # Also finish a child-generated error response before its server
            # middleware re-raises for logging. Lease cleanup has already run.
            if final_body is not None:
                await flush_start()
                await send(final_body)


def create_course_app(incident_app=None, registry=None):
    """Wrap the existing API without running its normal-store startup lifespan.

    PostgreSQL is selected only by COURSE_TEST_DATABASE_URL. An absent setting
    gives local disposable SQLite; no application credential is inferred.
    """
    if incident_app is None:
        import main

        incident_app = main.app
        if registry is None:
            shared_flag = os.environ.get("COURSE_TEST_ALLOW_SHARED_DATABASE", "0")
            if shared_flag not in {"0", "1"}:
                raise ValueError("COURSE_TEST_ALLOW_SHARED_DATABASE must be 0 or 1")
            registry = SystemSessionRegistry(
                course_store_factory(
                    os.environ.get("COURSE_TEST_DATABASE_URL"),
                    allow_shared_database=shared_flag == "1",
                ),
                main.initialize_database,
            )
    if registry is None:
        raise ValueError(
            "An injected incident app requires an explicit session registry"
        )

    @asynccontextmanager
    async def lifespan(application):
        try:
            yield
        finally:
            registry.close()

    application = FastAPI(
        title="SEC450 isolated course system",
        version="0.1.0",
        docs_url="/test/docs",
        openapi_url="/test/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    application.state.system_registry = registry
    application.state.system_sessions = registry
    application.add_middleware(_IsolatedProjectRequests, registry=registry)

    @application.exception_handler(ProtocolFailure)
    async def protocol_error(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"message": exc.message, "source": "system"},
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            status_code=400,
            content={
                "message": "Invalid system control body or query",
                "source": "system",
            },
        )

    @application.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse(
            status_code=500,
            content={
                "message": "Unexpected isolated system failure",
                "source": "system",
            },
        )

    @application.middleware("http")
    async def json_setup(request: Request, call_next):
        if request.method == "POST" and _route_path(request.scope) == "/test/setup":
            content_type = (
                request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            )
            if content_type != "application/json":
                return JSONResponse(
                    status_code=400,
                    content={
                        "message": "System setup requires application/json",
                        "source": "system",
                    },
                )
        return await call_next(request)

    errors = {
        status: {"model": ProtocolErrorBody} for status in (400, 404, 409, 500, 503)
    }

    @application.get("/test")
    def index(request: Request):
        base = request.scope.get("root_path", "")
        return {
            "system": ["POST /test/setup", "GET /test/report"],
            "incident_docs": base + "/docs",
            "system_docs": base + "/test/docs",
            "profile": "isolated stores; one headerless suite at a time",
            "optional_session_header": SESSION_HEADER,
        }

    @application.post(
        "/test/setup", status_code=201, response_model=SessionResponse, responses=errors
    )
    def setup(data: SystemSetupRequest):
        return registry.setup(data.session_id, data.setup)

    @application.get("/test/report", response_model=SystemReport, responses=errors)
    def report(session_id: str = Query(min_length=1)):
        return registry.report(session_id)

    application.mount("/", incident_app)

    def system_openapi():
        if application.openapi_schema is None:
            schema = get_openapi(
                title=application.title,
                version=application.version,
                routes=application.routes,
                servers=application.servers,
            )
            for path in schema["paths"].values():
                for operation in path.values():
                    if isinstance(operation, dict) and "responses" in operation:
                        operation["responses"].pop("422", None)
            application.openapi_schema = schema
        return application.openapi_schema

    application.openapi = system_openapi
    return application
