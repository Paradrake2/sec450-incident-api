"""Enforce one incident per Team 102 report without discarding existing rows.

Revision ID: 0003_team102_identity
Revises: 0002_report_imports
"""

import re

import sqlalchemy as sa
from alembic import op
from alembic.util.exc import CommandError

revision = "0003_team102_identity"
down_revision = "0002_report_imports"
branch_labels = None
depends_on = None

# Frozen migration contract; do not derive this definition from live models.
_INDEX_NAME = "uq_incidents_team102_source"
_PREDICATE = "substr(source, 1, 15) = 'team102:report:' OR substr(source, 1, 23) = 'team102-fixture:report:'"


def _normalized_predicate(value):
    pieces = re.split(r"('(?:''|[^'])*')", str(value))
    normalized = []
    for position, piece in enumerate(pieces):
        if position % 2:
            normalized.append(piece)
        else:
            piece = re.sub(r"::(?:text|character varying)", "", piece.lower())
            normalized.append(re.sub(r'[\s()"`\[\]]', "", piece).replace("~~", "like"))
    return "".join(normalized)


def _matching_index(index, dialect_name):
    options = dict(index.get("dialect_options", {}))
    predicate = options.pop(f"{dialect_name}_where", None)
    return (
        index.get("unique")
        and tuple(index.get("column_names", ())) == ("source",)
        and predicate is not None
        and _normalized_predicate(predicate) == _normalized_predicate(_PREDICATE)
        and not any(value is not None and bool(value) for value in options.values())
        and not index.get("include_columns")
        and not index.get("expressions")
    )


def upgrade():
    connection = op.get_bind()
    incidents = sa.table("incidents", sa.column("source", sa.String(100)))
    duplicates = connection.execute(
        sa.select(incidents.c.source)
        .where(sa.text(_PREDICATE))
        .group_by(incidents.c.source)
        .having(sa.func.count() > 1)
        .limit(1)
    ).first()
    if duplicates is not None:
        raise CommandError(
            "Cannot enable Team 102 report identity: duplicate incident sources exist; "
            "reconcile the existing incidents before retrying the migration"
        )

    existing = [
        index for index in sa.inspect(connection).get_indexes("incidents")
        if index["name"] == _INDEX_NAME
    ]
    if connection.dialect.name == "sqlite":
        # Reflection skips expression indexes; a matching name must still be checked.
        names = {
            row["name"] for row in connection.exec_driver_sql("PRAGMA index_list(incidents)").mappings()
        }
        if _INDEX_NAME in names and not existing:
            raise CommandError("Cannot enable Team 102 report identity: existing index definition differs")
    if existing:
        if len(existing) != 1 or not _matching_index(existing[0], connection.dialect.name):
            raise CommandError("Cannot enable Team 102 report identity: existing index definition differs")
        return

    op.create_index(
        _INDEX_NAME, "incidents", ["source"], unique=True,
        sqlite_where=sa.text(_PREDICATE), postgresql_where=sa.text(_PREDICATE),
    )


def downgrade():
    op.drop_index(_INDEX_NAME, table_name="incidents")
