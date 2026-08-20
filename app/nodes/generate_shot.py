import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CostLedger, Job, Shot
from app.domain.errors import AppError
from app.domain.schemas import ContinuityBible, JobStatus, ShotStatus, StoryPlan
from app.graph.budgets import BudgetExceeded, check_budget
from app.graph.state import VideoAgentState
from app.observability.logging import log_json, log_json_error
from app.providers.protocols import GenerateClipRequest, VideoProvider
from app.storage.local import clip_path, save_bytes

SessionFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
logger = logging.getLogger(__name__)


def make_generate_shot_node(
    beat_index: int,
    *,
    provider: VideoProvider,
    session_factory: SessionFactory,
    media_root: str,
):
    if beat_index < 1 or beat_index > 4:
        raise ValueError("beat_index must be 1..4")

    async def generate_shot_node(state: VideoAgentState) -> dict:
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

        existing = await _load_succeeded_shot(
            session_factory, tenant_id, job_id, beat_index
        )
        if existing is not None:
            return {
                "prior_frame_path": existing.frame_path,
                "current_clip_path": existing.clip_path,
                "current_beat_index": beat_index,
                **_budget_delta(budget_state),
            }

        if beat_index > 1 and "frame_conditioning" not in provider.capabilities():
            await _set_job_status(
                session_factory,
                tenant_id,
                job_id,
                JobStatus.PARTIAL
                if await _any_succeeded_shot(session_factory, tenant_id, job_id)
                else JobStatus.FAILED,
            )
            raise AppError(
                "PROVIDER_CAPABILITY_MISSING",
                "Video provider lacks frame_conditioning required for shot continuity",
                http_status=502,
            )

        bible = ContinuityBible.model_validate(state["continuity_bible"])
        plan = StoryPlan.model_validate(state["story_plan"])
        beat = plan.beats[beat_index - 1]
        prior = state.get("prior_frame_path") if beat_index > 1 else None
        prompt = _build_shot_prompt(bible, beat, beat_index)

        try:
            check_budget(budget_state)
        except BudgetExceeded:
            return await _partial(budget_state, session_factory, tenant_id, job_id)

        try:
            log_json(
                logger,
                "shot_generation_started",
                job_id=str(job_id),
                beat_index=beat_index,
                duration_seconds=beat.duration_seconds,
            )
            result = await provider.generate_clip(
                GenerateClipRequest(
                    prompt=prompt,
                    duration_seconds=beat.duration_seconds,
                    prior_frame_path=prior,
                )
            )
        except AppError as exc:
            log_json_error(
                logger,
                "shot_generation_failed",
                job_id=str(job_id),
                beat_index=beat_index,
                code=exc.code,
                error=str(exc)[:500],
            )
            return await _provider_failure(
                budget_state,
                session_factory,
                tenant_id,
                job_id,
                signature=exc.code,
                error_message=str(exc)[:500],
                previous=state.get("last_failure_signature"),
            )
        path = clip_path(media_root, tenant_id, job_id, beat_index)
        await save_bytes(path, result.video_bytes)

        cost = Decimal(str(result.cost_usd))
        async with session_factory(tenant_id) as session:
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)
            job.budget_used_usd = Decimal(str(job.budget_used_usd)) + cost
            job.budget_used_iterations = int(job.budget_used_iterations) + 1
            await session.execute(
                _shot_upsert(
                    session,
                    tenant_id=tenant_id,
                    job_id=job_id,
                    beat_index=beat_index,
                    status=ShotStatus.SUCCEEDED,
                    clip_path=str(path),
                    cost_usd=cost,
                    provider_id=result.provider_id,
                    seed=result.seed,
                    prompt=prompt,
                )
            )
            shot_id = await session.scalar(
                select(Shot.id).where(
                    Shot.job_id == job_id,
                    Shot.tenant_id == tenant_id,
                    Shot.beat_index == beat_index,
                )
            )
            session.add(
                CostLedger(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    shot_id=shot_id,
                    usd=cost,
                    tokens=None,
                    provider_id=result.provider_id,
                )
            )
            await session.commit()
            budget_state["budget_used_usd"] = float(job.budget_used_usd)
            budget_state["budget_used_iterations"] = int(job.budget_used_iterations)

        log_json(
            logger,
            "shot_generation_succeeded",
            job_id=str(job_id),
            beat_index=beat_index,
            provider_id=result.provider_id,
            cost_usd=result.cost_usd,
        )
        return {
            "current_clip_path": str(path),
            "current_beat_index": beat_index,
            **_budget_delta(budget_state),
        }

    return generate_shot_node


def _build_shot_prompt(bible: ContinuityBible, beat, beat_index: int) -> str:
    return (
        f"Beat {beat_index} ({beat.name}): {beat.action}. "
        f"Camera: {beat.camera}. "
        f"Character: {bible.character}; wardrobe: {bible.wardrobe}; "
        f"location: {bible.location}; lighting: {bible.lighting}; "
        f"palette: {bible.palette}; lens: {bible.lens}."
    )


