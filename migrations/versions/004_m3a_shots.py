"""Add M3a shot generation persistence.

Revision ID: 004_m3a_shots
Revises: 003_rls_nullif_tenant_setting
Create Date: 2026-08-07
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "004_m3a_shots"
down_revision: str | None = "003_rls_nullif_tenant_setting"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SHOT_STATUS_VALUES = ("PENDING", "RUNNING", "SUCCEEDED", "FAILED")
TENANT_TABLES = ("shots", "cost_ledger")
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
    # PostgreSQL requires a commit before a newly added enum value can be used.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE job_status ADD VALUE IF NOT EXISTS 'SHOTS_READY'")

    shot_status = postgresql.ENUM(*SHOT_STATUS_VALUES, name="shot_status")
    op.create_table(
        "shots",
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
        sa.Column("beat_index", sa.Integer(), nullable=False),
        sa.Column("status", shot_status, nullable=False),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("clip_path", sa.String(length=1024), nullable=True),
        sa.Column("frame_path", sa.String(length=1024), nullable=True),
        sa.Column(
            "cost_usd",
            sa.Numeric(12, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column("provider_id", sa.String(length=255), nullable=False),
        sa.Column("seed", sa.BigInteger(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "beat_index BETWEEN 1 AND 4",
            name="ck_shots_beat_index",
        ),
        sa.UniqueConstraint(
            "job_id",
            "beat_index",
            name="uq_shots_job_id_beat_index",
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "tenant_id"],
            ["jobs.id", "jobs.tenant_id"],
            name="fk_shots_job_tenant",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_shots_tenant_id", "shots", ["tenant_id"])

    op.create_table(
        "cost_ledger",
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
        sa.Column("usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("tokens", sa.BigInteger(), nullable=True),
        sa.Column("provider_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "tenant_id"],
            ["jobs.id", "jobs.tenant_id"],
            name="fk_cost_ledger_job_tenant",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_cost_ledger_tenant_id", "cost_ledger", ["tenant_id"])

    for table_name in TENANT_TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    op.drop_table("cost_ledger")
    op.drop_table("shots")
    postgresql.ENUM(name="shot_status").drop(op.get_bind(), checkfirst=True)
    # SHOTS_READY remains in job_status because PostgreSQL cannot safely remove
    # enum values in a backwards-compatible contract migration.
