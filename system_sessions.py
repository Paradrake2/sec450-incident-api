"""Isolated course-system stores; never selects the application's database.

Headerless calls use the newest prepared session for serialized compatibility.
An explicit header selects a retained session. Neither path falls back to a
normal store. Stores live until graceful shutdown; capacity rejects new setup.
"""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import ipaddress
from pathlib import Path
import re
import socket
import tempfile
from threading import Lock
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from tester_interfaces import ProtocolFailure
from testing_protocol import (
    CoverageMeasure,
    SessionResponse,
    SystemCoverage,
    SystemReport,
)


SESSION_HEADER = "X-Test-Session-Id"
_ALLOWED_QUERY = {
    "sslmode",
    "sslrootcert",
    "sslcert",
    "sslkey",
    "application_name",
    "connect_timeout",
}


def course_postgres_url(raw_url, *, allow_shared_database=False):
    """Require explicit test-only storage and reject incoming routing overrides."""
    try:
        url = make_url(raw_url)
    except (ArgumentError, TypeError, ValueError):
        raise ValueError(
            "COURSE_TEST_DATABASE_URL must be a PostgreSQL test database"
        ) from None
    if url.drivername not in {
        "postgres",
        "postgresql",
        "postgresql+psycopg",
        "postgresql+psycopg2",
    }:
        raise ValueError("COURSE_TEST_DATABASE_URL must select PostgreSQL")
    if (
        not url.host
        or not url.database
        or not re.fullmatch(r"[A-Za-z0-9_]+", url.database)
    ):
        raise ValueError(
            "COURSE_TEST_DATABASE_URL needs an explicit host and test database"
        )
    if url.port is not None and not 1 <= url.port <= 65535:
        raise ValueError("Course database port must be between 1 and 65535")
    if type(allow_shared_database) is not bool:
        raise ValueError("Shared database opt-in must be an explicit boolean")
    if not allow_shared_database and not (
        url.database.startswith("test_") or url.database.endswith("_test")
    ):
        raise ValueError("Course database must start with test_ or end with _test")
    if set(url.query) - _ALLOWED_QUERY:
        raise ValueError("Course database URL contains unsupported routing options")
    timeout = url.query.get("connect_timeout", "5")
    if (
        not isinstance(timeout, str)
        or not timeout.isascii()
        or not timeout.isdecimal()
        or not 1 <= int(timeout) <= 30
    ):
        raise ValueError(
            "Course database connection timeout must be between 1 and 30 seconds"
        )
    try:
        loopback = ipaddress.ip_address(url.host).is_loopback
    except ValueError:
        loopback = url.host == "localhost"
    if not loopback and url.query.get("sslmode") not in {
        "require",
        "verify-ca",
        "verify-full",
    }:
        raise ValueError(
            "Remote course database connections require explicit TLS configuration"
        )
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    return url.set(port=url.port or 5432).update_query_dict(
        {"connect_timeout": timeout}
    )


def _course_connection_arguments(url):
    """Pin libpq routing to the configured target, ignoring ambient PG defaults."""
    if url.host == "localhost":
        address = "127.0.0.1"
    else:
        try:
            ipaddress.ip_address(url.host)
            address = url.host
        except ValueError:
            try:
                resolved = socket.getaddrinfo(
                    url.host,
                    url.port,
                    family=socket.AF_UNSPEC,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                )
                address = next(
                    item[4][0]
                    for item in resolved
                    if item[0] in {socket.AF_INET, socket.AF_INET6}
                )
                ipaddress.ip_address(address)
            except (OSError, StopIteration, ValueError):
                raise ValueError(
                    "Could not resolve the configured course database host"
                ) from None
    return {
        "host": url.host,
        "port": url.port,
        "dbname": url.database,
        "hostaddr": address,
    }


def course_store_factory(
    database_url=None, *, temporary_root=None, allow_shared_database=False
):
    """Return independent stores; no DATABASE_URL or TEST_DATABASE_URL fallback.

    An omitted URL uses disposable local SQLite. PostgreSQL credentials must
    permit CREATE SCHEMA. Test-named databases are required unless trusted
    configuration explicitly opts into a shared physical database. Only the
    generated schema is dropped; existing schemas are never inspected.
    """
    url = (
        None
        if database_url is None
        else course_postgres_url(
            database_url, allow_shared_database=allow_shared_database
        )
    )

    @contextmanager
    def store():
        if url is None:
            with tempfile.TemporaryDirectory(
                prefix="sec450-course-", dir=temporary_root
            ) as folder:
                path = (Path(folder) / "incidents.db").as_posix()
                engine = create_engine(
                    "sqlite:///" + path, connect_args={"check_same_thread": False}
                )
                try:
                    yield engine
                finally:
                    engine.dispose()
            return
        routing = _course_connection_arguments(url)
        admin = create_engine(
            url,
            isolation_level="AUTOCOMMIT",
            pool_pre_ping=True,
            connect_args=dict(routing),
        )
        schema = "sec450_course_" + uuid4().hex
        engine = None
        created = False
        try:
            with admin.connect() as connection:
                connection.execute(CreateSchema(schema))
            created = True
            scoped = url.update_query_dict({"options": "-csearch_path=" + schema})
            engine = create_engine(
                scoped, pool_pre_ping=True, connect_args=dict(routing)
            )
            yield engine
        finally:
            if engine is not None:
                engine.dispose()
            try:
                if created:
                    with admin.connect() as connection:
                        connection.execute(DropSchema(schema, cascade=True))
            finally:
                admin.dispose()

    return store


