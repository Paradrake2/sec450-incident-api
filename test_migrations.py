"""Migrations run exclusively against fresh disposable SQLite/PostgreSQL stores."""

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql, sqlite

import migration_tools
from main import Base
from report_import_store import ReportImportBase
from testing_database import disposable_engine

IDENTITY_INDEX = "uq_incidents_team102_source"
IDENTITY_PREDICATE = "substr(source, 1, 15) = 'team102:report:' OR substr(source, 1, 23) = 'team102-fixture:report:'"
HEAD_REVISION = "0003_team102_identity"


def _legacy_metadata():
    """Retain the revision 0001 fixture before the additive identity index."""
    metadata = sa.MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(metadata)
    incidents = metadata.tables["incidents"]
    for index in list(incidents.indexes):
        if index.name == IDENTITY_INDEX:
            incidents.indexes.remove(index)
    return metadata


@pytest.fixture
def database_engine():
    with disposable_engine() as engine:
        yield engine


def _populate_legacy(engine, *, current_metadata=False):
    metadata = Base.metadata if current_metadata else _legacy_metadata()
    metadata.create_all(engine)
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
    assert _revision(database_engine) == HEAD_REVISION
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
    assert _revision(database_engine) == HEAD_REVISION


def test_adopts_startup_schema_with_the_exact_report_identity_index(database_engine):
    _populate_legacy(database_engine, current_metadata=True)
    before = _legacy_rows(database_engine)
    migration_tools.upgrade_database(database_engine)
    assert _legacy_rows(database_engine) == before
    assert _revision(database_engine) == HEAD_REVISION
    indexes = sa.inspect(database_engine).get_indexes("incidents")
    assert sum(index["name"] == IDENTITY_INDEX for index in indexes) == 1


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
    _legacy_metadata().tables["incidents"].create(database_engine)
    with pytest.raises(migration_tools.MigrationAdoptionError, match="partial"):
        migration_tools.upgrade_database(database_engine)
    assert sa.inspect(database_engine).get_table_names() == ["incidents"]


@pytest.mark.parametrize("drift", [
    "column_type", "string_family", "nullable", "server_default", "foreign_key", "check", "index", "extra_index",
    "partial_index", "expression_index", "extra_column", "identity_wrong_predicate",
    "identity_wrong_column", "identity_nonunique", "identity_full_index",
    "identity_literal_case", "identity_literal_space",
])
def test_drift_is_refused_before_stamp_or_import_ddl(database_engine, drift):
    metadata = _legacy_metadata()
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
    elif drift.startswith("identity_"):
        _identity_index(metadata, drift)
    metadata.create_all(database_engine)
    before = set(sa.inspect(database_engine).get_table_names())
    with pytest.raises(migration_tools.MigrationAdoptionError, match="differ"):
        migration_tools.upgrade_database(database_engine)
    assert set(sa.inspect(database_engine).get_table_names()) == before
    assert "alembic_version" not in before


def _identity_index(metadata, definition="exact"):
    table = metadata.tables["incidents"]
    predicate = "source LIKE 'team102:report:%'" if definition == "identity_wrong_predicate" else IDENTITY_PREDICATE
    if definition == "identity_literal_case":
        predicate = predicate.replace("team102", "TEAM102")
    elif definition == "identity_literal_space":
        predicate = predicate.replace("report:'", "report: '")
    options = {} if definition == "identity_full_index" else {
        "sqlite_where": sa.text(predicate), "postgresql_where": sa.text(predicate),
    }
    return sa.Index(
        IDENTITY_INDEX,
        table.c.title if definition == "identity_wrong_column" else table.c.source,
        unique=definition != "identity_nonunique", **options,
    )


def _upgrade_to_report_imports(engine):
    with engine.begin() as connection:
        command.upgrade(migration_tools.migration_config(connection), "0002_report_imports")


def _insert_sources(engine, sources):
    table = _legacy_metadata().tables["incidents"]
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)
    with engine.begin() as connection:
        connection.execute(table.insert(), [
            {
                "id": identifier, "title": "Synthetic migration fixture", "log": "original evidence",
                "occurred_at": now, "created_at": now, "source": source,
                "state": "NEW", "assessment": None, "review": None, "related": "[]",
            }
            for identifier, source in sources
        ])


def test_identity_upgrade_preserves_rows_and_only_restricts_report_sources(database_engine):
    _upgrade_to_report_imports(database_engine)
    _insert_sources(database_engine, [
        ("api", "team102:report:17"), ("fixture", "team102-fixture:report:17"),
        ("ordinary-a", "shared-ordinary-source"), ("ordinary-b", "shared-ordinary-source"),
        ("case-a", "TEAM102:REPORT:17"), ("case-b", "TEAM102:REPORT:17"),
        ("empty-a", None), ("empty-b", None),
    ])
    imports = _import_rows(database_engine)
    before = _legacy_rows(database_engine)
    with database_engine.connect() as connection:
        imports_before = {name: connection.execute(sa.select(table)).all() for name, table in imports.tables.items()}
    migration_tools.upgrade_database(database_engine)
    assert _legacy_rows(database_engine) == before
    with database_engine.connect() as connection:
        assert {name: connection.execute(sa.select(table)).all() for name, table in imports.tables.items()} == imports_before
    for identifier, source in (("api-repeat", "team102:report:17"), ("fixture-repeat", "team102-fixture:report:17")):
        with pytest.raises(sa.exc.IntegrityError):
            _insert_sources(database_engine, [(identifier, source)])
    _insert_sources(database_engine, [("ordinary-c", "shared-ordinary-source"), ("empty-c", None)])


