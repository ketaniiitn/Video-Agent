from datetime import datetime, timezone

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import select

from app.db.models import ContinuityBibleRow, Job, StoryPlanRow
from app.domain.errors import AppError
from app.domain.schemas import JobStatus
from app.gateway.client import FakeGateway, Usage
from app.graph.compile import build_graph


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


class SchemaCountingGateway(FakeGateway):
    def __init__(self, *, usage=Usage(usd=0.01, tokens=10)):
        super().__init__(
            responses={
                "story_plan": VALID_PLAN,
                "continuity_bible": {"nope": True},
            },
            usage=usage,
        )
        self.calls = {"story_plan": 0, "continuity_bible": 0}

    async def complete_json(self, alias, messages, schema_name):
        assert alias == "reasoning-high"
        self.calls[schema_name] += 1
        return await super().complete_json(alias, messages, schema_name)


def make_state(tenant_id, job_id, **overrides):
    state = {
        "job_id": str(job_id),
        "tenant_id": str(tenant_id),
        "prompt": "A courier crosses a flooded city.",
        "budget_used_usd": 0.0,
        "budget_max_usd": 1.0,
        "budget_used_tokens": 0,
        "budget_max_tokens": 50_000,
        "budget_used_iterations": 0,
        "budget_max_iterations": 20,
        "started_at_iso": datetime.now(timezone.utc).isoformat(),
        "budget_max_wall_clock_seconds": 600,
    }
    state.update(overrides)
    return state


@pytest.mark.asyncio
async def test_resume_continues_after_committed_story_plan(node_db):
    maker, session_factory, tenant_id, job_id = node_db
    gateway = SchemaCountingGateway()
    graph = await build_graph(
        MemorySaver(),
        gateway=gateway,
        session_factory=session_factory,
    )
    config = {
        "configurable": {
            "thread_id": str(job_id),
            "tenant_id": str(tenant_id),
        }
    }

    with pytest.raises(AppError) as first_error:
        await graph.ainvoke(make_state(tenant_id, job_id), config)
    assert first_error.value.code == "SCHEMA_INVALID"

    async with maker() as session:
        plan = await session.scalar(
            select(StoryPlanRow).where(StoryPlanRow.job_id == job_id)
        )
        bible = await session.scalar(
            select(ContinuityBibleRow).where(
                ContinuityBibleRow.job_id == job_id
            )
        )
    assert plan is not None
    assert bible is None

    gateway.responses["continuity_bible"] = VALID_BIBLE
    result = await graph.ainvoke(None, config)

    async with maker() as session:
        job = await session.get(Job, job_id)
        bible = await session.scalar(
            select(ContinuityBibleRow).where(
                ContinuityBibleRow.job_id == job_id
            )
        )
    assert result["outcome"] == "SUCCESS"
    assert gateway.calls["story_plan"] == 1
    assert bible is not None and bible.locked_at is not None
    assert job.status == JobStatus.BIBLE_LOCKED


@pytest.mark.asyncio
async def test_next_node_reloads_usage_and_stops_at_tiny_usd_cap(node_db):
    maker, session_factory, tenant_id, job_id = node_db
    gateway = SchemaCountingGateway()
    graph = await build_graph(
        MemorySaver(),
        gateway=gateway,
        session_factory=session_factory,
    )
    config = {
        "configurable": {
            "thread_id": str(job_id),
            "tenant_id": str(tenant_id),
        }
    }

    result = await graph.ainvoke(
        make_state(tenant_id, job_id, budget_max_usd=0.01),
        config,
    )

    async with maker() as session:
        job = await session.get(Job, job_id)
    assert result["outcome"] == "PARTIAL"
    assert result["budget_used_usd"] == pytest.approx(0.01)
    assert gateway.calls == {"story_plan": 1, "continuity_bible": 0}
    assert job.status == JobStatus.PARTIAL
