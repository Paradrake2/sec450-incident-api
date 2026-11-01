"""Explicit migrations and verified adoption of pre-Alembic incident stores.

Importing this module never reads .env, imports the application, or selects a
database. Callers must pass an Engine. The CLI loads configuration only when run.
"""

import argparse
import re
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import Boolean, DateTime, String, Text, create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

BASELINE_REVISION = "0001_baseline"
REPORT_IMPORT_TABLES = frozenset({"source_report_snapshots", "source_report_observations"})

# Frozen adoption contract for revision 0001; never derive it from live models.
# Each column is (SQLAlchemy type, nullable). No baseline column has a server default.
_BASELINE_COLUMNS = {
    "incidents": {
        "id": (String(36), False), "title": (String(200), False),
        "log": (Text(), False), "occurred_at": (DateTime(timezone=True), False),
        "created_at": (DateTime(timezone=True), False), "source": (String(100), True),
        "state": (String(32), False), "assessment": (Text(), True),
        "review": (Text(), True), "related": (Text(), False),
    },
    "incident_reviews": {
        "id": (String(36), False), "incident_id": (String(36), False),
        "decision": (String(20), False), "reviewer": (Text(), False),
        "rationale": (Text(), True), "reviewed_at": (DateTime(timezone=True), False),
    },
    "incident_relations": {
        "incident_a_id": (String(36), False), "incident_b_id": (String(36), False),
    },
    "disclosure_drafts": {
        "id": (String(36), False), "incident_id": (String(36), False),
        "draft": (Text(), False), "generated_at": (DateTime(timezone=True), False),
        "final": (Boolean(), False),
    },
}
LEGACY_TABLES = frozenset(_BASELINE_COLUMNS)


class MigrationAdoptionError(ValueError):
    """The existing schema cannot safely be adopted as the frozen baseline."""


def migration_config(connection=None):
    """Build Alembic configuration without selecting any database."""
    root = Path(__file__).resolve().parent
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def _same_type(actual, expected, dialect):
    # Type subclasses can change semantics (CHAR padding, CITEXT comparison).
    # Compare their actual DDL in this dialect rather than Python inheritance.
    # SQLite naturally compiles both DateTime timezone variants as DATETIME.
    return actual.compile(dialect=dialect) == expected.compile(dialect=dialect)


def _check_sql(sql):
    # PostgreSQL reflects the implicit VARCHAR-to-text casts in this check.
    normalized = re.sub(r"::(?:text|character varying)", "", sql.lower())
    return re.sub(r'[\s()"`\[\]]', "", normalized)


