from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, Shot
from app.graph.state import VideoAgentState

SessionFactory = Callable[[UUID], AbstractAsyncContextManager[AsyncSession]]


def make_flag_degraded_node(
    beat_index: int,
    *,
    session_factory: SessionFactory,
    assemble_deliver: bool,
):
    if beat_index < 1 or beat_index > 4:
        raise ValueError("beat_index must be 1..4")

    async def flag_degraded_node(state: VideoAgentState) -> dict:
        tenant_id = UUID(state["tenant_id"])
        job_id = UUID(state["job_id"])
        async with session_factory(tenant_id) as session:
            await session.execute(
                update(Shot)
                .where(
                    Shot.job_id == job_id,
                    Shot.tenant_id == tenant_id,
                    Shot.beat_index == beat_index,
                )
                .values(degraded=True)
            )
            await session.execute(
                update(Job)
                .where(Job.id == job_id, Job.tenant_id == tenant_id)
                .values(degraded=True)
            )
            await session.commit()

        delta: dict = {
            "qc_passed": False,
            "job_degraded": True,
            "current_beat_index": beat_index,
        }
        if beat_index == 4 and not assemble_deliver:
            delta["outcome"] = "PARTIAL"
            delta["shots_completed"] = True
        return delta

    return flag_degraded_node
