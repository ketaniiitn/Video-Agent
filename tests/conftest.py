from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.models import Base, Job, Tenant
from app.domain.schemas import JobStatus


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs):
    return "JSON"


@pytest_asyncio.fixture
async def node_db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = uuid4()
    job_id = uuid4()
    async with maker() as session:
        session.add(Tenant(id=tenant_id, name="test tenant"))
        session.add(
            Job(
                id=job_id,
                tenant_id=tenant_id,
                status=JobStatus.RUNNING,
                prompt="A courier crosses a flooded city.",
                trace_id="trace-test",
                budget_max_usd=Decimal("1"),
                budget_max_tokens=50_000,
                budget_max_iterations=20,
                budget_max_wall_clock_seconds=600,
                started_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()

    @asynccontextmanager
    async def session_factory(_tenant_id):
        async with maker() as session:
            yield session

    try:
        yield maker, session_factory, tenant_id, job_id
    finally:
        await engine.dispose()
