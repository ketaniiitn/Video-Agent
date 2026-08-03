import asyncio
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text

from app.db.models import IdempotencyKey, Job
from app.domain.schemas import JobStatus
from app.gateway.client import FakeGateway, Usage
from tests.api.conftest import VALID_PLAN, build_test_app


class BlockingGateway(FakeGateway):
    def __init__(self):
        super().__init__(
            responses={"story_plan": VALID_PLAN},
            usage=Usage(usd=0.01, tokens=10),
        )
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def complete_json(self, alias, messages, schema_name):
        self.started.set()
        await self.release.wait()
        return await super().complete_json(alias, messages, schema_name)


async def _poll_until(app, job_id, tenant_id, *, statuses, attempts=50, delay=0.02):
    for _ in range(attempts):
        response = await app.client.get(
            f"/jobs/{job_id}", headers={"X-Tenant-Id": str(tenant_id)}
        )
        assert response.status_code == 200
        body = response.json()
        if body["status"] in statuses:
            return body
        await asyncio.sleep(delay)
    pytest.fail(f"job {job_id} never reached {statuses}; last status={body['status']}")


@pytest.mark.asyncio
async def test_background_jobs_are_tracked_and_drained_on_close():
    gateway = BlockingGateway()
    app = await build_test_app(gateway=gateway)
    response = await app.client.post(
        "/jobs",
        json={"prompt": "A courier crosses a flooded city."},
        headers={
            "Idempotency-Key": "key-background-drain",
            "X-Tenant-Id": str(app.tenant_a),
        },
    )
    assert response.status_code == 202
    await gateway.started.wait()

    assert len(app.state.background_tasks) == 1
    close_task = asyncio.create_task(app.aclose())
    await asyncio.sleep(0)
    assert not close_task.done()

    gateway.release.set()
    await close_task
    assert not app.state.background_tasks


