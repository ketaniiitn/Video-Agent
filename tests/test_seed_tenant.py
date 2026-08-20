from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.cli import seed_tenant
from app.config import Settings
from app.db.models import Base, Tenant
from app.db.session import create_engine_from_settings, get_sessionmaker


@pytest.mark.asyncio
async def test_seed_tenant_is_idempotent(tmp_path):
    db_path = tmp_path / "seed.db"
    tenant_id = uuid4()
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{db_path}",
        tenant_id=str(tenant_id),
        tenant_name="dev",
    )
    engine = create_engine_from_settings(settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()

    first = await seed_tenant(settings)
    second = await seed_tenant(settings)
    assert first == second == tenant_id

    engine = create_engine_from_settings(settings)
    maker = get_sessionmaker(engine)
    async with maker() as session:
        count = await session.scalar(select(func.count()).select_from(Tenant))
    await engine.dispose()
    assert count == 1
