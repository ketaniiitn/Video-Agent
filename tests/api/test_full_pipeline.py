from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.config import Settings
from app.domain.schemas import JobStatus
from app.providers.fake import FakeVideoProvider
from tests.api.conftest import build_test_app


async def _wait_terminal(client, tenant, job_id, *, timeout=80):
    for _ in range(timeout):
        response = await client.get(
            f"/jobs/{job_id}", headers={"X-Tenant-Id": str(tenant)}
        )
        assert response.status_code == 200
        body = response.json()
        if body["status"] not in {JobStatus.QUEUED.value, JobStatus.RUNNING.value}:
            return body
        await __import__("asyncio").sleep(0.05)
    raise AssertionError("job did not reach a terminal status")


def _patch_media(monkeypatch, tmp_path):
    import app.media.ffmpeg as ffmpeg_mod

    async def fake_extract(video_path: Path, output_path: Path, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"JPEG")
        return output_path

    async def fake_stitch(clip_paths, output_path, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"STITCHED")
        return output_path

    monkeypatch.setattr(ffmpeg_mod, "extract_last_frame", fake_extract)
    monkeypatch.setattr(ffmpeg_mod, "stitch_clips", fake_stitch)


@pytest.mark.asyncio
async def test_full_pipeline_reaches_delivered(tmp_path, monkeypatch):
    from app.graph.compile import build_graph

    _patch_media(monkeypatch, tmp_path)
    settings = Settings(
        _env_file=None,
        feature_story_planning=True,
        feature_shot_generation=True,
        feature_qc_repair=True,
        feature_assemble_deliver=True,
        media_root=str(tmp_path),
        app_base_url="http://test",
        presign_secret="test-secret",
    )
    provider = FakeVideoProvider()
    app = await build_test_app(settings=settings, provider=provider)
    try:
        app.state.graph = await build_graph(
            MemorySaver(),
            gateway=app.state.gateway,
            session_factory=app.state.session_factory,
            settings=settings,
            provider=provider,
        )
        response = await app.client.post(
            "/jobs",
            headers={
                "Idempotency-Key": "m3b-full",
                "X-Tenant-Id": str(app.tenant_a),
            },
            json={"prompt": "A courier story"},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        await app.drain_background_tasks()
        body = await _wait_terminal(app.client, app.tenant_a, job_id)
        assert body["status"] == JobStatus.DELIVERED.value
        assert len(body["shots"]) == 4
        assert body["download_url"]
        assert body["degraded"] is False
        assert len(provider.calls) == 4
        assert (tmp_path / str(app.tenant_a) / str(job_id) / "assembled.mp4").exists()
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_qc_exhaustion_delivers_partial(tmp_path, monkeypatch):
    from app.gateway.client import FakeGateway, Usage
    from app.graph.compile import build_graph
    from tests.api.conftest import VALID_BIBLE, VALID_PLAN

    _patch_media(monkeypatch, tmp_path)
    settings = Settings(
        _env_file=None,
        feature_story_planning=True,
        feature_shot_generation=True,
        feature_qc_repair=True,
        feature_assemble_deliver=True,
        media_root=str(tmp_path),
        app_base_url="http://test",
        presign_secret="test-secret",
    )
    gateway = FakeGateway(
        responses={
            "story_plan": VALID_PLAN,
            "continuity_bible": VALID_BIBLE,
            "qc_score": {"score": 0.1, "rationale": "fail"},
        },
        usage=Usage(usd=0.01, tokens=10),
    )
    provider = FakeVideoProvider()
    app = await build_test_app(settings=settings, provider=provider, gateway=gateway)
    try:
        app.state.graph = await build_graph(
            MemorySaver(),
            gateway=gateway,
            session_factory=app.state.session_factory,
            settings=settings,
            provider=provider,
        )
        response = await app.client.post(
            "/jobs",
            headers={
                "Idempotency-Key": "m4-partial",
                "X-Tenant-Id": str(app.tenant_a),
            },
            json={
                "prompt": "A courier story",
                "budget": {
                    "budget_max_usd": 5.0,
                    "budget_max_tokens": 50_000,
                    "budget_max_iterations": 80,
                    "budget_max_wall_clock_seconds": 600,
                },
            },
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        await app.drain_background_tasks()
        body = await _wait_terminal(app.client, app.tenant_a, job_id, timeout=120)
        assert body["status"] == JobStatus.PARTIAL.value
        assert body["degraded"] is True
        assert body["download_url"]
        assert all(shot["degraded"] for shot in body["shots"])
        # generate + 2 repairs per shot
        assert len(provider.calls) == 12
    finally:
        await app.aclose()
