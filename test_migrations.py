"""Migrations run exclusively against fresh disposable SQLite/PostgreSQL stores."""

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.dialects import postgresql, sqlite

import migration_tools
from main import Base
from report_import_store import ReportImportBase
from testing_database import disposable_engine


@pytest.fixture
def database_engine():
    with disposable_engine() as engine:
        yield engine


def _populate_legacy(engine):
    Base.metadata.create_all(engine)
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(Base.metadata.tables["incidents"].insert(), [
            {
                "id": identifier, "title": f"Synthetic {identifier}", "log": "preserved evidence",
                "occurred_at": now, "created_at": now, "source": "fixture",
                "state": "MATERIAL_CONFIRMED", "assessment": '{"score": 3}',
                "review": '{"decision": "MATERIAL", "reviewer": "human"}',
                "related": '["b"]' if identifier == "a" else '["a"]',
            }
            for identifier in ("a", "b")
        ])
        connection.execute(Base.metadata.tables["incident_reviews"].insert(), [
            {"id": "review-1", "incident_id": "a", "decision": "NOT_MATERIAL",
             "reviewer": "first", "rationale": "initial", "reviewed_at": now},
            {"id": "review-2", "incident_id": "a", "decision": "MATERIAL",
             "reviewer": "second", "rationale": "escalated", "reviewed_at": now},
        ])
        connection.execute(Base.metadata.tables["incident_relations"].insert(),
                           {"incident_a_id": "a", "incident_b_id": "b"})
        connection.execute(Base.metadata.tables["disclosure_drafts"].insert(), [
            {"id": "draft-1", "incident_id": "a", "draft": "version one", "generated_at": now, "final": False},
            {"id": "draft-2", "incident_id": "a", "draft": "version two", "generated_at": now, "final": False},
        ])


def _legacy_rows(engine):
    with engine.connect() as connection:
        return {
            name: connection.execute(sa.select(table).order_by(*table.primary_key.columns)).all()
            for name, table in Base.metadata.tables.items()
        }


def _revision(engine):
    with engine.connect() as connection:
        return connection.scalar(sa.text("SELECT version_num FROM alembic_version"))


def _import_rows(engine):
    metadata = sa.MetaData()
    metadata.reflect(engine, only=list(migration_tools.REPORT_IMPORT_TABLES))
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(metadata.tables["source_report_snapshots"].insert(), {
            "id": "snapshot-1", "source_system": "fixture:team102", "source_report_id": "report-1",
            "canonical_sha256": "a" * 64, "raw_sha256": "b" * 64,
            "original_content": b'{"original": true}', "imported_at": now,
        })
        connection.execute(metadata.tables["source_report_observations"].insert(), {
            "id": "observation-1", "snapshot_id": "snapshot-1", "fetched_at": now,
            "source_url": "fixture://report-1", "request_id": None,
            "retrieval_method": "fixture", "raw_sha256": "b" * 64,
        })
    return metadata


def test_fresh_upgrade_uses_only_the_supplied_engine(database_engine, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "invalid URL that must never be selected")
    migration_tools.upgrade_database(database_engine)
    assert _revision(database_engine) == "0002_report_imports"
    inspector = sa.inspect(database_engine)
    assert set(inspector.get_table_names()) == (
        migration_tools.LEGACY_TABLES | migration_tools.REPORT_IMPORT_TABLES | {"alembic_version"}
    )
    # A freshly migrated baseline must satisfy the same adoption contract.
    with database_engine.connect() as connection:
        migration_tools._validate_baseline(connection)
        assert compare_metadata(
            MigrationContext.configure(connection), [Base.metadata, ReportImportBase.metadata],
        ) == []
    assert {
        (item["name"], tuple(item["column_names"]))
        for item in inspector.get_unique_constraints("source_report_snapshots")
    } == {("uq_source_report_snapshot_semantic", ("source_system", "source_report_id", "canonical_sha256"))}
    assert inspector.get_foreign_keys("source_report_observations")[0]["referred_table"] == "source_report_snapshots"


def test_adopts_current_populated_schema_without_changing_any_legacy_rows(database_engine):
    _populate_legacy(database_engine)
    before = _legacy_rows(database_engine)
    migration_tools.upgrade_database(database_engine)
    assert _legacy_rows(database_engine) == before
    assert _revision(database_engine) == "0002_report_imports"


def test_repeated_upgrade_preserves_legacy_and_import_history(database_engine):
    _populate_legacy(database_engine)
    before = _legacy_rows(database_engine)
    migration_tools.upgrade_database(database_engine)
    import_metadata = _import_rows(database_engine)
    with database_engine.connect() as connection:
        imports_before = {
            name: connection.execute(sa.select(table)).all()
            for name, table in import_metadata.tables.items()
        }
    migration_tools.upgrade_database(database_engine)
    assert _legacy_rows(database_engine) == before
    with database_engine.connect() as connection:
        assert {
            name: connection.execute(sa.select(table)).all()
            for name, table in import_metadata.tables.items()
        } == imports_before


def test_partial_legacy_schema_is_refused_without_stamp_or_ddl(database_engine):
    Base.metadata.tables["incidents"].create(database_engine)
    with pytest.raises(migration_tools.MigrationAdoptionError, match="partial"):
        migration_tools.upgrade_database(database_engine)
    assert sa.inspect(database_engine).get_table_names() == ["incidents"]


