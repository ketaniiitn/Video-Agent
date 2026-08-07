from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Shot
from app.domain.errors import AppError
from app.domain.schemas import ShotStatus
from app.graph.state import VideoAgentState
from app.media.ffmpeg import extract_last_frame
from app.storage.local import frame_path

SessionFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
FrameExtractor = Callable[..., object]


def make_chain_frame_node(
    beat_index: int,
    *,
    session_factory: SessionFactory,
    media_root: str,
    extract_frame: FrameExtractor | None = None,
):
    if beat_index < 1 or beat_index > 4:
        raise ValueError("beat_index must be 1..4")

    async def chain_frame_node(state: VideoAgentState) -> dict:
        from app.media.ffmpeg import extract_last_frame as default_extract

        extractor = extract_frame or default_extract
        tenant_id = UUID(state["tenant_id"])
        job_id = UUID(state["job_id"])
        clip = state.get("current_clip_path")
        if not clip:
            async with session_factory(tenant_id) as session:
                shot = await session.scalar(
                    select(Shot).where(
                        Shot.job_id == job_id,
                        Shot.tenant_id == tenant_id,
                        Shot.beat_index == beat_index,
                        Shot.status == ShotStatus.SUCCEEDED,
                    )
                )
            if shot is None or not shot.clip_path:
                raise AppError(
                    "SHOT_CLIP_MISSING",
                    f"No clip available to chain for beat {beat_index}",
                    http_status=500,
                )
            clip = shot.clip_path

        out = frame_path(media_root, tenant_id, job_id, beat_index)
        await extractor(Path(clip), out)

        async with session_factory(tenant_id) as session:
            await session.execute(
                update(Shot)
                .where(
                    Shot.job_id == job_id,
                    Shot.tenant_id == tenant_id,
                    Shot.beat_index == beat_index,
                )
                .values(frame_path=str(out))
            )
            await session.commit()

        delta: dict = {
            "prior_frame_path": str(out),
            "current_beat_index": beat_index,
        }
        if beat_index == 4:
            delta["outcome"] = "SUCCESS"
            delta["shots_completed"] = True
        return delta

    return chain_frame_node
