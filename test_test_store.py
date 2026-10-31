"""Test-store guards prevent the regression suite from reaching company data."""

from pathlib import Path

import pytest
from sqlalchemy import text

from testing_database import disposable_engine, postgres_test_url


@pytest.mark.parametrize("url", [
    "postgresql+psycopg://demo:local@127.0.0.1:5433/sec450_test",
    "postgres://demo:local@localhost/test_sec450",
    "postgresql+psycopg2://demo:local@[::1]/sec450_test",
])
def test_explicit_local_postgres_test_databases_are_accepted(url):
    parsed = postgres_test_url(url)
    assert parsed.get_backend_name() == "postgresql"


@pytest.mark.parametrize("url", [
    "not-a-database-url",
    "sqlite:///incidents.db",
    "postgresql://demo@company.example/sec450_test",
    "postgresql:///sec450_test",
    "postgresql://demo@127.0.0.1/company",
    "postgresql://demo@127.0.0.1/sec450_test?host=company.example",
    "postgresql://demo@127.0.0.1/sec450_test?hostaddr=203.0.113.1",
    "postgresql://demo@127.0.0.1/sec450_test?service=company",
    "postgresql://demo@127.0.0.1/sec450_test?options=-csearch_path=public",
])
def test_unsafe_postgres_test_targets_are_rejected_before_connection(url):
    with pytest.raises(ValueError):
        postgres_test_url(url)


def test_default_store_ignores_normal_database_url_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://demo@company.example/company")
    with disposable_engine(tmp_path) as engine:
        assert engine.dialect.name == "sqlite"
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT 1")) == 1
        path = Path(engine.url.database)
        assert path.is_file()
    assert not path.exists()


def test_bad_explicit_opt_in_does_not_fall_back_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URL", "")
    with pytest.raises(ValueError):
        with disposable_engine(tmp_path):
            pytest.fail("An invalid PostgreSQL opt-in must not prepare a store")
    assert list(tmp_path.iterdir()) == []
