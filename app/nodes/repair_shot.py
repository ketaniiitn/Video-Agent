from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CostLedger, Job, Shot
from app.domain.errors import AppError
from app.domain.schemas import ContinuityBible, ShotStatus, StoryPlan
from app.graph.budgets import BudgetExceeded, check_budget
from app.graph.state import VideoAgentState
from app.nodes.generate_shot import (
    _budget_delta,
    _build_shot_prompt,
    _load_budget,
    _partial,
    _provider_failure,
    _stop_if_budget_exhausted,
)
from app.providers.protocols import GenerateClipRequest, VideoProvider
from app.storage.local import job_dir, save_bytes

SessionFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


def make_repair_shot_node(
    beat_index: int,
    *,
    provider: VideoProvider,
    session_factory: SessionFactory,
    media_root: str,
):
    if beat_index < 1 or beat_index > 4:
        raise ValueError("beat_index must be 1..4")

    async def repair_shot_node(state: VideoAgentState) -> dict:
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

        try:
            check_budget(budget_state)
        except BudgetExceeded:
            return await _partial(budget_state, session_factory, tenant_id, job_id)

        bible = ContinuityBible.model_validate(state["continuity_bible"])
        plan = StoryPlan.model_validate(state["story_plan"])
        beat = plan.beats[beat_index - 1]
        prior = state.get("prior_frame_path") if beat_index > 1 else None
        prompt = _build_shot_prompt(bible, beat, beat_index)

        try:
            result = await provider.generate_clip(
                GenerateClipRequest(
                    prompt=prompt,
                    duration_seconds=beat.duration_seconds,
                    prior_frame_path=prior,
                )
            )
        except AppError as exc:
            return await _provider_failure(
                budget_state,
                session_factory,
                tenant_id,
                job_id,
                signature=exc.code,
                error_message=str(exc)[:500],
                previous=state.get("last_failure_signature"),
            )

        attempt_dir = job_dir(media_root, tenant_id, job_id)
        shot_row = await _load_shot(session_factory, tenant_id, job_id, beat_index)
        next_repair = int(shot_row.repair_count if shot_row else 0) + 1
        path = attempt_dir / f"shot_{beat_index}_attempt_{next_repair}.mp4"
        await save_bytes(path, result.video_bytes)
        cost = Decimal(str(result.cost_usd))

        async with session_factory(tenant_id) as session:
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)
            job.budget_used_usd = Decimal(str(job.budget_used_usd)) + cost
            job.budget_used_iterations = int(job.budget_used_iterations) + 1
            await session.execute(
                update(Shot)
                .where(
                    Shot.job_id == job_id,
                    Shot.tenant_id == tenant_id,
                    Shot.beat_index == beat_index,
                )
                .values(
                    status=ShotStatus.SUCCEEDED,
                    attempt_count=Shot.attempt_count + 1,
                    repair_count=next_repair,
                    prompt=prompt,
                    provider_id=result.provider_id,
                    seed=result.seed,
                    cost_usd=Shot.cost_usd + cost,
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

        return {
            "current_clip_path": str(path),
            "current_beat_index": beat_index,
            "qc_passed": False,
            "repair_count": next_repair,
            **_budget_delta(budget_state),
        }

    return repair_shot_node


async def _load_shot(
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
            )
        )
