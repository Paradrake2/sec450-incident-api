"""Course tester controls, with networking and project cases supplied explicitly."""

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from tester_core import TesterCoordinator
from tester_interfaces import ProtocolFailure
from tester_targets import ApprovedTargets
from testing_protocol import (
    ProtocolErrorBody,
    SessionResponse,
    SuiteReport,
    TesterRunRequest,
    TesterSetupRequest,
)


ERROR_RESPONSES = {
    status: {"model": ProtocolErrorBody, "description": description}
    for status, description in {
        400: "Invalid protocol request or unapproved target",
        404: "Unknown session",
        409: "Session conflict or report not ready",
        500: "Unconfigured tester, setup failure, or saved tester abort",
        "default": "Classified setup failure, including forwarded target errors",
    }.items()
}


def create_tester_app(
    coordinator: TesterCoordinator | None = None,
    approved_targets: tuple[str, ...] = (),
) -> FastAPI:
    """Build an independent protocol app; an unconfigured instance fails closed.

    No gateway, cases, or approved targets are inferred from request data or
    environment variables. The default instance cannot make network requests.
    An actual outbound adapter and project suite remain separate milestones.
    """
    service = coordinator if coordinator is not None else TesterCoordinator()
    targets = ApprovedTargets(approved_targets)
    application = FastAPI(
        title="SEC450 Tester Protocol Foundation",
        version="0.1.0",
        description=(
            "Setup, one-run execution, and immutable report retrieval. The "
            "default service has no outbound adapter or agreed project suite; "
            "setup fails closed until both are explicitly configured. "
            "Mock-target validation does not establish cross-team conformance."
        ),
    )
    application.state.coordinator = service

    @application.exception_handler(ProtocolFailure)
    async def protocol_failure(request: Request, exc: ProtocolFailure):
        body = ProtocolErrorBody(message=exc.message, source=exc.source)
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=400, content={
            "message": "Invalid protocol request body or query parameters",
            "source": "tester",
        })

    @application.exception_handler(HTTPException)
    async def protocol_http_error(request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={
            "message": str(exc.detail), "source": "tester",
        })

    @application.exception_handler(Exception)
    async def unexpected_failure(request: Request, exc: Exception):
        return JSONResponse(status_code=500, content={
            "message": "Unexpected tester failure", "source": "tester",
        })

    @application.middleware("http")
    async def require_protocol_json(request: Request, call_next):
        if request.method == "POST" and request.url.path.endswith(("/test/setup", "/test/run")):
            content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                return JSONResponse(status_code=400, content={
                    "message": "Protocol POST requests require application/json",
                    "source": "tester",
                })
        return await call_next(request)

    @application.post(
        "/test/setup", status_code=201, response_model=SessionResponse,
        responses=ERROR_RESPONSES,
    )
    async def setup(data: TesterSetupRequest):
        if not service.configured:
            raise ProtocolFailure(500, "Tester target adapter and project suite are not configured", "tester")
        target = targets.normalize_and_require(data.target_url)
        return await service.setup(target)

    @application.post("/test/run", response_model=SuiteReport, responses=ERROR_RESPONSES)
    async def run(data: TesterRunRequest):
        return await service.run(data.session_id)

    @application.get("/test/report", response_model=SuiteReport, responses=ERROR_RESPONSES)
    async def report(session_id: str = Query(min_length=1)):
        if not session_id.strip():
            raise ProtocolFailure(400, "session_id must not be blank", "tester")
        return service.report(session_id)

    def protocol_openapi():
        if application.openapi_schema is None:
            schema = get_openapi(
                title=application.title,
                version=application.version,
                description=application.description,
                routes=application.routes,
                servers=application.servers,
            )
            for path in schema["paths"].values():
                for operation in path.values():
                    if isinstance(operation, dict) and "responses" in operation:
                        operation["responses"].pop("422", None)
            application.openapi_schema = schema
        return application.openapi_schema

    application.openapi = protocol_openapi
    return application
