from datetime import datetime, timezone

import pytest
from sqlalchemy import event, func, select

from app.db.models import Job, StoryPlanRow
from app.domain.errors import AppError
from app.domain.schemas import JobStatus
from app.gateway.client import FakeGateway, Usage
from app.nodes.plan_story import plan_story_node
import app.nodes.plan_story as plan_story_module


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


class CountingGateway(FakeGateway):
    def __init__(self, responses, usage=Usage(usd=0.05, tokens=100)):
        super().__init__(responses=responses, usage=usage)
        self.call_count = 0

    async def complete_json(self, alias, messages, schema_name):
        self.call_count += 1
        assert alias == "reasoning-high"
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
async def test_plan_story_persists_idempotently(node_db):
    maker, session_factory, tenant_id, job_id = node_db
    gateway = CountingGateway({"story_plan": VALID_PLAN})
    state = make_state(tenant_id, job_id)

    first = await plan_story_node(
        state, gateway=gateway, session_factory=session_factory
    )
    second = await plan_story_node(
        {**state, **first}, gateway=gateway, session_factory=session_factory
    )

    async with maker() as session:
        count = await session.scalar(select(func.count()).select_from(StoryPlanRow))
        job = await session.get(Job, job_id)
    assert count == 1
    assert gateway.call_count == 1
    assert second["story_plan"] == VALID_PLAN
    assert float(job.budget_used_usd) == pytest.approx(0.05)
    assert job.budget_used_tokens == 100
    assert job.budget_used_iterations == 1


@pytest.mark.asyncio
async def test_plan_story_upsert_handles_conflict_without_duplicate(
    node_db, monkeypatch
):
    maker, session_factory, tenant_id, job_id = node_db
    gateway = CountingGateway({"story_plan": VALID_PLAN})
    state = make_state(tenant_id, job_id)
    monkeypatch.setattr(plan_story_module, "_load_existing", _always_missing)
    statements = []
    event.listen(
        maker.kw["bind"].sync_engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _parameters, _context, _many: (
            statements.append(statement)
        ),
    )

    await plan_story_node(
        state, gateway=gateway, session_factory=session_factory
    )
    await plan_story_node(
        state, gateway=gateway, session_factory=session_factory
    )

    async with maker() as session:
        count = await session.scalar(select(func.count()).select_from(StoryPlanRow))
    assert count == 1
    assert gateway.call_count == 2
    assert any("ON CONFLICT" in statement for statement in statements)


@pytest.mark.asyncio
async def test_plan_story_budget_exhaustion_is_partial_without_gateway_call(node_db):
    _, session_factory, tenant_id, job_id = node_db
    gateway = CountingGateway({"story_plan": VALID_PLAN})

    result = await plan_story_node(
        make_state(tenant_id, job_id, budget_max_usd=0.0),
        gateway=gateway,
        session_factory=session_factory,
    )

    assert result["outcome"] == "PARTIAL"
    assert result["budget_used_usd"] == pytest.approx(0.0)
    assert gateway.call_count == 0


@pytest.mark.asyncio
async def test_plan_story_reloads_persisted_budget_before_gateway_call(node_db):
    maker, session_factory, tenant_id, job_id = node_db
    gateway = CountingGateway({"story_plan": VALID_PLAN})
    async with maker() as session:
        job = await session.get(Job, job_id)
        job.budget_used_usd = 1
        await session.commit()

    result = await plan_story_node(
        make_state(tenant_id, job_id),
        gateway=gateway,
        session_factory=session_factory,
    )

    assert result["outcome"] == "PARTIAL"
    assert result["budget_used_usd"] == pytest.approx(1.0)
    assert gateway.call_count == 0


@pytest.mark.asyncio
async def test_plan_story_stops_schema_retries_when_usage_reaches_cap(node_db):
    maker, session_factory, tenant_id, job_id = node_db
    gateway = CountingGateway(
        {"story_plan": {"nope": True}},
        usage=Usage(usd=0.01, tokens=10),
    )

    result = await plan_story_node(
        make_state(tenant_id, job_id, budget_max_usd=0.01),
        gateway=gateway,
        session_factory=session_factory,
    )

    async with maker() as session:
        job = await session.get(Job, job_id)
    assert result["outcome"] == "PARTIAL"
    assert result["budget_used_usd"] == pytest.approx(0.01)
    assert gateway.call_count == 1
    assert job.status == JobStatus.PARTIAL
    assert float(job.budget_used_usd) == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_invalid_story_schema_retries_then_fails_job(node_db):
    maker, session_factory, tenant_id, job_id = node_db
    gateway = CountingGateway({"story_plan": {"nope": True}})

    with pytest.raises(AppError) as error:
        await plan_story_node(
            make_state(tenant_id, job_id),
            gateway=gateway,
            session_factory=session_factory,
        )

    async with maker() as session:
        job = await session.get(Job, job_id)
    assert error.value.code == "SCHEMA_INVALID"
    assert gateway.call_count == 3
    assert job.status == JobStatus.FAILED
    assert float(job.budget_used_usd) == pytest.approx(0.15)
    assert job.budget_used_tokens == 300
    assert job.budget_used_iterations == 3


async def _always_missing(*_args, **_kwargs):
    return None
