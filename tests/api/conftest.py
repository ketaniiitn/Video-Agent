"""Shared API test wiring: in-memory SQLite + fakeredis + FakeGateway.

No PostgreSQL / no Redis server is used here. SQLite has no row-level
security, so the "RLS" assertions in ``test_jobs.py`` prove the API layer's
own ``tenant_id`` filter (the same belt-and-suspenders pattern already used
in ``app/nodes/*``) rather than the database-level RLS policy itself.
Real RLS enforcement is covered separately by ``tests/db/test_rls.py``
against PostgreSQL when ``TEST_DATABASE_URL`` is set.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import fakeredis.aioredis
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.api.deps import AppState, get_app_state
from app.config import Settings
from app.db.models import Base, Tenant
from app.gateway.client import FakeGateway, Usage
from app.graph.compile import build_graph
from app.jobs.runner import drain_background_tasks
from app.main import create_app

VALID_PLAN = {
    "beats": [
        {
            "name": name,
            "duration_seconds": 10,
            "action": f"{name} action",
            "camera": "35mm tracking shot",
        }
        for name in ("setup", "development", "turn", "resolution")
    ]
}
VALID_BIBLE = {
    "character": "Mara, a bicycle courier",
    "wardrobe": "yellow rain jacket",
    "location": "flooded city streets",
    "lighting": "blue-hour ambient light",
    "palette": "teal and amber",
    "lens": "35mm",
}


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


async def make_db_maker() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    # SQLite ignores FK constraints by default; Postgres always enforces
    # them. Turning this on is what makes the FK-ordering bug on
    # ``POST /jobs`` (idempotency key flushed before its job existed)
    # reproducible against these tests instead of only in production.
    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


async def make_tenants(maker: async_sessionmaker[AsyncSession]) -> tuple:
    tenant_a, tenant_b = uuid4(), uuid4()
    async with maker() as session:
        session.add_all([Tenant(id=tenant_a, name="A"), Tenant(id=tenant_b, name="B")])
        await session.commit()
    return tenant_a, tenant_b


def session_factory_for(maker: async_sessionmaker[AsyncSession]):
    @asynccontextmanager
    async def session_factory(_tenant_id) -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    return session_factory


def sweep_session_factory_for(maker: async_sessionmaker[AsyncSession]):
    @asynccontextmanager
    async def sweep_session_factory() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            yield session

    return sweep_session_factory


def default_gateway() -> FakeGateway:
    return FakeGateway(
        responses={"story_plan": VALID_PLAN, "continuity_bible": VALID_BIBLE},
        usage=Usage(usd=0.01, tokens=10),
    )


class TestApp:
    """Everything one API test needs, wired to sqlite + fakeredis."""

    def __init__(
        self,
        client: AsyncClient,
        state: AppState,
        maker: async_sessionmaker[AsyncSession],
        tenant_a,
        tenant_b,
    ):
        self.client = client
        self.state = state
        self.maker = maker
        self.tenant_a = tenant_a
        self.tenant_b = tenant_b

    async def aclose(self) -> None:
        await drain_background_tasks(self.state)
        await self.client.aclose()
        await self.state.redis.aclose()

    async def drain_background_tasks(self) -> None:
        await drain_background_tasks(self.state)


async def build_test_app(
    *,
    gateway: FakeGateway | None = None,
    feature_story_planning: bool = True,
    feature_shot_generation: bool = False,
    settings: Settings | None = None,
    provider=None,
) -> TestApp:
    maker = await make_db_maker()
    tenant_a, tenant_b = await make_tenants(maker)
    gateway = gateway or default_gateway()
    settings = settings or Settings(
        _env_file=None,
        feature_story_planning=feature_story_planning,
        feature_shot_generation=feature_shot_generation,
    )
    redis = fakeredis.aioredis.FakeRedis()
    session_factory = session_factory_for(maker)
    graph = await build_graph(
        MemorySaver(),
        gateway=gateway,
        session_factory=session_factory,
        settings=settings,
        provider=provider,
    )
    state = AppState(
        settings=settings,
        redis=redis,
        session_factory=session_factory,
        sweep_session_factory=sweep_session_factory_for(maker),
        graph=graph,
        gateway=gateway,
    )

    app = create_app()
    app.dependency_overrides[get_app_state] = lambda: state
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return TestApp(client, state, maker, tenant_a, tenant_b)
