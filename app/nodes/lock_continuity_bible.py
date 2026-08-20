import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

import logging

from app.db.models import ContinuityBibleRow, Job
from app.domain.errors import AppError
from app.domain.schemas import ContinuityBible, JobStatus
from app.gateway.protocols import GatewayClient, Usage
from app.graph.budgets import BudgetExceeded, check_budget
from app.graph.state import VideoAgentState
from app.observability.logging import log_json
from app.prompts.registry import get_prompt

SessionFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
_SCHEMA_ATTEMPTS = 3
logger = logging.getLogger(__name__)


async def lock_continuity_bible_node(
    state: VideoAgentState,
    *,
    gateway: GatewayClient,
    session_factory: SessionFactory,
) -> dict:
    tenant_id = UUID(state["tenant_id"])
    job_id = UUID(state["job_id"])
    budget_state = {
        **state,
        **await _load_budget(session_factory, tenant_id, job_id),
    }
    partial = await _stop_if_budget_exhausted(
        budget_state, session_factory, tenant_id, job_id
    )
    if partial is not None:
        return partial

    existing = await _load_existing(session_factory, tenant_id, job_id)
    if existing is not None:
        bible, job = existing
        return {
            "continuity_bible": bible.model_dump(mode="json"),
            "outcome": "SUCCESS",
            **_budget_delta(job),
        }

    messages = get_prompt("continuity_bible", 1).render(
        {"story_plan": json.dumps(state["story_plan"], sort_keys=True)}
    )
    bible: ContinuityBible | None = None
    successful_usage: Usage | None = None
    for _attempt in range(_SCHEMA_ATTEMPTS):
        partial = await _stop_if_budget_exhausted(
            budget_state, session_factory, tenant_id, job_id
        )
        if partial is not None:
            return partial
        payload, usage = await gateway.complete_json(
            "reasoning-high", messages, schema_name="continuity_bible"
        )
        _apply_usage(budget_state, usage)
        try:
            bible = ContinuityBible.model_validate(payload)
            successful_usage = usage
            break
        except ValidationError:
            await _persist_usage(session_factory, tenant_id, job_id, usage)
            continue

    if bible is None or successful_usage is None:
        await _set_job_status(
            session_factory, tenant_id, job_id, JobStatus.FAILED
        )
        raise AppError(
            "SCHEMA_INVALID",
            "Continuity bible remained schema-invalid after gateway retries",
            http_status=502,
        )

    async with session_factory(tenant_id) as session:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)

        locked_at = datetime.now(timezone.utc)
        await _increment_usage(session, tenant_id, job_id, successful_usage)
        await session.execute(
            _continuity_bible_upsert(
                session,
                tenant_id=tenant_id,
                job_id=job_id,
                bible_json=bible.model_dump(mode="json"),
                locked_at=locked_at,
            )
        )
        job.status = JobStatus.BIBLE_LOCKED
        await session.commit()
        log_json(logger, "bible_locked", job_id=str(job_id))
        return {
            "continuity_bible": bible.model_dump(mode="json"),
            "outcome": "SUCCESS",
            **_budget_delta(budget_state),
        }


async def _load_existing(
    session_factory: SessionFactory, tenant_id: UUID, job_id: UUID
) -> tuple[ContinuityBible, Job] | None:
    async with session_factory(tenant_id) as session:
        row = await session.scalar(
            select(ContinuityBibleRow).where(
                ContinuityBibleRow.job_id == job_id,
                ContinuityBibleRow.tenant_id == tenant_id,
            )
        )
        if row is None:
            return None
        job = await session.get(Job, job_id)
        if job is None:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)
        return ContinuityBible.model_validate(row.bible_json), job


async def _set_job_status(
    session_factory: SessionFactory,
    tenant_id: UUID,
    job_id: UUID,
    status: JobStatus,
) -> None:
    async with session_factory(tenant_id) as session:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)
        job.status = status
        await session.commit()


async def _load_budget(
    session_factory: SessionFactory,
    tenant_id: UUID,
    job_id: UUID,
) -> dict:
    async with session_factory(tenant_id) as session:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)
        return _budget_delta(job)


async def _persist_usage(
    session_factory: SessionFactory,
    tenant_id: UUID,
    job_id: UUID,
    usage: Usage,
) -> None:
    async with session_factory(tenant_id) as session:
        await _increment_usage(session, tenant_id, job_id, usage)
        await session.commit()


async def _increment_usage(
    session: AsyncSession,
    tenant_id: UUID,
    job_id: UUID,
    usage: Usage,
) -> None:
    result = await session.execute(
        update(Job)
        .where(Job.id == job_id, Job.tenant_id == tenant_id)
        .values(
            budget_used_usd=Job.budget_used_usd + Decimal(str(usage.usd)),
            budget_used_tokens=Job.budget_used_tokens + usage.tokens,
            budget_used_iterations=Job.budget_used_iterations + 1,
        )
    )
    if result.rowcount != 1:
        raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)


async def _stop_if_budget_exhausted(
    state: dict,
    session_factory: SessionFactory,
    tenant_id: UUID,
    job_id: UUID,
) -> dict | None:
    try:
        check_budget(state)
    except BudgetExceeded:
        await _set_job_status(
            session_factory, tenant_id, job_id, JobStatus.PARTIAL
        )
        return {"outcome": "PARTIAL", **_budget_delta(state)}
    return None


def _apply_usage(state: dict, usage: Usage) -> None:
    state["budget_used_usd"] = float(
        Decimal(str(state["budget_used_usd"])) + Decimal(str(usage.usd))
    )
    state["budget_used_tokens"] += usage.tokens
    state["budget_used_iterations"] += 1


def _continuity_bible_upsert(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    job_id: UUID,
    bible_json: dict,
    locked_at: datetime,
):
    dialect_name = session.get_bind().dialect.name
    insert = (
        postgresql_insert
        if dialect_name == "postgresql"
        else sqlite_insert
        if dialect_name == "sqlite"
        else None
    )
    if insert is None:
        raise RuntimeError(f"Unsupported database dialect: {dialect_name}")
    statement = insert(ContinuityBibleRow).values(
        tenant_id=tenant_id,
        job_id=job_id,
        bible_json=bible_json,
        locked_at=locked_at,
    )
    return statement.on_conflict_do_update(
        index_elements=[ContinuityBibleRow.job_id],
        set_={
            "bible_json": statement.excluded.bible_json,
            "locked_at": statement.excluded.locked_at,
        },
    )


def _budget_delta(source: Job | dict) -> dict:
    return {
        "budget_used_usd": float(
            source.budget_used_usd
            if isinstance(source, Job)
            else source["budget_used_usd"]
        ),
        "budget_used_tokens": (
            source.budget_used_tokens
            if isinstance(source, Job)
            else source["budget_used_tokens"]
        ),
        "budget_used_iterations": (
            source.budget_used_iterations
            if isinstance(source, Job)
            else source["budget_used_iterations"]
        ),
    }
