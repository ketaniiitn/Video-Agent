from datetime import datetime, timezone

import pytest

from app.db.models import ContinuityBibleRow, Job
from app.domain.schemas import JobStatus
from app.gateway.client import FakeGateway, Usage
from app.nodes.lock_continuity_bible import lock_continuity_bible_node


VALID_PLAN = {
    "beats": [
        {
            "name": name,
            "duration_seconds": 10,
            "action": f"{name} action",
            "camera": "35mm tracking shot",
        }
        for name in ("setup", "development", "turn", "resolution")
    ]
}
VALID_BIBLE = {
    "character": "Mara, a bicycle courier",
    "wardrobe": "yellow rain jacket",
    "location": "flooded city streets",
    "lighting": "blue-hour ambient light",
    "palette": "teal and amber",
    "lens": "35mm",
}


def make_state(tenant_id, job_id):
    return {
        "job_id": str(job_id),
        "tenant_id": str(tenant_id),
        "prompt": "A courier crosses a flooded city.",
        "story_plan": VALID_PLAN,
        "budget_used_usd": 0.05,
        "budget_max_usd": 1.0,
        "budget_used_tokens": 100,
        "budget_max_tokens": 50_000,
        "budget_used_iterations": 1,
        "budget_max_iterations": 20,
        "started_at_iso": datetime.now(timezone.utc).isoformat(),
        "budget_max_wall_clock_seconds": 600,
    }


@pytest.mark.asyncio
async def test_lock_sets_locked_at_and_job_status(node_db):
    maker, session_factory, tenant_id, job_id = node_db
    gateway = FakeGateway(
        {"continuity_bible": VALID_BIBLE},
        usage=Usage(usd=0.03, tokens=80),
    )

    result = await lock_continuity_bible_node(
        make_state(tenant_id, job_id),
        gateway=gateway,
        session_factory=session_factory,
    )

    async with maker() as session:
        bible = await session.get(ContinuityBibleRow, job_id)
        if bible is None:
            from sqlalchemy import select

            bible = await session.scalar(
                select(ContinuityBibleRow).where(
                    ContinuityBibleRow.job_id == job_id
                )
            )
        job = await session.get(Job, job_id)
    assert bible is not None
    assert bible.locked_at is not None
    assert job.status == JobStatus.BIBLE_LOCKED
    assert result["continuity_bible"] == VALID_BIBLE
    assert result["outcome"] == "SUCCESS"