async def _load_succeeded_shot(
    session_factory: SessionFactory,
    tenant_id: UUID,
    job_id: UUID,
    beat_index: int,
) -> Shot | None:
    async with session_factory(tenant_id) as session:
        return await session.scalar(
            select(Shot).where(
                Shot.job_id == job_id,
                Shot.tenant_id == tenant_id,
                Shot.beat_index == beat_index,
                Shot.status == ShotStatus.SUCCEEDED,
            )
        )


async def _any_succeeded_shot(
    session_factory: SessionFactory, tenant_id: UUID, job_id: UUID
) -> bool:
    async with session_factory(tenant_id) as session:
        row = await session.scalar(
            select(Shot.id).where(
                Shot.job_id == job_id,
                Shot.tenant_id == tenant_id,
                Shot.status == ShotStatus.SUCCEEDED,
            )
        )
        return row is not None


def _shot_upsert(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    job_id: UUID,
    beat_index: int,
    status: ShotStatus,
    clip_path: str,
    cost_usd: Decimal,
    provider_id: str,
    seed: int | None,
    prompt: str,
):
    values = {
        "tenant_id": tenant_id,
        "job_id": job_id,
        "beat_index": beat_index,
        "status": status,
        "clip_path": clip_path,
        "cost_usd": cost_usd,
        "provider_id": provider_id,
        "seed": seed,
        "prompt": prompt,
        "attempt_count": 1,
    }
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"
    insert = sqlite_insert if dialect == "sqlite" else postgresql_insert
    stmt = insert(Shot).values(**values)
    return stmt.on_conflict_do_update(
        index_elements=["job_id", "beat_index"],
        set_={
            "status": status,
            "clip_path": clip_path,
            "cost_usd": cost_usd,
            "provider_id": provider_id,
            "seed": seed,
            "prompt": prompt,
            "attempt_count": values["attempt_count"],
        },
    )


async def persist_gateway_usage(
    session_factory: SessionFactory,
    tenant_id: UUID,
    job_id: UUID,
    usd: float,
    tokens: int,
) -> None:
    async with session_factory(tenant_id) as session:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)
        job.budget_used_usd = Decimal(str(job.budget_used_usd)) + Decimal(str(usd))
        job.budget_used_tokens = int(job.budget_used_tokens) + int(tokens)
        job.budget_used_iterations = int(job.budget_used_iterations) + 1
        await session.commit()


async def _provider_failure(
    state: dict,
    session_factory: SessionFactory,
    tenant_id: UUID,
    job_id: UUID,
    *,
    signature: str,
    error_message: str | None = None,
    previous: str | None,
) -> dict:
    extra = {
        "last_failure_signature": signature,
        "last_error_message": error_message,
    }
    if previous == signature:
        await _set_job_status(
            session_factory, tenant_id, job_id, JobStatus.FAILED_NO_PROGRESS
        )
        return {
            "outcome": "FAILED_NO_PROGRESS",
            **extra,
            **_budget_delta(state),
        }
    any_ok = await _any_succeeded_shot(session_factory, tenant_id, job_id)
    status = JobStatus.PARTIAL if any_ok else JobStatus.FAILED
    await _set_job_status(session_factory, tenant_id, job_id, status)
    return {
        "outcome": "PARTIAL" if any_ok else "FAILED",
        **extra,
        **_budget_delta(state),
    }


async def _load_budget(
    session_factory: SessionFactory, tenant_id: UUID, job_id: UUID
) -> dict:
    async with session_factory(tenant_id) as session:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)
        return {
            "budget_used_usd": float(job.budget_used_usd),
            "budget_used_tokens": int(job.budget_used_tokens),
            "budget_used_iterations": int(job.budget_used_iterations),
            "budget_max_usd": float(job.budget_max_usd),
            "budget_max_tokens": int(job.budget_max_tokens),
            "budget_max_iterations": int(job.budget_max_iterations),
            "budget_max_wall_clock_seconds": int(job.budget_max_wall_clock_seconds),
            "started_at_iso": job.started_at.isoformat() if job.started_at else "",
        }


def _budget_delta(state: dict) -> dict:
    return {
        "budget_used_usd": float(state["budget_used_usd"]),
        "budget_used_tokens": int(state["budget_used_tokens"]),
        "budget_used_iterations": int(state["budget_used_iterations"]),
    }


async def _stop_if_budget_exhausted(
    state: dict,
    session_factory: SessionFactory,
    tenant_id: UUID,
    job_id: UUID,
) -> dict | None:
    try:
        check_budget(state)
    except BudgetExceeded:
        return await _partial(state, session_factory, tenant_id, job_id)
    return None


async def _partial(
    state: dict,
    session_factory: SessionFactory,
    tenant_id: UUID,
    job_id: UUID,
) -> dict:
    await _set_job_status(session_factory, tenant_id, job_id, JobStatus.PARTIAL)
    return {"outcome": "PARTIAL", **_budget_delta(state)}


async def _set_job_status(
    session_factory: SessionFactory,
    tenant_id: UUID,
    job_id: UUID,
    status: JobStatus,
) -> None:
    async with session_factory(tenant_id) as session:
        await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.tenant_id == tenant_id)
            .values(status=status)
        )
        await session.commit()
