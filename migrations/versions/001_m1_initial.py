"""Create the M1 application and checkpoint schema.

Revision ID: 001_m1_initial
Revises:
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "001_m1_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "jobs",
    "story_plans",
    "continuity_bibles",
    "idempotency_keys",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
)

JOB_STATUS_VALUES = (
    "QUEUED",
    "RUNNING",
    "BIBLE_LOCKED",
    "PARTIAL",
    "FAILED",
    "FAILED_NO_PROGRESS",
    "ESCALATED",
)


def _enable_rls(table_name: str) -> None:
    policy_name = f"{table_name}_tenant_isolation"
    op.execute(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY')
    op.execute(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY')
    op.execute(
        f'CREATE POLICY "{policy_name}" ON "{table_name}" '
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def upgrade() -> None:
    job_status = postgresql.ENUM(*JOB_STATUS_VALUES, name="job_status")

    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", job_status, nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.String(length=255), nullable=False),
        sa.Column("budget_max_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("budget_max_tokens", sa.BigInteger(), nullable=False),
        sa.Column("budget_max_iterations", sa.Integer(), nullable=False),
        sa.Column("budget_max_wall_clock_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "budget_used_usd",
            sa.Numeric(12, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "budget_used_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "budget_used_iterations",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index("ix_jobs_tenant_id", "jobs", ["tenant_id"])

    op.create_table(
        "story_plans",
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
            unique=True,
        ),
        sa.Column("beats_json", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_story_plans_tenant_id", "story_plans", ["tenant_id"])

    op.create_table(
        "continuity_bibles",
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
            unique=True,
        ),
        sa.Column("bible_json", postgresql.JSONB(), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_continuity_bibles_tenant_id", "continuity_bibles", ["tenant_id"]
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "key", name="uq_idempotency_keys_tenant_key"
        ),
    )
    op.create_index(
        "ix_idempotency_keys_tenant_id", "idempotency_keys", ["tenant_id"]
    )

    # This DDL mirrors langgraph-checkpoint-postgres 3.1.1 migrations 0-10.
    # Alembic owns these tables so application startup must not call saver.setup().
    op.create_table(
        "checkpoint_migrations",
        sa.Column("v", sa.Integer(), primary_key=True, autoincrement=False),
    )
    op.execute("INSERT INTO checkpoint_migrations (v) VALUES (10)")

    checkpoint_tenant_id = sa.Column(
        "tenant_id",
        postgresql.UUID(as_uuid=True),
        nullable=False,
        server_default=sa.text("current_setting('app.tenant_id', true)::uuid"),
    )
    op.create_table(
        "checkpoints",
        checkpoint_tenant_id,
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column(
            "checkpoint_ns", sa.Text(), nullable=False, server_default=sa.text("''")
        ),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.Text(), nullable=True),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("checkpoint", postgresql.JSONB(), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("thread_id", "checkpoint_ns", "checkpoint_id"),
    )
    op.create_index("checkpoints_thread_id_idx", "checkpoints", ["thread_id"])

    op.create_table(
        "checkpoint_blobs",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("current_setting('app.tenant_id', true)::uuid"),
        ),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column(
            "checkpoint_ns", sa.Text(), nullable=False, server_default=sa.text("''")
        ),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("blob", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint(
            "thread_id", "checkpoint_ns", "channel", "version"
        ),
    )
    op.create_index(
        "checkpoint_blobs_thread_id_idx", "checkpoint_blobs", ["thread_id"]
    )

    op.create_table(
        "checkpoint_writes",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("current_setting('app.tenant_id', true)::uuid"),
        ),
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column(
            "checkpoint_ns", sa.Text(), nullable=False, server_default=sa.text("''")
        ),
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("type", sa.Text(), nullable=True),
        sa.Column("blob", sa.LargeBinary(), nullable=False),
        sa.Column(
            "task_path", sa.Text(), nullable=False, server_default=sa.text("''")
        ),
        sa.PrimaryKeyConstraint(
            "thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"
        ),
    )
    op.create_index(
        "checkpoint_writes_thread_id_idx", "checkpoint_writes", ["thread_id"]
    )

    for table_name in TENANT_TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(TENANT_TABLES):
        op.drop_table(table_name)
    op.drop_table("checkpoint_migrations")
    op.drop_table("idempotency_keys")
    op.drop_table("continuity_bibles")
    op.drop_table("story_plans")
    op.drop_table("jobs")
    op.drop_table("tenants")
    postgresql.ENUM(name="job_status").drop(op.get_bind(), checkfirst=True)
