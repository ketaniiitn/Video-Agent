import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from shutil import copyfile
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import Job, Shot
from app.domain.errors import AppError
from app.graph.state import VideoAgentState
from app.observability.logging import log_json
from app.storage.local import thumbnail_path
from app.storage.presign import presign_url

SessionFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]
logger = logging.getLogger(__name__)


def make_deliver_node(
    *,
    session_factory: SessionFactory,
    settings: Settings,
    media_root: str,
):
    async def deliver_node(state: VideoAgentState) -> dict:
        tenant_id = UUID(state["tenant_id"])
        job_id = UUID(state["job_id"])
        assembled = state.get("assembled_path")
        if not assembled:
            raise AppError(
                "ASSEMBLE_NO_CLIPS",
                "Deliver requires an assembled artifact",
                http_status=500,
            )

        async with session_factory(tenant_id) as session:
            shots = (
                await session.execute(
                    select(Shot)
                    .where(Shot.job_id == job_id, Shot.tenant_id == tenant_id)
                    .order_by(Shot.beat_index)
                )
            ).scalars().all()

        thumb = thumbnail_path(media_root, tenant_id, job_id)
        source_frame = next((shot.frame_path for shot in shots if shot.frame_path), None)
        if source_frame and Path(source_frame).exists():
            thumb.parent.mkdir(parents=True, exist_ok=True)
            copyfile(source_frame, thumb)

        download_url = presign_url(
            job_id=job_id, artifact="assembled.mp4", settings=settings
        )
        thumbnail_url = (
            presign_url(job_id=job_id, artifact="thumbnail.jpg", settings=settings)
            if thumb.exists()
            else None
        )
        degraded = bool(state.get("job_degraded"))
        outcome = "PARTIAL" if degraded or state.get("outcome") == "PARTIAL" else "SUCCESS"

        async with session_factory(tenant_id) as session:
            await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.tenant_id == tenant_id)
                .values(
                    download_url=download_url,
                    thumbnail_url=thumbnail_url,
                    degraded=degraded,
                )
            )
            await session.commit()

        log_json(
            logger,
            "job_delivered",
            job_id=str(job_id),
            outcome=outcome,
            degraded=degraded,
        )
        return {
            "download_url": download_url,
            "delivered": True,
            "outcome": outcome,
            "shots_completed": True,
            "job_degraded": degraded,
        }

    return deliver_node
