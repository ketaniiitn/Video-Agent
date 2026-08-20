from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from decimal import Decimal
from pathlib import Path
from shutil import copyfile
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import QcScore, Shot
from app.domain.errors import AppError
from app.domain.schemas import ContinuityBible, QcScoreResult, StoryPlan
from app.gateway.protocols import GatewayClient
from app.graph.state import VideoAgentState
from app.nodes.generate_shot import (
    _budget_delta,
    _load_budget,
    persist_gateway_usage,
    _stop_if_budget_exhausted,
)
from app.pipeline.constants import QC_PASS_THRESHOLD
from app.prompts.registry import get_prompt
from app.storage.local import clip_path

SessionFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


def make_qc_score_node(
    beat_index: int,
    *,
    gateway: GatewayClient,
    session_factory: SessionFactory,
    media_root: str,
    assemble_deliver: bool,
):
    if beat_index < 1 or beat_index > 4:
        raise ValueError("beat_index must be 1..4")

    async def qc_score_node(state: VideoAgentState) -> dict:
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

        shot = await _load_shot(session_factory, tenant_id, job_id, beat_index)
        if shot is None or not shot.clip_path:
            raise AppError(
                "SHOT_CLIP_MISSING",
                f"No clip available to QC for beat {beat_index}",
                http_status=500,
            )

        bible = ContinuityBible.model_validate(state["continuity_bible"])
        plan = StoryPlan.model_validate(state["story_plan"])
        beat = plan.beats[beat_index - 1]
        messages = get_prompt("qc_score", 1).render(
            {
                "bible": bible.model_dump_json(),
                "beat": f"{beat.name}: {beat.action}. Camera: {beat.camera}",
                "frame_path": shot.frame_path or "",
            }
        )
        payload, usage = await gateway.complete_json(
            "vision-default", messages, schema_name="qc_score"
        )
        await persist_gateway_usage(
            session_factory, tenant_id, job_id, usage.usd, usage.tokens
        )
        budget_state["budget_used_usd"] = float(budget_state["budget_used_usd"]) + usage.usd
        budget_state["budget_used_tokens"] = int(budget_state["budget_used_tokens"]) + usage.tokens
        budget_state["budget_used_iterations"] = int(budget_state["budget_used_iterations"]) + 1

        try:
            result = QcScoreResult.model_validate(payload)
        except ValidationError as exc:
            raise AppError(
                "SCHEMA_INVALID",
                "QC score remained schema-invalid",
                http_status=502,
            ) from exc

        score = Decimal(str(result.score))
        passed = float(score) >= QC_PASS_THRESHOLD
        await _record_score(
            session_factory,
            tenant_id=tenant_id,
            job_id=job_id,
            shot=shot,
            beat_index=beat_index,
            score=score,
            rationale=result.rationale,
            clip_source=state.get("current_clip_path") or shot.clip_path,
            media_root=media_root,
        )

        delta: dict = {
            "qc_passed": passed,
            "current_beat_index": beat_index,
            "current_clip_path": shot.clip_path,
            "repair_count": int(shot.repair_count or 0),
            **_budget_delta(budget_state),
        }
        if passed and beat_index == 4 and not assemble_deliver:
            delta["outcome"] = "SUCCESS"
            delta["shots_completed"] = True
        return delta

    return qc_score_node


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


async def _record_score(
    session_factory: SessionFactory,
    *,
    tenant_id: UUID,
    job_id: UUID,
    shot: Shot,
    beat_index: int,
    score: Decimal,
    rationale: str,
    clip_source: str,
    media_root: str,
) -> None:
    canonical = clip_path(media_root, tenant_id, job_id, beat_index)
    previous = shot.qc_score
    keep_new = previous is None or score >= previous
    if keep_new and Path(clip_source).exists() and Path(clip_source) != canonical:
        canonical.parent.mkdir(parents=True, exist_ok=True)
        copyfile(clip_source, canonical)
    stored_clip = str(canonical) if keep_new else shot.clip_path

    async with session_factory(tenant_id) as session:
        session.add(
            QcScore(
                tenant_id=tenant_id,
                job_id=job_id,
                shot_id=shot.id,
                beat_index=beat_index,
                attempt=int(shot.attempt_count or 1),
                score=score,
                rationale=rationale,
            )
        )
        values: dict = {"qc_score": score if keep_new else previous}
        if keep_new:
            values["clip_path"] = stored_clip
        await session.execute(
            update(Shot)
            .where(
                Shot.id == shot.id,
                Shot.tenant_id == tenant_id,
            )
            .values(**values)
        )
        await session.commit()
