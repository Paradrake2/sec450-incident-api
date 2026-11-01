"""Phase 4 delivery: normal product API plus isolated system and real tester.

Public deployment requires explicit course PostgreSQL configuration. The local
factory opts into fixed loopback targets and disposable SQLite unless a course
PostgreSQL URL is supplied; request bodies cannot enable local network access.
"""

import argparse
from contextlib import asynccontextmanager
import ipaddress
import json
import logging
import os
import re
from urllib.parse import urlsplit

from fastapi import FastAPI
from sqlalchemy.engine import make_url

from course_fixtures import load_fixture_profile, make_fixture_seed
from course_system import create_course_app
from incident_suite import build_incident_cases
from system_sessions import SystemSessionRegistry, course_store_factory
from tester_app import create_tester_app
from tester_core import TesterCoordinator
from tester_http import HttpTargetGateway
from tester_targets import ApprovedTargets


DEFAULT_ORIGIN = "https://sec450-incident-api.onrender.com"
PEER_BASE = "https://seclog.redinbo.win"
MAX_SESSIONS = 32
SETUP_SECONDS = 30
RUN_SECONDS = 90
log = logging.getLogger("sec450.delivery")


def _course_url(raw):
    if not raw:
        return None
    try:
        url = make_url(raw)
        try:
            local = ipaddress.ip_address(url.host).is_loopback
        except ValueError:
            local = url.host == "localhost"
        if not local and "sslmode" not in url.query:
            url = url.update_query_dict({"sslmode": "require"})
        return url.render_as_string(hide_password=False)
    except Exception:
        raise ValueError("Invalid explicit course database configuration") from None


def _build_commit():
    value = os.environ.get("RENDER_GIT_COMMIT") or os.environ.get("BUILD_COMMIT")
    return value.lower() if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{40}", value) else None


def build_delivery_app(*, public_base_url, local=False, database_engine=None,
                       registry=None, gateway=None, approved_targets=None):
    from main import create_incident_app, initialize_database

    if type(local) is not bool:
        raise ValueError("Local delivery mode must be an explicit boolean")
    parsed = urlsplit(public_base_url)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("Public base URL must be an origin without credentials or query")
    if local and parsed.hostname not in {"127.0.0.1", "::1"}:
        raise ValueError("Local delivery mode requires a loopback IP origin")
    system_base = ApprovedTargets((public_base_url.rstrip("/") + "/system",), allow_loopback=local).targets[0]
    if not local and not system_base.startswith("https://"):
        raise ValueError("Public delivery requires HTTPS")
    normal = create_incident_app(database_engine)
    isolated = create_incident_app(database_engine)
    profile = load_fixture_profile()
    if registry is None:
        url = _course_url(os.environ.get("COURSE_TEST_DATABASE_URL"))
        if url is None and not local:
            raise ValueError("Public delivery requires COURSE_TEST_DATABASE_URL; use the local factory for development")
        shared = os.environ.get("COURSE_TEST_ALLOW_SHARED_DATABASE", "0")
        if shared not in {"0", "1"}:
            raise ValueError("COURSE_TEST_ALLOW_SHARED_DATABASE must be 0 or 1")
        registry = SystemSessionRegistry(
            course_store_factory(url, allow_shared_database=shared == "1"),
            initialize_database, max_sessions=MAX_SESSIONS, seed=make_fixture_seed(profile),
        )
    course = create_course_app(incident_app=isolated, registry=registry)
    if approved_targets is None:
        raw = os.environ.get("TESTER_ALLOWED_TARGETS")
        try:
            extra = [] if not raw else json.loads(raw)
        except ValueError:
            raise ValueError("TESTER_ALLOWED_TARGETS must be a JSON array of approved bases") from None
        if not isinstance(extra, list) or any(not isinstance(value, str) for value in extra):
            raise ValueError("TESTER_ALLOWED_TARGETS must be a JSON string array")
        approved_targets = tuple(dict.fromkeys([system_base, PEER_BASE, *extra]))
    approved_targets = ApprovedTargets(approved_targets, allow_loopback=local).targets
    gateway = gateway or HttpTargetGateway(approved_targets, allow_loopback=local, timeout=15)
    cases = build_incident_cases(gateway, isolated_reset_targets=(system_base,), allow_loopback=local)
    coordinator = TesterCoordinator(
        gateway=gateway, cases=cases, setup_timeout=SETUP_SECONDS,
        run_timeout=RUN_SECONDS, max_sessions=MAX_SESSIONS,
    )
    tester = create_tester_app(coordinator, approved_targets, allow_loopback=local)

    @asynccontextmanager
    async def lifespan(application):
        try:
            async with normal.router.lifespan_context(normal):
                async with course.router.lifespan_context(course):
                    log.info("delivery ready mode=%s commit=%s", "local" if local else "public", _build_commit() or "unknown")
                    yield
        finally:
            await gateway.aclose()

    application = FastAPI(title="SEC450 Phase 4 delivery", docs_url=None,
                          openapi_url=None, redoc_url=None, lifespan=lifespan)
    application.state.system_app = course
    application.state.tester_app = tester
    application.state.normal_app = normal
    application.state.gateway = gateway
    application.state.system_base = system_base

    @application.get("/")
    async def overview():
        return {
            "service": "SEC450 incident management", "mode": "local" if local else "public",
            "incident_docs": "/docs", "system_base": system_base,
            "system_docs": "/system/test/docs", "isolated_incident_docs": "/system/docs",
            "tester_docs": "/tester/docs", "version": "/version",
            "fixture_profile": profile["profile_id"],
            "fixture_agreement": profile["agreement_status"],
            "notes": "Use /system for test setup and isolated project requests; the product API remains at the root.",
        }

    @application.get("/version")
    async def version():
        return {"service": "sec450-incident-api", "commit": _build_commit(),
                "system_profile": profile["profile_id"], "mode": "local" if local else "public"}

    application.mount("/system", course)
    application.mount("/tester", tester)
    application.mount("/", normal)
    return application


def create_app():
    return build_delivery_app(public_base_url=os.environ.get("PUBLIC_BASE_URL", DEFAULT_ORIGIN))


def create_local_app():
    return build_delivery_app(public_base_url="http://127.0.0.1:8002", local=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local", action="store_true")
    args = parser.parse_args(argv)
    import uvicorn
    application = create_local_app() if args.local else create_app()
    uvicorn.run(application, host="127.0.0.1" if args.local else "0.0.0.0",
                port=8002 if args.local else int(os.environ.get("PORT", "8000")), workers=1)


if __name__ == "__main__":
    raise SystemExit(main())
