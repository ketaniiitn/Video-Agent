import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, Shot
from app.domain.errors import AppError
from app.graph.state import VideoAgentState
from app.observability.logging import log_json, log_json_error
from app.storage.local import assembled_path

SessionFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
StitchFn = Callable[..., object]
logger = logging.getLogger(__name__)


def make_assemble_node(
    *,
    session_factory: SessionFactory,
    media_root: str,
    stitch: StitchFn | None = None,
):
    async def assemble_node(state: VideoAgentState) -> dict:
        from app.media.ffmpeg import stitch_clips as default_stitch

        stitcher = stitch or default_stitch
        tenant_id = UUID(state["tenant_id"])
        job_id = UUID(state["job_id"])
        async with session_factory(tenant_id) as session:
            shots = (
                await session.execute(
                    select(Shot)
                    .where(Shot.job_id == job_id, Shot.tenant_id == tenant_id)
                    .order_by(Shot.beat_index)
                )
            ).scalars().all()

        clips = [Path(shot.clip_path) for shot in shots if shot.clip_path]
        if not clips:
            raise AppError(
                "ASSEMBLE_NO_CLIPS",
                "No clips available to assemble; nothing was preserved to deliver",
                http_status=500,
            )

        output = assembled_path(media_root, tenant_id, job_id)
        log_json(
            logger,
            "assemble_started",
            job_id=str(job_id),
            clip_count=len(clips),
        )
        try:
            await stitcher(clips, output)
        except AppError as exc:
            log_json_error(
                logger,
                "assemble_failed",
                job_id=str(job_id),
                code=exc.code,
                error=str(exc)[:400],
            )
            raise

        degraded = bool(state.get("job_degraded")) or any(shot.degraded for shot in shots)
        incomplete = len(clips) < 4
        outcome = "PARTIAL" if degraded or incomplete else state.get("outcome") or "SUCCESS"

        async with session_factory(tenant_id) as session:
            await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.tenant_id == tenant_id)
                .values(assembled_path=str(output), degraded=degraded or incomplete)
            )
            await session.commit()

        return {
            "assembled_path": str(output),
            "shots_completed": True,
            "job_degraded": degraded or incomplete,
            "outcome": outcome if outcome in {"PARTIAL", "FAILED"} else "SUCCESS",
        }

    return assemble_node
