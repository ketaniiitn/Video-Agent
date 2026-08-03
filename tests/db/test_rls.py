import uuid

import pytest
from sqlalchemy import select, text

from app.db.models import Job, Tenant
from app.db.session import get_sessionmaker
from app.domain.schemas import JobStatus


@pytest.mark.asyncio
async def test_tenant_cannot_read_other_tenants_job(db_engine):
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    Session = get_sessionmaker(db_engine)

    async with Session() as session:
        session.add_all(
            [Tenant(id=tenant_a, name="A"), Tenant(id=tenant_b, name="B")]
        )
        await session.commit()

    async with Session() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_a)},
        )
        job = Job(
            id=uuid.uuid4(),
            tenant_id=tenant_a,
            status=JobStatus.QUEUED,
            prompt="x",
            trace_id="tr_test",
            budget_max_usd=1,
            budget_max_tokens=1_000,
            budget_max_iterations=10,
            budget_max_wall_clock_seconds=60,
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    async with Session() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_b)},
        )
        rows = (
            await session.execute(select(Job).where(Job.id == job_id))
        ).scalars().all()

    assert rows == []
