from datetime import UTC, datetime, timedelta
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import (
    ContinuityBibleRow,
    IdempotencyKey,
    Job,
    StoryPlanRow,
    Tenant,
)
from app.db.session import get_sessionmaker
from app.domain.schemas import JobStatus


async def _seed_tenant_data(Session):
    tenant_a, tenant_b, job_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
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
        session.add(
            Job(
                id=job_id,
                tenant_id=tenant_a,
                status=JobStatus.QUEUED,
                prompt="x",
                trace_id="tr_test",
                budget_max_usd=1,
                budget_max_tokens=1_000,
                budget_max_iterations=10,
                budget_max_wall_clock_seconds=60,
            )
        )
        await session.flush()
        session.add_all(
            [
                StoryPlanRow(
                    tenant_id=tenant_a,
                    job_id=job_id,
                    beats_json={"beats": []},
                ),
                ContinuityBibleRow(
                    tenant_id=tenant_a,
                    job_id=job_id,
                    bible_json={"character": "A"},
                ),
                IdempotencyKey(
                    tenant_id=tenant_a,
                    key=f"key-{job_id}",
                    request_hash="hash",
                    job_id=job_id,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                ),
            ]
        )
        await session.execute(
            text(
                "INSERT INTO checkpoints "
                "(thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata) "
                "VALUES (:thread_id, '', 'checkpoint-1', '{}'::jsonb, '{}'::jsonb)"
            ),
            {"thread_id": str(job_id)},
        )
        await session.commit()

    return tenant_a, tenant_b, job_id


@pytest.mark.asyncio
async def test_tenant_cannot_read_other_tenants_job(db_engine):
    Session = get_sessionmaker(db_engine)
    _, tenant_b, job_id = await _seed_tenant_data(Session)

    async with Session() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_b)},
        )
        rows = (
            await session.execute(select(Job).where(Job.id == job_id))
        ).scalars().all()

    assert rows == []


@pytest.mark.asyncio
async def test_tenant_cannot_read_other_tenants_artifacts_or_checkpoint(db_engine):
    Session = get_sessionmaker(db_engine)
    _, tenant_b, job_id = await _seed_tenant_data(Session)

    async with Session() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_b)},
        )
        artifact_counts = [
            await session.scalar(select(func.count()).select_from(model))
            for model in (StoryPlanRow, ContinuityBibleRow, IdempotencyKey)
        ]
        checkpoint_count = await session.scalar(
            text(
                "SELECT count(*) FROM checkpoints "
                "WHERE thread_id = :thread_id"
            ),
            {"thread_id": str(job_id)},
        )

    assert artifact_counts == [0, 0, 0]
    assert checkpoint_count == 0


@pytest.mark.asyncio
async def test_cross_tenant_job_insert_is_denied(db_engine):
    Session = get_sessionmaker(db_engine)
    tenant_a, tenant_b, _ = await _seed_tenant_data(Session)

    async with Session() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_a)},
        )
        session.add(
            Job(
                tenant_id=tenant_b,
                status=JobStatus.QUEUED,
                prompt="cross-tenant",
                trace_id="tr_cross_tenant",
                budget_max_usd=1,
                budget_max_tokens=1_000,
                budget_max_iterations=10,
                budget_max_wall_clock_seconds=60,
            )
        )

        with pytest.raises(DBAPIError):
            await session.commit()


@pytest.mark.asyncio
async def test_missing_tenant_context_returns_no_rows(db_engine):
    Session = get_sessionmaker(db_engine)
    _, _, job_id = await _seed_tenant_data(Session)

    async with Session() as session:
        rows = (
            await session.execute(select(Job).where(Job.id == job_id))
        ).scalars().all()

    assert rows == []


@pytest.mark.asyncio
async def test_artifact_cannot_reference_another_tenants_job(db_engine):
    Session = get_sessionmaker(db_engine)
    _, tenant_b, job_id = await _seed_tenant_data(Session)

    async with Session() as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_b)},
        )
        session.add(
            IdempotencyKey(
                tenant_id=tenant_b,
                key=f"cross-tenant-{job_id}",
                request_hash="hash",
                job_id=job_id,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )

        with pytest.raises(DBAPIError):
            await session.commit()
