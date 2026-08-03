import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.db.models import IdempotencyKey, Job
from app.domain.schemas import JobStatus
from app.gateway.client import FakeGateway, Usage
from tests.api.conftest import VALID_PLAN, build_test_app


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
async def test_idempotency_mismatch_returns_422():
    app = await build_test_app()
    try:
        headers = {"Idempotency-Key": "key-mismatch", "X-Tenant-Id": str(app.tenant_a)}
        first = await app.client.post(
            "/jobs", json={"prompt": "prompt A"}, headers=headers
        )
        assert first.status_code == 202

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

        body = await _poll_until(
            app, job_id, app.tenant_a, statuses={JobStatus.PARTIAL.value}
        )
        assert body["story_plan"] is not None
        assert body["continuity_bible"] is None
        assert body["budget_used_usd"] == pytest.approx(0.01)
    finally:
        await app.aclose()
