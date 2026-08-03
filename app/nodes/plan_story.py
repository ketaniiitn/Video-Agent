from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, StoryPlanRow
from app.domain.errors import AppError
from app.domain.schemas import JobStatus, StoryPlan
from app.gateway.protocols import GatewayClient, Usage
from app.graph.budgets import BudgetExceeded, check_budget
from app.graph.state import VideoAgentState
from app.prompts.registry import get_prompt

SessionFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
_SCHEMA_ATTEMPTS = 3


async def plan_story_node(
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
        plan, job = existing
        return {
            "story_plan": plan.model_dump(mode="json"),
            **_budget_delta(job),
        }

    messages = get_prompt("story_plan", 1).render({"premise": state["prompt"]})
    plan: StoryPlan | None = None
    for _attempt in range(_SCHEMA_ATTEMPTS):
        partial = await _stop_if_budget_exhausted(
            budget_state, session_factory, tenant_id, job_id
        )
        if partial is not None:
            return partial
        payload, usage = await gateway.complete_json(
            "reasoning-high", messages, schema_name="story_plan"
        )
        _apply_usage(budget_state, usage)
        await _persist_usage(session_factory, tenant_id, job_id, usage)
        try:
            plan = StoryPlan.model_validate(payload)
            break
        except ValidationError:
            continue

    if plan is None:
        await _set_job_status(
            session_factory, tenant_id, job_id, JobStatus.FAILED
        )
        raise AppError(
            "SCHEMA_INVALID",
            "Story plan remained schema-invalid after gateway retries",
            http_status=502,
        )

    async with session_factory(tenant_id) as session:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)

        await session.execute(
            _story_plan_upsert(
                session,
                tenant_id=tenant_id,
                job_id=job_id,
                beats_json=plan.model_dump(mode="json"),
            )
        )
        await session.commit()
        return {
            "story_plan": plan.model_dump(mode="json"),
            **_budget_delta(budget_state),
        }


async def _load_existing(
    session_factory: SessionFactory, tenant_id: UUID, job_id: UUID
) -> tuple[StoryPlan, Job] | None:
    async with session_factory(tenant_id) as session:
        row = await session.scalar(
            select(StoryPlanRow).where(
                StoryPlanRow.job_id == job_id,
                StoryPlanRow.tenant_id == tenant_id,
            )
        )
        if row is None:
            return None
        job = await session.get(Job, job_id)
        if job is None:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)
        return StoryPlan.model_validate(row.beats_json), job


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
        result = await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.tenant_id == tenant_id)
            .values(
                budget_used_usd=Job.budget_used_usd
                + Decimal(str(usage.usd)),
                budget_used_tokens=Job.budget_used_tokens + usage.tokens,
                budget_used_iterations=Job.budget_used_iterations + 1,
            )
        )
        if result.rowcount != 1:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)
        await session.commit()


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


def _story_plan_upsert(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    job_id: UUID,
    beats_json: dict,
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
    statement = insert(StoryPlanRow).values(
        tenant_id=tenant_id,
        job_id=job_id,
        beats_json=beats_json,
    )
    return statement.on_conflict_do_update(
        index_elements=[StoryPlanRow.job_id],
        set_={"beats_json": statement.excluded.beats_json},
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