@pytest.mark.parametrize("source", ["team102:report:17", "team102-fixture:report:17"])
@pytest.mark.parametrize("adopt", [False, True])
def test_duplicate_identity_upgrade_refuses_without_deleting_rows_or_advancing_revision(database_engine, source, adopt):
    if adopt:
        _populate_legacy(database_engine)
    else:
        _upgrade_to_report_imports(database_engine)
    _insert_sources(database_engine, [("report-a", source), ("report-b", source)])
    before = _legacy_rows(database_engine)
    before_tables = set(sa.inspect(database_engine).get_table_names())
    with pytest.raises(migration_tools.CommandError, match="duplicate incident sources"):
        migration_tools.upgrade_database(database_engine)
    assert _legacy_rows(database_engine) == before
    assert set(sa.inspect(database_engine).get_table_names()) == before_tables
    assert IDENTITY_INDEX not in {item["name"] for item in sa.inspect(database_engine).get_indexes("incidents")}
    if not adopt:
        assert _revision(database_engine) == "0002_report_imports"


@pytest.mark.parametrize("definition", [
    "exact", "identity_wrong_predicate", "identity_wrong_column", "identity_nonunique", "identity_full_index",
    "identity_literal_case", "identity_literal_space",
])
def test_stamped_identity_upgrade_verifies_an_index_already_created_by_startup(database_engine, definition):
    _upgrade_to_report_imports(database_engine)
    _insert_sources(database_engine, [("api", "team102:report:17")])
    _identity_index(_legacy_metadata(), definition).create(database_engine)
    before = _legacy_rows(database_engine)
    if definition == "exact":
        migration_tools.upgrade_database(database_engine)
        assert _revision(database_engine) == HEAD_REVISION
    else:
        with pytest.raises(migration_tools.CommandError, match="index definition differs"):
            migration_tools.upgrade_database(database_engine)
        assert _revision(database_engine) == "0002_report_imports"
    assert _legacy_rows(database_engine) == before


def test_identity_index_validation_accepts_postgres_reflection_without_changing_literals():
    reflected = {
        "name": IDENTITY_INDEX, "column_names": ["source"], "unique": True,
        "include_columns": [],
        "dialect_options": {
            "postgresql_where": "((substr((source)::text, 1, 15) = 'team102:report:'::text) OR (substr((source)::text, 1, 23) = 'team102-fixture:report:'::text))",
            "postgresql_include": [],
        },
    }
    assert migration_tools._valid_team102_identity_index(reflected, "postgresql")
    migration = ScriptDirectory.from_config(migration_tools.migration_config()).get_revision(HEAD_REVISION).module
    assert migration._matching_index(reflected, "postgresql")
    reflected["dialect_options"]["postgresql_where"] = reflected["dialect_options"]["postgresql_where"].replace("team102", "TEAM102")
    assert not migration_tools._valid_team102_identity_index(reflected, "postgresql")
    assert not migration._matching_index(reflected, "postgresql")


def test_identity_downgrade_removes_only_the_index(database_engine):
    migration_tools.upgrade_database(database_engine)
    _insert_sources(database_engine, [("api", "team102:report:17")])
    imports = _import_rows(database_engine)
    before = _legacy_rows(database_engine)
    before_tables = set(sa.inspect(database_engine).get_table_names())
    with database_engine.connect() as connection:
        imports_before = {name: connection.execute(sa.select(table)).all() for name, table in imports.tables.items()}
    with database_engine.begin() as connection:
        command.downgrade(migration_tools.migration_config(connection), "0002_report_imports")
    assert _revision(database_engine) == "0002_report_imports"
    assert set(sa.inspect(database_engine).get_table_names()) == before_tables
    assert _legacy_rows(database_engine) == before
    with database_engine.connect() as connection:
        assert {name: connection.execute(sa.select(table)).all() for name, table in imports.tables.items()} == imports_before
    assert IDENTITY_INDEX not in {item["name"] for item in sa.inspect(database_engine).get_indexes("incidents")}
    _insert_sources(database_engine, [("api-repeat", "team102:report:17")])


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_identity_migration_compiles_the_same_partial_unique_index_for_both_databases(monkeypatch, dialect):
    migration = ScriptDirectory.from_config(migration_tools.migration_config()).get_revision(HEAD_REVISION).module
    engine = sa.create_mock_engine(f"{dialect.name}://", lambda sql, *args, **kwargs: None)
    monkeypatch.setattr(migration.op, "get_bind", lambda: engine)
    monkeypatch.setattr(migration.sa, "inspect", lambda connection: type("Inspector", (), {
        "get_indexes": lambda self, name: [],
    })())
    monkeypatch.setattr(engine, "execute", lambda query: type("Result", (), {"first": lambda self: None})())
    if dialect.name == "sqlite":
        monkeypatch.setattr(engine, "exec_driver_sql", lambda query: type("Result", (), {"mappings": lambda self: []})(), raising=False)
    captured = []
    monkeypatch.setattr(migration.op, "create_index", lambda name, table, columns, **options: captured.append((name, table, columns, options)))
    migration.upgrade()
    name, table, columns, options = captured[0]
    schema_table = sa.Table(table, sa.MetaData(), sa.Column("source", sa.String(100)))
    index = sa.Index(name, *(schema_table.c[column] for column in columns), **options)
    sql = str(sa.schema.CreateIndex(index).compile(dialect=dialect))
    assert sql.startswith("CREATE UNIQUE INDEX uq_incidents_team102_source ON incidents (source) WHERE ")
    assert sql.endswith(IDENTITY_PREDICATE.replace("%", "%%") if dialect.name == "postgresql" else IDENTITY_PREDICATE)


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
