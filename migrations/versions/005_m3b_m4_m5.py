"""Add M3b/M4 delivery, QC scores, and DELIVERED status.

Revision ID: 005_m3b_m4_m5
Revises: 004_m3a_shots
Create Date: 2026-08-20
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "005_m3b_m4_m5"
down_revision: str | None = "004_m3a_shots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_ISOLATION_POLICY_USING = (
    "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
)


def _enable_rls(table_name: str) -> None:
    policy_name = f"{table_name}_tenant_isolation"
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY "{policy_name}" ON "{table_name}" '
        f"USING ({TENANT_ISOLATION_POLICY_USING})"
    )


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'DELIVERED'")

    op.add_column(
        "jobs",
        sa.Column(
            "degraded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("jobs", sa.Column("assembled_path", sa.String(length=1024)))
    op.add_column("jobs", sa.Column("download_url", sa.String(length=2048)))
    op.add_column("jobs", sa.Column("thumbnail_url", sa.String(length=2048)))

    op.add_column("shots", sa.Column("qc_score", sa.Numeric(6, 4)))
    op.add_column(
        "shots",
        sa.Column(
            "degraded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "shots",
        sa.Column(
            "repair_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )

    op.create_table(
        "qc_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "shot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("shots.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("beat_index", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(6, 4), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "tenant_id"],
            ["jobs.id", "jobs.tenant_id"],
            name="fk_qc_scores_job_tenant",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_qc_scores_tenant_id", "qc_scores", ["tenant_id"])
    _enable_rls("qc_scores")


def downgrade() -> None:
    op.drop_table("qc_scores")
    op.drop_column("shots", "repair_count")
    op.drop_column("shots", "degraded")
    op.drop_column("shots", "qc_score")
    op.drop_column("jobs", "thumbnail_url")
    op.drop_column("jobs", "download_url")
    op.drop_column("jobs", "assembled_path")
    op.drop_column("jobs", "degraded")