@pytest.mark.parametrize("drift", [
    "column_type", "string_family", "nullable", "server_default", "foreign_key", "check", "index", "extra_index",
    "partial_index", "expression_index", "extra_column",
])
def test_drift_is_refused_before_stamp_or_import_ddl(database_engine, drift):
    metadata = sa.MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    if drift == "column_type":
        metadata.tables["incidents"].c.title.type = sa.String(199)
    elif drift == "string_family":
        metadata.tables["incident_reviews"].c.id.type = sa.CHAR(36)
    elif drift == "nullable":
        metadata.tables["incidents"].c.title.nullable = True
    elif drift == "server_default":
        metadata.tables["incidents"].c.title.server_default = sa.DefaultClause("'changed'")
    elif drift == "foreign_key":
        table = metadata.tables["incident_reviews"]
        for constraint in list(table.foreign_key_constraints):
            table.constraints.remove(constraint)
    elif drift == "check":
        table = metadata.tables["incident_relations"]
        for constraint in list(table.constraints):
            if isinstance(constraint, sa.CheckConstraint):
                table.constraints.remove(constraint)
    elif drift == "index":
        metadata.tables["incident_reviews"].indexes.clear()
    elif drift == "extra_index":
        sa.Index("unexpected_title_index", metadata.tables["incidents"].c.title)
    elif drift == "partial_index":
        table = metadata.tables["incident_reviews"]
        table.indexes.clear()
        sa.Index(
            "ix_incident_reviews_incident_id", table.c.incident_id,
            sqlite_where=sa.text("incident_id <> 'ignored'"),
            postgresql_where=sa.text("incident_id <> 'ignored'"),
        )
    elif drift == "expression_index":
        sa.Index("unexpected_title_expression", sa.func.lower(metadata.tables["incidents"].c.title))
    elif drift == "extra_column":
        metadata.tables["incidents"].append_column(sa.Column("unexpected", sa.Text()))
    metadata.create_all(database_engine)
    before = set(sa.inspect(database_engine).get_table_names())
    with pytest.raises(migration_tools.MigrationAdoptionError, match="differ"):
        migration_tools.upgrade_database(database_engine)
    assert set(sa.inspect(database_engine).get_table_names()) == before
    assert "alembic_version" not in before


def test_postgres_type_guard_rejects_citext_for_baseline_text():
    assert not migration_tools._same_type(postgresql.CITEXT(), sa.Text(), postgresql.dialect())


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_type_guard_distinguishes_fixed_from_variable_length_strings(dialect):
    assert not migration_tools._same_type(sa.CHAR(36), sa.String(36), dialect)


def test_type_guard_retains_dialect_specific_timezone_semantics():
    assert migration_tools._same_type(sa.DateTime(), sa.DateTime(timezone=True), sqlite.dialect())
    assert not migration_tools._same_type(sa.DateTime(), sa.DateTime(timezone=True), postgresql.dialect())


@pytest.mark.parametrize("with_legacy", [False, True])
def test_report_tables_without_history_are_refused(database_engine, with_legacy):
    if with_legacy:
        _populate_legacy(database_engine)
    sa.Table("source_report_snapshots", sa.MetaData(), sa.Column("id", sa.String(36), primary_key=True)).create(database_engine)
    before = set(sa.inspect(database_engine).get_table_names())
    with pytest.raises(migration_tools.MigrationAdoptionError, match="without Alembic"):
        migration_tools.upgrade_database(database_engine)
    assert set(sa.inspect(database_engine).get_table_names()) == before


def test_import_downgrade_preserves_all_legacy_rows(database_engine):
    _populate_legacy(database_engine)
    before = _legacy_rows(database_engine)
    migration_tools.upgrade_database(database_engine)
    _import_rows(database_engine)
    with database_engine.begin() as connection:
        command.downgrade(migration_tools.migration_config(connection), "0001_baseline")
    assert set(sa.inspect(database_engine).get_table_names()) == migration_tools.LEGACY_TABLES | {"alembic_version"}
    assert _legacy_rows(database_engine) == before
    assert _revision(database_engine) == "0001_baseline"


@pytest.mark.parametrize("adopt", [False, True])
def test_upgrade_failure_rolls_back_all_new_ddl_and_stamp(database_engine, monkeypatch, adopt):
    if adopt:
        _populate_legacy(database_engine)
    before_tables = set(sa.inspect(database_engine).get_table_names())
    before_rows = _legacy_rows(database_engine) if adopt else None
    original_upgrade = command.upgrade

    def fail_after_upgrade(config, revision):
        original_upgrade(config, revision)
        raise RuntimeError("synthetic failure after migration DDL")

    monkeypatch.setattr(command, "upgrade", fail_after_upgrade)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        migration_tools.upgrade_database(database_engine)
    assert set(sa.inspect(database_engine).get_table_names()) == before_tables
    if adopt:
        assert _legacy_rows(database_engine) == before_rows


def test_cli_requires_database_url_without_sqlite_fallback(monkeypatch, capsys):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert migration_tools.main(["upgrade"]) == 1
    assert "DATABASE_URL is required" in capsys.readouterr().err


def test_cli_does_not_print_credentials_on_configuration_failure(monkeypatch, capsys):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("DATABASE_URL", "invalid://user:synthetic-secret@example.invalid/database")
    assert migration_tools.main(["upgrade"]) == 1
    captured = capsys.readouterr()
    assert "synthetic-secret" not in captured.out + captured.err
    assert "migration failed" in captured.err


def test_cli_normalizes_legacy_postgres_scheme_without_logging_url(database_engine, monkeypatch, capsys):
    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("DATABASE_URL", "postgres://user:synthetic-secret@example.invalid/database")
    selected_urls = []

    def selected_engine(url, **kwargs):
        selected_urls.append(url)
        return database_engine

    monkeypatch.setattr(migration_tools, "create_engine", selected_engine)
    assert migration_tools.main(["upgrade"]) == 0
    assert selected_urls == ["postgresql://user:synthetic-secret@example.invalid/database"]
    captured = capsys.readouterr()
    assert "synthetic-secret" not in captured.out + captured.err
    assert "Database migration complete" in captured.out
