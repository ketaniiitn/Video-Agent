from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.models import CostLedger, Shot
from app.domain.schemas import ShotStatus
from app.nodes.chain_frame import make_chain_frame_node
from app.nodes.generate_shot import make_generate_shot_node
from app.providers.fake import FakeVideoProvider

VALID_PLAN = {
    "beats": [
        {
            "name": name,
            "duration_seconds": 10,
            "action": f"{name} action",
            "camera": "35mm",
        }
        for name in ("setup", "development", "turn", "resolution")
    ]
}
VALID_BIBLE = {
    "character": "Mara",
    "wardrobe": "yellow jacket",
    "location": "streets",
    "lighting": "blue hour",
    "palette": "teal",
    "lens": "35mm",
}


def make_state(tenant_id, job_id, **overrides):
    state = {
        "job_id": str(job_id),
        "tenant_id": str(tenant_id),
        "prompt": "story",
        "story_plan": VALID_PLAN,
        "continuity_bible": VALID_BIBLE,
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
async def test_generate_shot_persists_clip_and_ledger(node_db, tmp_path):
    maker, session_factory, tenant_id, job_id = node_db
    provider = FakeVideoProvider()
    node = make_generate_shot_node(
        1,
        provider=provider,
        session_factory=session_factory,
        media_root=str(tmp_path),
    )
    result = await node(make_state(tenant_id, job_id))
    assert Path(result["current_clip_path"]).exists()
    assert len(provider.calls) == 1
    assert provider.calls[0].prior_frame_path is None

    async with maker() as session:
        shot = await session.scalar(
            select(Shot).where(Shot.job_id == job_id, Shot.beat_index == 1)
        )
        ledger = (
            await session.execute(select(CostLedger).where(CostLedger.job_id == job_id))
        ).scalars().all()
    assert shot is not None
    assert shot.status == ShotStatus.SUCCEEDED
    assert len(ledger) == 1


@pytest.mark.asyncio
async def test_generate_shot_passes_prior_frame(node_db, tmp_path):
    _, session_factory, tenant_id, job_id = node_db
    provider = FakeVideoProvider()
    node = make_generate_shot_node(
        2,
        provider=provider,
        session_factory=session_factory,
        media_root=str(tmp_path),
    )
    await node(
        make_state(tenant_id, job_id, prior_frame_path=str(tmp_path / "frame_1.jpg"))
    )
    assert provider.calls[0].prior_frame_path == str(tmp_path / "frame_1.jpg")


@pytest.mark.asyncio
async def test_generate_shot_skips_when_already_succeeded(node_db, tmp_path):
    maker, session_factory, tenant_id, job_id = node_db
    provider = FakeVideoProvider()
    node = make_generate_shot_node(
        1,
        provider=provider,
        session_factory=session_factory,
        media_root=str(tmp_path),
    )
    await node(make_state(tenant_id, job_id))
    await node(make_state(tenant_id, job_id))
    assert len(provider.calls) == 1
    async with maker() as session:
        shots = (
            await session.execute(select(Shot).where(Shot.job_id == job_id))
        ).scalars().all()
    assert len(shots) == 1


@pytest.mark.asyncio
async def test_chain_frame_writes_frame_path(node_db, tmp_path):
    maker, session_factory, tenant_id, job_id = node_db
    provider = FakeVideoProvider()
    gen = make_generate_shot_node(
        1,
        provider=provider,
        session_factory=session_factory,
        media_root=str(tmp_path),
    )
    state = make_state(tenant_id, job_id)
    state.update(await gen(state))

    async def fake_extract(video_path: Path, output_path: Path, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"JPEG")
        return output_path

    chain = make_chain_frame_node(
        1,
        session_factory=session_factory,
        media_root=str(tmp_path),
        extract_frame=fake_extract,
    )
    result = await chain(state)
    assert Path(result["prior_frame_path"]).exists()
    assert result.get("shots_completed") is not True

    async with maker() as session:
        shot = await session.scalar(
            select(Shot).where(Shot.job_id == job_id, Shot.beat_index == 1)
        )
    assert shot.frame_path is not None


@pytest.mark.asyncio
async def test_chain_frame_4_marks_shots_completed(node_db, tmp_path):
    maker, session_factory, tenant_id, job_id = node_db
    provider = FakeVideoProvider()
    gen = make_generate_shot_node(
        4,
        provider=provider,
        session_factory=session_factory,
        media_root=str(tmp_path),
    )
    state = make_state(tenant_id, job_id)
    state.update(await gen(state))

    async def fake_extract(video_path: Path, output_path: Path, **_kwargs):
        output_path.write_bytes(b"JPEG")
        return output_path

    chain = make_chain_frame_node(
        4,
        session_factory=session_factory,
        media_root=str(tmp_path),
        extract_frame=fake_extract,
    )
    result = await chain(state)
    assert result["shots_completed"] is True
    assert result["outcome"] == "SUCCESS"
