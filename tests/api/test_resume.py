import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.db.models import Job
from app.domain.errors import AppError
from app.domain.schemas import JobStatus
from app.gateway.client import FakeGateway, Usage
from app.jobs.runner import sweep_stale_jobs
from tests.api.conftest import VALID_BIBLE, VALID_PLAN, build_test_app


async def _insert_job(
    app,
    tenant_id,
    *,
    status: JobStatus,
    started_at: datetime | None = None,
    budget_max_usd: float = 1.0,
    prompt: str = "A courier crosses a flooded city.",
) -> UUID:
    job_id = uuid4()
    async with app.state.session_factory(tenant_id) as session:
        session.add(
            Job(
                id=job_id,
                tenant_id=tenant_id,
                status=status,
                prompt=prompt,
                trace_id="tr_test",
                budget_max_usd=Decimal(str(budget_max_usd)),
                budget_max_tokens=50_000,
                budget_max_iterations=20,
                budget_max_wall_clock_seconds=600,
                started_at=started_at,
            )
        )
        await session.commit()
    return job_id


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
async def test_resume_on_terminal_job_returns_409_job_already_terminal():
    app = await build_test_app()
    try:
        job_id = await _insert_job(app, app.tenant_a, status=JobStatus.BIBLE_LOCKED)

        response = await app.client.post(
            f"/jobs/{job_id}/resume", headers={"X-Tenant-Id": str(app.tenant_a)}
        )
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "JOB_ALREADY_TERMINAL"
        assert body["trace_id"].startswith("tr_")
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_concurrent_resume_second_call_returns_409_job_locked():
    app = await build_test_app()
    try:
        job_id = await _insert_job(app, app.tenant_a, status=JobStatus.QUEUED)
        headers = {"X-Tenant-Id": str(app.tenant_a)}

        responses = await asyncio.gather(
            app.client.post(f"/jobs/{job_id}/resume", headers=headers),
            app.client.post(f"/jobs/{job_id}/resume", headers=headers),
        )

        status_codes = sorted(r.status_code for r in responses)
        assert status_codes == [202, 409]
        locked_response = next(r for r in responses if r.status_code == 409)
        assert locked_response.json()["code"] == "JOB_LOCKED"

        # Drain the winning background task so it doesn't outlive the test.
        await _poll_until(
            app,
            job_id,
            app.tenant_a,
            statuses={
                JobStatus.BIBLE_LOCKED.value,
                JobStatus.PARTIAL.value,
                JobStatus.FAILED.value,
            },
        )
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_sweep_resumes_job_with_started_at_set_but_no_checkpoint():
    """Regression for using ``started_at is None`` as the "needs initial
    state" proxy.

    A job can have ``started_at`` populated (the RUNNING transition was
    persisted) yet crash before the graph's first node ever wrote a
    checkpoint for its ``thread_id`` — e.g. the process died between
    ``_mark_running``'s commit and ``graph.ainvoke`` completing its first
    step. With the old ``started_at is None`` check this looks like "not
    the first run" and resumes with ``initial_state=None`` against a
    thread_id that has zero checkpoints, which raises LangGraph's
    ``EmptyInputError`` and the job never runs. Detecting "no checkpoint
    exists" directly must still drive it to completion.
    """
    app = await build_test_app()
    try:
        started_at = datetime.now(UTC)
        job_id = await _insert_job(
            app, app.tenant_a, status=JobStatus.RUNNING, started_at=started_at
        )
        # No graph.ainvoke has ever executed for this thread_id, so the
        # MemorySaver checkpointer genuinely has no checkpoint for it —
        # unlike test_startup_sweep_resumes_mid_graph_job_to_bible_locked,
        # which seeds a real mid-graph checkpoint first.

        await sweep_stale_jobs(app.state)

        body = await _poll_until(
            app, job_id, app.tenant_a, statuses={JobStatus.BIBLE_LOCKED.value}
        )
        assert body["story_plan"] is not None
        assert body["continuity_bible"] is not None
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_resume_endpoint_with_started_at_set_but_no_checkpoint():
    """Same regression as above, through ``POST /jobs/{id}/resume`` instead
    of the startup sweep, since it shares ``run_locked_job``."""
    app = await build_test_app()
    try:
        started_at = datetime.now(UTC)
        job_id = await _insert_job(
            app, app.tenant_a, status=JobStatus.QUEUED, started_at=started_at
        )

        response = await app.client.post(
            f"/jobs/{job_id}/resume", headers={"X-Tenant-Id": str(app.tenant_a)}
        )
        assert response.status_code == 202

        body = await _poll_until(
            app, job_id, app.tenant_a, statuses={JobStatus.BIBLE_LOCKED.value}
        )
        assert body["continuity_bible"] is not None
    finally:
        await app.aclose()


@pytest.mark.asyncio
async def test_startup_sweep_resumes_mid_graph_job_to_bible_locked():
    """Seeds a job stuck at RUNNING with a checkpoint after plan_story but
    before lock_continuity_bible, then proves the sweep picks it up and
    drives it to BIBLE_LOCKED using only the checkpoint (thread_id), not a
    fresh initial state.

    To get a real mid-graph checkpoint without hand-rolling LangGraph's
    internal format, this runs the graph directly once with a temporarily
    schema-invalid continuity_bible response (mirrors
    tests/graph/test_compile_resume.py's technique) — that call raises
    SCHEMA_INVALID and the node persists JobStatus.FAILED as a side effect.
    The job status is then reset to RUNNING to model the case the sweep
    exists for: a crash that leaves a job non-terminal, not a clean
    exception that already reached a terminal status on its own.
    """
    gateway = FakeGateway(
        responses={"story_plan": VALID_PLAN, "continuity_bible": {"nope": True}},
        usage=Usage(usd=0.01, tokens=10),
    )
    app = await build_test_app(gateway=gateway)
    try:
        started_at = datetime.now(UTC)
        job_id = await _insert_job(
            app, app.tenant_a, status=JobStatus.RUNNING, started_at=started_at
        )

        initial_state = {
            "job_id": str(job_id),
            "tenant_id": str(app.tenant_a),
            "prompt": "A courier crosses a flooded city.",
            "budget_used_usd": 0.0,
            "budget_used_tokens": 0,
            "budget_used_iterations": 0,
            "budget_max_usd": 1.0,
            "budget_max_tokens": 50_000,
            "budget_max_iterations": 20,
            "budget_max_wall_clock_seconds": 600,
            "started_at_iso": started_at.isoformat(),
        }
        config = {
            "configurable": {
                "thread_id": str(job_id),
                "tenant_id": str(app.tenant_a),
            }
        }
        with pytest.raises(AppError) as excinfo:
            await app.state.graph.ainvoke(initial_state, config)
        assert excinfo.value.code == "SCHEMA_INVALID"

        async with app.state.session_factory(app.tenant_a) as session:
            job = await session.get(Job, job_id)
            assert job.status == JobStatus.FAILED  # node's own error handling
            job.status = JobStatus.RUNNING
            await session.commit()

        gateway.responses["continuity_bible"] = VALID_BIBLE

        await sweep_stale_jobs(app.state)

        body = await _poll_until(
            app, job_id, app.tenant_a, statuses={JobStatus.BIBLE_LOCKED.value}
        )
        assert body["continuity_bible"] is not None
    finally:
        await app.aclose()
