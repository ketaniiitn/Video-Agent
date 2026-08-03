from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TENANT_ISOLATION_POLICY_USING = (
    "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
)


async def set_tenant_context(session: AsyncSession, tenant_id: UUID) -> None:
    """Set the transaction-local tenant used by PostgreSQL RLS policies."""
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
