"""Add tenant-aware job foreign keys.

Revision ID: 002_expand_tenant_aware_job_fks
Revises: 001_m1_initial
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op


revision: str = "002_expand_tenant_aware_job_fks"
down_revision: str | None = "001_m1_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_JOB_REFERENCES = (
    ("story_plans", "fk_story_plans_job_tenant"),
    ("continuity_bibles", "fk_continuity_bibles_job_tenant"),
    ("idempotency_keys", "fk_idempotency_keys_job_tenant"),
)


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_jobs_id_tenant_id",
        "jobs",
        ["id", "tenant_id"],
    )
    for table_name, constraint_name in TENANT_JOB_REFERENCES:
        op.create_foreign_key(
            constraint_name,
            table_name,
            "jobs",
            ["job_id", "tenant_id"],
            ["id", "tenant_id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    for table_name, constraint_name in reversed(TENANT_JOB_REFERENCES):
        op.drop_constraint(constraint_name, table_name, type_="foreignkey")
    op.drop_constraint("uq_jobs_id_tenant_id", "jobs", type_="unique")
