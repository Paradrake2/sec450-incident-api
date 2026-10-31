"""Disposable stores for local regressions; never use the application's URL."""

import ipaddress
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.schema import CreateSchema, DropSchema


def postgres_test_url(raw_url):
    """Require an explicit local test database before any connection is opened."""
    try:
        url = make_url(raw_url)
    except (ArgumentError, TypeError, ValueError) as exc:
        raise ValueError("TEST_DATABASE_URL must be a valid PostgreSQL URL") from exc
    if url.drivername not in {
        "postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2",
    }:
        raise ValueError("TEST_DATABASE_URL must select PostgreSQL")
    if url.query:
        raise ValueError("TEST_DATABASE_URL must not contain query parameters")
    if url.host != "localhost":
        try:
            loopback = ipaddress.ip_address(url.host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("TEST_DATABASE_URL must use a loopback host")
    if not url.database or not re.fullmatch(r"[A-Za-z0-9_]+", url.database):
        raise ValueError("TEST_DATABASE_URL needs a dedicated test database")
    if not (url.database.startswith("test_") or url.database.endswith("_test")):
        raise ValueError("TEST_DATABASE_URL database must start with test_ or end with _test")
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    return url


@contextmanager
def disposable_engine(root_dir=None):
    """Yield an empty store and remove only that store on exit.

    TEST_DATABASE_URL opts into PostgreSQL. Each store gets its own random
    schema with no public-schema fallback. With no opt-in, use temporary SQLite.
    DATABASE_URL is deliberately never consulted.
    """
    raw_url = os.environ.get("TEST_DATABASE_URL")
    if raw_url is None:
        with tempfile.TemporaryDirectory(prefix="sec450-test-", dir=root_dir) as directory:
            path = (Path(directory) / "incidents.db").as_posix()
            engine = create_engine(
                "sqlite:///" + path,
                connect_args={"check_same_thread": False},
            )
            try:
                yield engine
            finally:
                engine.dispose()
        return

    url = postgres_test_url(raw_url)
    # Explicit connection routing also prevents libpq's PGHOSTADDR/PGPORT
    # environment defaults from redirecting an otherwise approved local URL.
    connect_args = {
        "host": url.host,
        "hostaddr": "127.0.0.1" if url.host == "localhost" else url.host,
        "port": url.port or 5432,
        "dbname": url.database,
        "connect_timeout": 5,
    }
    admin = create_engine(url, isolation_level="AUTOCOMMIT", connect_args=connect_args)
    schema = "sec450_test_" + uuid4().hex
    engine = None
    created = False
    try:
        with admin.connect() as connection:
            connection.execute(CreateSchema(schema))
        created = True
        # The URL is also used when main constructs its own engine, so carry
        # these routing constraints in the URL rather than only connect_args.
        scoped_url = url.set(port=url.port or 5432).update_query_dict({
            "options": "-csearch_path=" + schema,
            "hostaddr": connect_args["hostaddr"],
            "connect_timeout": "5",
        })
        engine = create_engine(scoped_url, pool_pre_ping=True, connect_args=connect_args)
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
