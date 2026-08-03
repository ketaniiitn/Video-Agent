from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.db.rls import set_tenant_context


def create_engine_from_settings(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def get_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


_default_engine: AsyncEngine | None = None
_default_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _get_default_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _default_engine, _default_sessionmaker
    if _default_sessionmaker is None:
        _default_engine = create_engine_from_settings(Settings())
        _default_sessionmaker = get_sessionmaker(_default_engine)
    return _default_sessionmaker


@asynccontextmanager
async def get_session(
    tenant_id: UUID,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Yield a transaction whose tenant context is enforced by PostgreSQL RLS.

    ``sessionmaker`` is optional so this keeps matching the single-argument
    ``SessionFactory`` shape used by graph nodes; callers that manage their
    own engine (e.g. app startup, so it can be disposed on shutdown) bind it
    with ``functools.partial(get_session, sessionmaker=...)``.
    """
    maker = sessionmaker or _get_default_sessionmaker()
    async with maker() as session:
        async with session.begin():
            await set_tenant_context(session, tenant_id)
            yield session


@asynccontextmanager
async def get_raw_session(
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Session with no tenant context set — for privileged, cross-tenant reads.

    Only the startup sweep (finding non-terminal jobs across all tenants)
    should use this. It reads no tenant-scoped columns beyond ``id`` and
    ``tenant_id`` needed to route work back through the tenant-scoped
    ``get_session`` for everything else. In production, forced RLS means
    this only sees rows if the connecting role has ``BYPASSRLS`` (or is the
    table owner without ``FORCE ROW LEVEL SECURITY`` friction) — grant that
    to the app's sweep role, not to the general per-request role.
    """
    maker = sessionmaker or _get_default_sessionmaker()
    async with maker() as session:
        yield session
