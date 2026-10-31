"""Add private immutable source snapshots and retrieval observations.

Revision ID: 0002_report_imports
Revises: 0001_baseline
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_report_imports"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "source_report_snapshots",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("source_system", sa.String(100), nullable=False),
        sa.Column("source_report_id", sa.String(64), nullable=False),
        sa.Column("canonical_sha256", sa.String(64), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.Column("original_content", sa.LargeBinary(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_system", "source_report_id", "canonical_sha256",
            name="uq_source_report_snapshot_semantic",
        ),
    )
    op.create_table(
        "source_report_observations",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("retrieval_method", sa.String(16), nullable=False),
        sa.Column("raw_sha256", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["source_report_snapshots.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade():
    op.drop_table("source_report_observations")
    op.drop_table("source_report_snapshots")
