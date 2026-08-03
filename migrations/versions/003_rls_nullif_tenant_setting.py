"""Harden tenant RLS policies against empty app.tenant_id setting.

Revision ID: 003_rls_nullif_tenant_setting
Revises: 002_expand_tenant_aware_job_fks
Create Date: 2026-08-03
"""

from collections.abc import Sequence

from alembic import op


revision: str = "003_rls_nullif_tenant_setting"
down_revision: str | None = "002_expand_tenant_aware_job_fks"
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

TENANT_ISOLATION_USING = (
    "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
)
LEGACY_TENANT_ISOLATION_USING = (
    "tenant_id = current_setting('app.tenant_id', true)::uuid"
)


def _replace_tenant_isolation_policy(table_name: str, using_expr: str) -> None:
    policy_name = f"{table_name}_tenant_isolation"
    op.execute(f'DROP POLICY IF EXISTS "{policy_name}" ON "{table_name}"')
    op.execute(
        f'CREATE POLICY "{policy_name}" ON "{table_name}" '
        f"USING ({using_expr})"
    )


def upgrade() -> None:
    for table_name in TENANT_TABLES:
        _replace_tenant_isolation_policy(table_name, TENANT_ISOLATION_USING)


def downgrade() -> None:
    for table_name in TENANT_TABLES:
        _replace_tenant_isolation_policy(table_name, LEGACY_TENANT_ISOLATION_USING)
