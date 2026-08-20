import pytest

from app.domain.schemas import JobStatus
from app.providers.fake import FakeVideoProvider
from tests.api.conftest import build_test_app


async def _wait_terminal(client, tenant, job_id, *, timeout=50):
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


@pytest.mark.asyncio
async def test_flag_off_still_ends_bible_locked():
    app = await build_test_app(feature_shot_generation=False)
    try:
        response = await app.client.post(
            "/jobs",
            headers={
                "Idempotency-Key": "m3a-off",
                "X-Tenant-Id": str(app.tenant_a),
            },
            json={"prompt": "A courier story"},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        await app.drain_background_tasks()
        body = await _wait_terminal(app.client, app.tenant_a, job_id)
        assert body["status"] == JobStatus.BIBLE_LOCKED.value
        assert body["shots"] == []
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_flag_on_reaches_shots_ready_with_four_shots(tmp_path, monkeypatch):
    from pathlib import Path

    from app.config import Settings
    from langgraph.checkpoint.memory import MemorySaver
    from app.graph.compile import build_graph
    import app.media.ffmpeg as ffmpeg_mod

    async def fake_extract(video_path: Path, output_path: Path, **_kwargs):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"JPEG")
        return output_path

    monkeypatch.setattr(ffmpeg_mod, "extract_last_frame", fake_extract)

    settings = Settings(
        _env_file=None,
        feature_story_planning=True,
        feature_shot_generation=True,
        media_root=str(tmp_path),
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
                "Idempotency-Key": "m3a-on",
                "X-Tenant-Id": str(app.tenant_a),
            },
            json={"prompt": "A courier story"},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        await app.drain_background_tasks()
        body = await _wait_terminal(app.client, app.tenant_a, job_id)
        assert body["status"] == JobStatus.SHOTS_READY.value
        assert len(body["shots"]) == 4
        assert len(provider.calls) == 4
        assert provider.calls[0].prior_frame_path is None
        assert provider.calls[1].prior_frame_path is not None
    finally:
        await app.aclose()