def _validate_baseline(connection):
    inspector = inspect(connection)

    def refuse(table, component):
        raise MigrationAdoptionError(
            f"Cannot adopt existing schema: {table} {component} differ from revision {BASELINE_REVISION}"
        )

    for table, expected_columns in _BASELINE_COLUMNS.items():
        actual_columns = {column["name"]: column for column in inspector.get_columns(table)}
        if set(actual_columns) != set(expected_columns):
            refuse(table, "columns")
        for name, (expected_type, nullable) in expected_columns.items():
            column = actual_columns[name]
            if (
                not _same_type(column["type"], expected_type, connection.dialect)
                or column["nullable"] != nullable
                or column.get("default") is not None
                or column.get("computed") is not None
                or column.get("identity") is not None
            ):
                refuse(table, f"column {name}")
        pk = ("incident_a_id", "incident_b_id") if table == "incident_relations" else ("id",)
        if tuple(inspector.get_pk_constraint(table)["constrained_columns"]) != pk:
            refuse(table, "primary key")
        expected_fk_columns = {
            "incident_reviews": {("incident_id",)},
            "incident_relations": {("incident_a_id",), ("incident_b_id",)},
            "disclosure_drafts": {("incident_id",)},
        }.get(table, set())
        actual_fk_columns = []
        for fk in inspector.get_foreign_keys(table):
            actual_fk_columns.append(tuple(fk["constrained_columns"]))
            if (
                fk["referred_table"] != "incidents"
                or tuple(fk["referred_columns"]) != ("id",)
                or fk.get("referred_schema") not in {None, inspector.default_schema_name}
                or any(value is not None for value in fk.get("options", {}).values())
            ):
                refuse(table, "foreign keys")
        if len(actual_fk_columns) != len(expected_fk_columns) or set(actual_fk_columns) != expected_fk_columns:
            refuse(table, "foreign keys")
        expected_indexes = set()
        if table in {"incident_reviews", "disclosure_drafts"}:
            expected_indexes.add((f"ix_{table}_incident_id", ("incident_id",), False))
        if connection.dialect.name == "sqlite":
            # SQLAlchemy skips SQLite expression indexes during reflection.
            # Count all explicit indexes first so those cannot escape adoption checks.
            quoted_table = connection.dialect.identifier_preparer.quote(table)
            sqlite_indexes = connection.exec_driver_sql(f"PRAGMA index_list({quoted_table})").mappings()
            explicit_indexes = {
                (item["name"], bool(item["unique"]), bool(item["partial"]))
                for item in sqlite_indexes if item["origin"] != "pk"
            }
            if explicit_indexes != {(name, unique, False) for name, _, unique in expected_indexes}:
                refuse(table, "indexes")
        if inspector.get_unique_constraints(table):
            refuse(table, "unique constraints")
        actual_indexes = set()
        for index in inspector.get_indexes(table):
            actual_indexes.add((index["name"], tuple(index["column_names"]), bool(index["unique"])))
            if (
                any(
                    value is not None and (
                        not isinstance(value, (list, tuple, dict, str, bool)) or bool(value)
                    )
                    for value in index.get("dialect_options", {}).values()
                )
                or index.get("include_columns")
                or index.get("expressions")
            ):
                refuse(table, "indexes")
        if actual_indexes != expected_indexes:
            refuse(table, "indexes")
        checks = inspector.get_check_constraints(table)
        expected_checks = {
            ("ck_relation_canonical_order", "incident_a_id<incident_b_id")
        } if table == "incident_relations" else set()
        if len(checks) != len(expected_checks) or {
            (item.get("name"), _check_sql(item["sqltext"])) for item in checks
        } != expected_checks:
            refuse(table, "check constraints")


def upgrade_database(engine: Engine) -> None:
    """Upgrade only this engine; adopt complete, matching legacy stores safely.

    Validation happens before stamping or DDL. A single transaction includes
    adoption and upgrades, including with SQLite's legacy transaction behavior.
    """
    with engine.begin() as connection:
        if connection.dialect.name == "sqlite":
            driver_connection = connection.connection.driver_connection
            if not driver_connection.in_transaction:
                connection.exec_driver_sql("BEGIN")
        config = migration_config(connection)
        tables = set(inspect(connection).get_table_names())
        if "alembic_version" not in tables:
            if tables & REPORT_IMPORT_TABLES:
                raise MigrationAdoptionError(
                    "Cannot adopt source report tables without Alembic migration history"
                )
            existing_legacy = tables & LEGACY_TABLES
            if existing_legacy and existing_legacy != LEGACY_TABLES:
                raise MigrationAdoptionError("Cannot adopt a partial incident API schema")
            if existing_legacy:
                _validate_baseline(connection)
                command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Migrate the explicitly configured incident database")
    parser.add_argument("command", choices=["upgrade"])
    parser.parse_args(argv)

    # Explicit CLI execution is the only configuration-loading path.
    import os

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().with_name(".env"))
    raw_url = os.environ.get("DATABASE_URL")
    if not raw_url:
        print("DATABASE_URL is required for migrations", file=sys.stderr)
        return 1
    if raw_url.startswith("postgres://"):
        raw_url = "postgresql://" + raw_url[len("postgres://"):]
    database_engine = None
    try:
        database_engine = create_engine(raw_url, pool_pre_ping=True)
        upgrade_database(database_engine)
    except MigrationAdoptionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (SQLAlchemyError, CommandError, ValueError):
        # Driver/URL exceptions can contain credentials; never print them.
        print("Database migration failed; check the configured database and schema", file=sys.stderr)
        return 1
    finally:
        if database_engine is not None:
            database_engine.dispose()
    print("Database migration complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
