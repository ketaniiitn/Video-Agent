from datetime import datetime, timezone

import pytest
from sqlalchemy import event, func, select

import app.nodes.lock_continuity_bible as lock_bible_module
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


class CountingGateway(FakeGateway):
    def __init__(self, responses, usage=Usage(usd=0.03, tokens=80)):
        super().__init__(responses=responses, usage=usage)
        self.call_count = 0

    async def complete_json(self, alias, messages, schema_name):
        self.call_count += 1
        return await super().complete_json(alias, messages, schema_name)


def make_state(tenant_id, job_id, **overrides):
    state = {
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
    state.update(overrides)
    return state


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


@pytest.mark.asyncio
async def test_lock_bible_commits_artifact_and_usage_once(node_db):
    maker, session_factory, tenant_id, job_id = node_db
    gateway = CountingGateway({"continuity_bible": VALID_BIBLE})
    commits = []
    event.listen(
        maker.kw["bind"].sync_engine,
        "commit",
        lambda _connection: commits.append(None),
    )

    await lock_continuity_bible_node(
        make_state(tenant_id, job_id),
        gateway=gateway,
        session_factory=session_factory,
    )

    async with maker() as session:
        bible = await session.scalar(
            select(ContinuityBibleRow).where(ContinuityBibleRow.job_id == job_id)
        )
        job = await session.get(Job, job_id)
    assert len(commits) == 1
    assert bible is not None
    assert float(job.budget_used_usd) == pytest.approx(0.03)
    assert job.budget_used_tokens == 80
    assert job.budget_used_iterations == 1


@pytest.mark.asyncio
async def test_lock_bible_upsert_handles_conflict_without_duplicate(
    node_db, monkeypatch
):
    maker, session_factory, tenant_id, job_id = node_db
    gateway = CountingGateway({"continuity_bible": VALID_BIBLE})
    state = make_state(tenant_id, job_id)
    monkeypatch.setattr(lock_bible_module, "_load_existing", _always_missing)
    statements = []
    event.listen(
        maker.kw["bind"].sync_engine,
        "before_cursor_execute",
        lambda _conn, _cursor, statement, _parameters, _context, _many: (
            statements.append(statement)
        ),
    )

    await lock_continuity_bible_node(
        state, gateway=gateway, session_factory=session_factory
    )
    await lock_continuity_bible_node(
        state, gateway=gateway, session_factory=session_factory
    )

    async with maker() as session:
        count = await session.scalar(
            select(func.count()).select_from(ContinuityBibleRow)
        )
    assert count == 1
    assert gateway.call_count == 2
    assert any("ON CONFLICT" in statement for statement in statements)


@pytest.mark.asyncio
async def test_lock_bible_stops_schema_retries_when_usage_reaches_cap(node_db):
    maker, session_factory, tenant_id, job_id = node_db
    gateway = CountingGateway(
        {"continuity_bible": {"nope": True}},
        usage=Usage(usd=0.01, tokens=10),
    )

    result = await lock_continuity_bible_node(
        make_state(
            tenant_id,
            job_id,
            budget_used_usd=0.0,
            budget_used_tokens=0,
            budget_used_iterations=0,
            budget_max_usd=0.01,
        ),
        gateway=gateway,
        session_factory=session_factory,
    )

    async with maker() as session:
        job = await session.get(Job, job_id)
    assert result["outcome"] == "PARTIAL"
    assert result["budget_used_usd"] == pytest.approx(0.01)
    assert gateway.call_count == 1
    assert job.status == JobStatus.PARTIAL


async def _always_missing(*_args, **_kwargs):
    return None