@dataclass
class _SystemSession:
    engine: object
    resources: ExitStack
    busy: bool = False


class SystemSessionRegistry:
    """One-process registry with short cross-thread locks and no eviction."""

    def __init__(
        self, store_factory, initialize_database, *, max_sessions=32, seed=None
    ):
        if type(max_sessions) is not int or max_sessions < 1:
            raise ValueError("max_sessions must be a positive integer")
        self._factory = store_factory
        self._initialize = initialize_database
        self.seed = seed
        self._maximum = max_sessions
        self._sessions = {}
        self._preparing = set()
        self._active = None
        self._closed = False
        self._lock = Lock()

    @staticmethod
    def _validate_id(session_id):
        if (
            not isinstance(session_id, str)
            or not session_id.strip()
            or len(session_id) > 128
        ):
            raise ProtocolFailure(
                400, "session_id must contain 1 to 128 nonblank characters", "system"
            )

    def setup(self, session_id, setup):
        self._validate_id(session_id)
        if not isinstance(setup, dict) or setup:
            raise ProtocolFailure(
                400,
                "This course profile currently supports only an empty setup object",
                "system",
            )
        with self._lock:
            if self._closed:
                raise ProtocolFailure(
                    503, "The course system is shutting down", "system"
                )
            if session_id in self._sessions or session_id in self._preparing:
                raise ProtocolFailure(
                    409, "session_id has already been prepared", "system"
                )
            if len(self._sessions) + len(self._preparing) >= self._maximum:
                raise ProtocolFailure(
                    503,
                    "Course session capacity reached; existing sessions are retained",
                    "system",
                )
            if any(session.busy for session in self._sessions.values()):
                raise ProtocolFailure(
                    409,
                    "An incident request is active; prepare the next session afterwards",
                    "system",
                )
            self._preparing.add(session_id)
        resources = ExitStack()
        try:
            engine = resources.enter_context(self._factory())
            self._initialize(engine)
            if self.seed is not None:
                with Session(engine) as database_session:
                    self.seed(database_session)
                    database_session.commit()
            with self._lock:
                if self._closed:
                    raise RuntimeError("Registry closed while setup was being prepared")
                self._sessions[session_id] = _SystemSession(engine, resources)
                self._active = session_id
            return SessionResponse(session_id=session_id)
        except Exception:
            try:
                resources.close()
            except Exception:
                pass
            raise ProtocolFailure(
                500, "Could not prepare an isolated course session", "system"
            ) from None
        finally:
            with self._lock:
                self._preparing.discard(session_id)

    @contextmanager
    def request_lease(self, session_id=None):
        if session_id is not None:
            self._validate_id(session_id)
        with self._lock:
            if self._closed:
                raise ProtocolFailure(
                    503, "The course system is shutting down", "system"
                )
            selected_id = self._active if session_id is None else session_id
            if selected_id is None:
                raise ProtocolFailure(
                    400,
                    "Prepare a course session before incident API requests",
                    "system",
                )
            selected = self._sessions.get(selected_id)
            if selected is None:
                raise ProtocolFailure(404, "The course session was not found", "system")
            if selected.busy:
                raise ProtocolFailure(
                    409,
                    "This course session already has an active incident request",
                    "system",
                )
            selected.busy = True
        try:
            yield selected.engine
        finally:
            with self._lock:
                selected.busy = False

    def report(self, session_id):
        self._validate_id(session_id)
        with self._lock:
            if self._closed:
                raise ProtocolFailure(
                    503, "The course system is shutting down", "system"
                )
            if session_id not in self._sessions:
                raise ProtocolFailure(404, "The course session was not found", "system")
        return SystemReport(
            session_id=session_id,
            coverage=SystemCoverage(
                code=CoverageMeasure(
                    covered=None,
                    total=None,
                    basis="No line-coverage instrumentation is configured for this isolated course session",
                )
            ),
        )

    def close(self):
        with self._lock:
            if self._preparing or any(
                session.busy for session in self._sessions.values()
            ):
                raise RuntimeError(
                    "Cannot close course stores while requests or setup are active"
                )
            self._closed = True
            retained = list(self._sessions.values())
            self._sessions.clear()
            self._active = None
        failed = False
        for session in retained:
            try:
                session.resources.close()
            except Exception:
                failed = True
        if failed:
            raise RuntimeError(
                "Some dedicated course stores could not be cleaned up"
            ) from None
