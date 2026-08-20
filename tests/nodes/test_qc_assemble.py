from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import Settings
from app.db.models import Job, QcScore, Shot
from app.domain.schemas import JobStatus
from app.gateway.client import FakeGateway, Usage
from app.nodes.assemble import make_assemble_node
from app.nodes.deliver import make_deliver_node
from app.nodes.flag_degraded import make_flag_degraded_node
from app.nodes.generate_shot import make_generate_shot_node
from app.nodes.qc_score import make_qc_score_node
from app.nodes.repair_shot import make_repair_shot_node
from app.providers.fake import FakeVideoProvider
from tests.nodes.test_generate_and_chain import VALID_BIBLE, VALID_PLAN, make_state


@pytest.mark.asyncio
async def test_qc_pass_sets_qc_passed(node_db, tmp_path):
    maker, session_factory, tenant_id, job_id = node_db
    provider = FakeVideoProvider()
    gen = make_generate_shot_node(
        1, provider=provider, session_factory=session_factory, media_root=str(tmp_path)
    )
    state = make_state(tenant_id, job_id)
    state.update(await gen(state))
    gateway = FakeGateway(
        responses={"qc_score": {"score": 0.9, "rationale": "ok"}},
        usage=Usage(usd=0.01, tokens=5),
    )
    qc = make_qc_score_node(
        1,
        gateway=gateway,
        session_factory=session_factory,
        media_root=str(tmp_path),
        assemble_deliver=False,
    )
    result = await qc(state)
    assert result["qc_passed"] is True
    async with maker() as session:
        scores = (await session.execute(select(QcScore))).scalars().all()
        shot = await session.scalar(select(Shot).where(Shot.beat_index == 1))
    assert len(scores) == 1
    assert shot.qc_score is not None


@pytest.mark.asyncio
async def test_qc_fail_then_two_repairs_then_flag(node_db, tmp_path):
    maker, session_factory, tenant_id, job_id = node_db
    provider = FakeVideoProvider()
    gen = make_generate_shot_node(
        1, provider=provider, session_factory=session_factory, media_root=str(tmp_path)
    )
    state = make_state(tenant_id, job_id)
    state.update(await gen(state))
    gateway = FakeGateway(
        responses={"qc_score": {"score": 0.2, "rationale": "drift"}},
        usage=Usage(usd=0.01, tokens=5),
    )
    qc = make_qc_score_node(
        1,
        gateway=gateway,
        session_factory=session_factory,
        media_root=str(tmp_path),
        assemble_deliver=True,
    )
    repair = make_repair_shot_node(
        1, provider=provider, session_factory=session_factory, media_root=str(tmp_path)
    )
    flag = make_flag_degraded_node(
        1, session_factory=session_factory, assemble_deliver=True
    )

    first = await qc(state)
    assert first["qc_passed"] is False
    assert first["repair_count"] == 0
    state.update(first)
    state.update(await repair(state))
    state.update(await qc(state))
    assert state["repair_count"] == 1
    state.update(await repair(state))
    state.update(await qc(state))
    assert state["repair_count"] == 2
    flagged = await flag(state)
    assert flagged["job_degraded"] is True
    async with maker() as session:
        shot = await session.scalar(select(Shot).where(Shot.beat_index == 1))
        job = await session.get(Job, job_id)
    assert shot.degraded is True
    assert job.degraded is True
    assert len(provider.calls) == 3


@pytest.mark.asyncio
async def test_assemble_and_deliver_write_presigned_url(node_db, tmp_path):
    _, session_factory, tenant_id, job_id = node_db
    provider = FakeVideoProvider()
    gen = make_generate_shot_node(
        1, provider=provider, session_factory=session_factory, media_root=str(tmp_path)
    )
    state = make_state(tenant_id, job_id)
    state.update(await gen(state))

    async def fake_stitch(clips, output, **_kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"STITCHED")
        return output

    assemble = make_assemble_node(
        session_factory=session_factory, media_root=str(tmp_path), stitch=fake_stitch
    )
    assembled = await assemble(state)
    assert Path(assembled["assembled_path"]).read_bytes() == b"STITCHED"
    assert assembled["outcome"] == "PARTIAL"

    settings = Settings(_env_file=None, app_base_url="http://test", presign_secret="s")
    deliver = make_deliver_node(
        session_factory=session_factory, settings=settings, media_root=str(tmp_path)
    )
    delivered = await deliver({**state, **assembled, "job_degraded": True})
    assert delivered["delivered"] is True
    assert delivered["outcome"] == "PARTIAL"
    assert "assembled.mp4" in delivered["download_url"]
    assert "sig=" in delivered["download_url"]
