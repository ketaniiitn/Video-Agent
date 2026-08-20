import pytest

from app.domain.schemas import JobStatus
from tests.api.conftest import build_test_app


@pytest.mark.asyncio
async def test_duplicate_idempotency_keys_do_not_create_two_jobs():
    app = await build_test_app()
    try:
        headers = {
            "Idempotency-Key": "load-key",
            "X-Tenant-Id": str(app.tenant_a),
        }
        body = {"prompt": "A courier story"}
        responses = []
        for _ in range(8):
            responses.append(await app.client.post("/jobs", headers=headers, json=body))
        assert all(item.status_code == 202 for item in responses)
        job_ids = {item.json()["job_id"] for item in responses}
        assert len(job_ids) == 1
        await app.drain_background_tasks()
        job_id = next(iter(job_ids))
        detail = await app.client.get(
            f"/jobs/{job_id}", headers={"X-Tenant-Id": str(app.tenant_a)}
        )
        assert detail.status_code == 200
        assert detail.json()["status"] in {
            JobStatus.BIBLE_LOCKED.value,
            JobStatus.QUEUED.value,
            JobStatus.RUNNING.value,
        }
    finally:
        await app.aclose()