@pytest.mark.asyncio
async def test_happy_path_reaches_bible_locked():
    app = await build_test_app()
    try:
        response = await app.client.post(
            "/jobs",
            json={"prompt": "A courier crosses a flooded city."},
            headers={
                "Idempotency-Key": "key-1",
                "X-Tenant-Id": str(app.tenant_a),
            },
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert response.json()["status"] == JobStatus.QUEUED.value
        await app.drain_background_tasks()

        body = await _poll_until(
            app, job_id, app.tenant_a, statuses={JobStatus.BIBLE_LOCKED.value}
        )
        assert len(body["story_plan"]["beats"]) == 4
        assert body["continuity_bible"]["character"].startswith("Mara")
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_idempotency_replay_returns_same_job_and_single_row():
    app = await build_test_app()
    try:
        headers = {"Idempotency-Key": "key-replay", "X-Tenant-Id": str(app.tenant_a)}
        body = {"prompt": "A courier crosses a flooded city."}

        first = await app.client.post("/jobs", json=body, headers=headers)
        await app.drain_background_tasks()
        second = await app.client.post("/jobs", json=body, headers=headers)

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["job_id"] == second.json()["job_id"]

        async with app.maker() as session:
            job_count = await session.scalar(select(func.count()).select_from(Job))
            key_count = await session.scalar(
                select(func.count()).select_from(IdempotencyKey)
            )
        assert job_count == 1
        assert key_count == 1
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_sqlite_foreign_keys_are_enforced_in_test_fixture():
    """Guardrail for the FK-order regression tests below: if the test
    engine ever stops enforcing FK constraints, a passing ``POST /jobs``
    would no longer prove anything about insert order."""
    app = await build_test_app()
    try:
        async with app.maker() as session:
            enabled = await session.scalar(text("PRAGMA foreign_keys"))
        assert enabled == 1
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_create_job_succeeds_with_foreign_keys_enforced():
    """Regression for creating the idempotency key row (which FKs to
    ``jobs``) before the job row existed in the same transaction. With
    SQLite ``PRAGMA foreign_keys=ON`` (matching Postgres's always-on FK
    enforcement) that ordering bug raises ``IntegrityError`` on every
    ``POST /jobs``; the job row must be added and flushed first."""
    app = await build_test_app()
    try:
        response = await app.client.post(
            "/jobs",
            json={"prompt": "A courier crosses a flooded city."},
            headers={
                "Idempotency-Key": "key-fk-order",
                "X-Tenant-Id": str(app.tenant_a),
            },
        )
        assert response.status_code == 202
        job_id = UUID(response.json()["job_id"])
        await app.drain_background_tasks()

        async with app.maker() as session:
            job = await session.get(Job, job_id)
            key_row = await session.scalar(
                select(IdempotencyKey).where(IdempotencyKey.job_id == job_id)
            )
        assert job is not None
        assert key_row is not None
        assert key_row.job_id == job_id
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_redis_stale_mirror_after_job_deleted_does_not_fabricate_queued():
    """Regression: Redis is a cache, not the source of truth. If the job a
    mirrored idempotency key points at has been deleted from Postgres
    (which cascades away its idempotency_keys row too), a replay must not
    fabricate a fake QUEUED status for a job that no longer exists — it
    should treat the mirror as stale and fall through to create a fresh
    job.
    """
    app = await build_test_app()
    try:
        headers = {
            "Idempotency-Key": "key-stale-mirror",
            "X-Tenant-Id": str(app.tenant_a),
        }
        body = {"prompt": "A courier crosses a flooded city."}

        first = await app.client.post("/jobs", json=body, headers=headers)
        assert first.status_code == 202
        old_job_id = UUID(first.json()["job_id"])

        cache_key = f"idem:{app.tenant_a}:key-stale-mirror"
        cached_before = await app.state.redis.get(cache_key)
        assert cached_before is not None
        assert json.loads(cached_before)["job_id"] == str(old_job_id)
        await app.drain_background_tasks()

        async with app.maker() as session:
            job = await session.get(Job, old_job_id)
            await session.delete(job)
            await session.commit()
            # ON DELETE CASCADE should have taken the idempotency key with it.
            remaining_keys = await session.scalar(
                select(func.count()).select_from(IdempotencyKey)
            )
        assert remaining_keys == 0

        second = await app.client.post("/jobs", json=body, headers=headers)
        assert second.status_code == 202
        new_job_id = UUID(second.json()["job_id"])
        assert new_job_id != old_job_id
        assert second.json()["status"] == JobStatus.QUEUED.value
        await app.drain_background_tasks()

        cached_after = await app.state.redis.get(cache_key)
        assert cached_after is not None
        assert json.loads(cached_after)["job_id"] == str(new_job_id)

        await _poll_until(
            app,
            new_job_id,
            app.tenant_a,
            statuses={JobStatus.BIBLE_LOCKED.value},
        )
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_idempotency_mismatch_returns_422():
    app = await build_test_app()
    try:
        headers = {"Idempotency-Key": "key-mismatch", "X-Tenant-Id": str(app.tenant_a)}
        first = await app.client.post(
            "/jobs", json={"prompt": "prompt A"}, headers=headers
        )
        assert first.status_code == 202
        await app.drain_background_tasks()

        second = await app.client.post(
            "/jobs", json={"prompt": "prompt B"}, headers=headers
        )
        assert second.status_code == 422
        assert second.json()["code"] == "IDEMPOTENCY_KEY_REUSE_MISMATCH"
        assert second.json()["trace_id"].startswith("tr_")
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_feature_flag_off_returns_403_with_trace_and_creates_no_job():
    app = await build_test_app(feature_story_planning=False)
    try:
        response = await app.client.post(
            "/jobs",
            json={"prompt": "A courier crosses a flooded city."},
            headers={
                "Idempotency-Key": "key-flag-off",
                "X-Tenant-Id": str(app.tenant_a),
            },
        )
        assert response.status_code == 403
        body = response.json()
        assert body["code"] == "FEATURE_DISABLED"
        assert body["trace_id"].startswith("tr_")

        async with app.maker() as session:
            job_count = await session.scalar(select(func.count()).select_from(Job))
            key_count = await session.scalar(
                select(func.count()).select_from(IdempotencyKey)
            )
        assert job_count == 0
        assert key_count == 0
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_missing_idempotency_key_returns_400():
    app = await build_test_app()
    try:
        response = await app.client.post(
            "/jobs",
            json={"prompt": "x"},
            headers={"X-Tenant-Id": str(app.tenant_a)},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "IDEMPOTENCY_KEY_MISSING"
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_missing_tenant_header_returns_400():
    app = await build_test_app()
    try:
        response = await app.client.post(
            "/jobs",
            json={"prompt": "x"},
            headers={"Idempotency-Key": "key-no-tenant"},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "TENANT_ID_MISSING"
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_cross_tenant_get_returns_404():
    app = await build_test_app()
    try:
        create = await app.client.post(
            "/jobs",
            json={"prompt": "A courier crosses a flooded city."},
            headers={
                "Idempotency-Key": "key-cross-tenant",
                "X-Tenant-Id": str(app.tenant_a),
            },
        )
        job_id = create.json()["job_id"]
        await app.drain_background_tasks()

        response = await app.client.get(
            f"/jobs/{job_id}", headers={"X-Tenant-Id": str(app.tenant_b)}
        )
        assert response.status_code == 404
        assert response.json()["code"] == "JOB_NOT_FOUND"

        own_tenant = await app.client.get(
            f"/jobs/{job_id}", headers={"X-Tenant-Id": str(app.tenant_a)}
        )
        assert own_tenant.status_code == 200
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_get_unknown_job_returns_404():
    app = await build_test_app()
    try:
        response = await app.client.get(
            f"/jobs/{uuid4()}", headers={"X-Tenant-Id": str(app.tenant_a)}
        )
        assert response.status_code == 404
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_budget_cap_stops_before_second_node_and_reports_partial():
    gateway = FakeGateway(
        responses={"story_plan": VALID_PLAN},
        usage=Usage(usd=0.01, tokens=10),
    )
    app = await build_test_app(gateway=gateway)
    try:
        response = await app.client.post(
            "/jobs",
            json={
                "prompt": "A courier crosses a flooded city.",
                "budget": {"budget_max_usd": 0.01},
            },
            headers={
                "Idempotency-Key": "key-budget",
                "X-Tenant-Id": str(app.tenant_a),
            },
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        await app.drain_background_tasks()

        body = await _poll_until(
            app, job_id, app.tenant_a, statuses={JobStatus.PARTIAL.value}
        )
        assert body["story_plan"] is not None
        assert body["continuity_bible"] is None
        assert body["budget_used_usd"] == pytest.approx(0.01)
    finally:
        await app.aclose()
