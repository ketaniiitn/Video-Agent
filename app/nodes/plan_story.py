from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, StoryPlanRow
from app.domain.errors import AppError
from app.domain.schemas import JobStatus, StoryPlan
from app.gateway.protocols import GatewayClient
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
    try:
        check_budget(state)
    except BudgetExceeded:
        await _set_job_status(
            session_factory, tenant_id, job_id, JobStatus.PARTIAL
        )
        return {"outcome": "PARTIAL"}

    existing = await _load_existing(session_factory, tenant_id, job_id)
    if existing is not None:
        plan, job = existing
        return {
            "story_plan": plan.model_dump(mode="json"),
            **_budget_delta(job),
        }

    messages = get_prompt("story_plan", 1).render({"premise": state["prompt"]})
    plan: StoryPlan | None = None
    total_usd = Decimal("0")
    total_tokens = 0
    gateway_calls = 0
    for _attempt in range(_SCHEMA_ATTEMPTS):
        payload, usage = await gateway.complete_json(
            "reasoning-high", messages, schema_name="story_plan"
        )
        total_usd += Decimal(str(usage.usd))
        total_tokens += usage.tokens
        gateway_calls += 1
        try:
            plan = StoryPlan.model_validate(payload)
            break
        except ValidationError:
            continue

    if plan is None:
        await _fail_job(
            session_factory,
            tenant_id,
            job_id,
            total_usd,
            total_tokens,
            gateway_calls,
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

        row = await session.scalar(
            select(StoryPlanRow).where(
                StoryPlanRow.job_id == job_id,
                StoryPlanRow.tenant_id == tenant_id,
            )
        )
        if row is None:
            session.add(
                StoryPlanRow(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    beats_json=plan.model_dump(mode="json"),
                )
            )
        else:
            row.beats_json = plan.model_dump(mode="json")

        job.budget_used_usd += total_usd
        job.budget_used_tokens += total_tokens
        job.budget_used_iterations += gateway_calls
        await session.commit()
        return {
            "story_plan": plan.model_dump(mode="json"),
            **_budget_delta(job),
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


async def _fail_job(
    session_factory: SessionFactory,
    tenant_id: UUID,
    job_id: UUID,
    used_usd: Decimal,
    used_tokens: int,
    used_iterations: int,
) -> None:
    async with session_factory(tenant_id) as session:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)
        job.status = JobStatus.FAILED
        job.budget_used_usd += used_usd
        job.budget_used_tokens += used_tokens
        job.budget_used_iterations += used_iterations
        await session.commit()


def _budget_delta(job: Job) -> dict:
    return {
        "budget_used_usd": float(job.budget_used_usd),
        "budget_used_tokens": job.budget_used_tokens,
        "budget_used_iterations": job.budget_used_iterations,
    }
