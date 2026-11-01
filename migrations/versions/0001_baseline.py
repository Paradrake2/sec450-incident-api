"""Freeze the four-table incident API schema before report imports.

Revision ID: 0001_baseline
Revises: None

Existing databases are adopted only after migration_tools validates this schema.
Downgrading this baseline explicitly deletes all four incident API tables.
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "incidents",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("log", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(100), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("assessment", sa.Text(), nullable=True),
        sa.Column("review", sa.Text(), nullable=True),
        sa.Column("related", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "incident_reviews",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("reviewer", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_incident_reviews_incident_id", "incident_reviews", ["incident_id"])
    op.create_table(
        "incident_relations",
        sa.Column("incident_a_id", sa.String(36), nullable=False),
        sa.Column("incident_b_id", sa.String(36), nullable=False),
        sa.CheckConstraint("incident_a_id < incident_b_id", name="ck_relation_canonical_order"),
        sa.ForeignKeyConstraint(["incident_a_id"], ["incidents.id"]),
        sa.ForeignKeyConstraint(["incident_b_id"], ["incidents.id"]),
        sa.PrimaryKeyConstraint("incident_a_id", "incident_b_id"),
    )
    op.create_table(
        "disclosure_drafts",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("incident_id", sa.String(36), nullable=False),
        sa.Column("draft", sa.Text(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("final", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_disclosure_drafts_incident_id", "disclosure_drafts", ["incident_id"])


def downgrade():
    op.drop_index("ix_disclosure_drafts_incident_id", table_name="disclosure_drafts")
    op.drop_table("disclosure_drafts")
    op.drop_table("incident_relations")
    op.drop_index("ix_incident_reviews_incident_id", table_name="incident_reviews")
    op.drop_table("incident_reviews")
    op.drop_table("incidents")
