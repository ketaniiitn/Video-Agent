"""In-process job execution: lock, invoke the graph, map outcome to status.

Two invocation shapes exist because one test requirement needs a
synchronous lock decision and the others don't:

- ``run_job``: acquires ``lock:{job_id}``, runs, releases. Used for the
  ``POST /jobs`` background task and the startup sweep, where nothing is
  waiting on the lock result — a contention failure there just means
  another worker already owns the job, which is fine to drop.
- ``run_locked_job``: assumes the caller already holds the lock. Used by
  ``POST /jobs/{id}/resume``, which awaits ``try_acquire_job_lock`` itself
  *before* responding, so a concurrent resume gets a deterministic
  ``409 JOB_LOCKED`` in the same request/response cycle instead of a race
  against when a background task happens to start.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select

from app.api.deps import SessionFactory
from app.cache.locks import release_job_lock, try_acquire_job_lock
from app.cache.progress import clear_progress
from app.db.models import Job
from app.domain.errors import AppError
from app.domain.schemas import JobStatus
from app.gateway.protocols import GatewayClient

if TYPE_CHECKING:
    from app.api.deps import AppState

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = frozenset(
    {
        JobStatus.BIBLE_LOCKED,
        JobStatus.SHOTS_READY,
        JobStatus.PARTIAL,
        JobStatus.FAILED,
        JobStatus.FAILED_NO_PROGRESS,
        JobStatus.ESCALATED,
    }
)

_STALE_STATUSES = (JobStatus.RUNNING, JobStatus.QUEUED)

_OUTCOME_TO_STATUS: dict[str, JobStatus] = {
    "SUCCESS": JobStatus.BIBLE_LOCKED,
    "PARTIAL": JobStatus.PARTIAL,
    "FAILED": JobStatus.FAILED,
    "FAILED_NO_PROGRESS": JobStatus.FAILED_NO_PROGRESS,
    "ESCALATED": JobStatus.ESCALATED,
}


async def run_job(
    *,
    job_id: UUID,
    tenant_id: UUID,
    redis: Any,
    session_factory: SessionFactory,
    graph: Any,
    gateway: GatewayClient,
) -> None:
    del gateway  # already bound into node closures by build_graph; kept for interface parity
    lock_token = await try_acquire_job_lock(redis, str(job_id))
    if lock_token is None:
        raise AppError("JOB_LOCKED", "job is already running", http_status=409)
    try:
        await run_locked_job(
            job_id=job_id,
            tenant_id=tenant_id,
            redis=redis,
            session_factory=session_factory,
            graph=graph,
        )
    finally:
        await release_job_lock(redis, str(job_id), lock_token)


async def run_locked_job(
    *,
    job_id: UUID,
    tenant_id: UUID,
    redis: Any,
    session_factory: SessionFactory,
    graph: Any,
) -> None:
    """Run the graph to completion. Caller must already hold ``lock:{job_id}``."""
    config = {
        "configurable": {"thread_id": str(job_id), "tenant_id": str(tenant_id)}
    }
    has_checkpoint = await _has_checkpoint(graph, config)
    initial_state = await _mark_running(
        session_factory, tenant_id, job_id, needs_initial_state=not has_checkpoint
    )
    try:
        result = await graph.ainvoke(initial_state, config)
    except AppError:
        # Nodes already persist JobStatus.FAILED before raising SCHEMA_INVALID;
        # this is a defensive backstop so any other AppError escaping the
        # graph still leaves the job terminal instead of stuck at RUNNING.
        await _ensure_terminal(session_factory, tenant_id, job_id, JobStatus.FAILED)
        await clear_progress(redis, job_id)
        raise

    status = _OUTCOME_TO_STATUS.get(result.get("outcome"))
    if result.get("shots_completed") and result.get("outcome") == "SUCCESS":
        status = JobStatus.SHOTS_READY
    if status is not None:
        await _set_status(session_factory, tenant_id, job_id, status)
    if status in TERMINAL_STATUSES:
        await clear_progress(redis, job_id)


async def sweep_stale_jobs(state: "AppState") -> list[asyncio.Task]:
    """Find non-terminal jobs (crashed mid-run) and reschedule each.

    Reads through ``state.sweep_session_factory`` — a privileged,
    cross-tenant session used only to discover ``(id, tenant_id)`` pairs.
    Every subsequent read/write for a given job goes through the normal
    tenant-scoped ``state.session_factory`` inside ``run_job``.
    """
    async with state.sweep_session_factory() as session:
        rows = (
            await session.execute(
                select(Job.id, Job.tenant_id).where(Job.status.in_(_STALE_STATUSES))
            )
        ).all()

    tasks: list[asyncio.Task] = []
    for job_id, tenant_id in rows:
        tasks.append(
            schedule_background_task(
                state,
                run_job(
                    job_id=job_id,
                    tenant_id=tenant_id,
                    redis=state.redis,
                    session_factory=state.session_factory,
                    graph=state.graph,
                    gateway=state.gateway,
                )
            )
        )
    return tasks


def schedule_background_task(state: "AppState", coro: Any) -> asyncio.Task:
    """Create and track a task until it finishes."""
    task = asyncio.create_task(coro)
    state.background_tasks.append(task)

    def _on_done(done_task: asyncio.Task) -> None:
        _log_task_exception(done_task)
        try:
            state.background_tasks.remove(done_task)
        except ValueError:
            pass

    task.add_done_callback(_on_done)
    return task


async def drain_background_tasks(state: "AppState") -> None:
    """Wait for all tracked work, including tasks added while draining."""
    while state.background_tasks:
        await asyncio.gather(*tuple(state.background_tasks), return_exceptions=True)


def _log_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("background job task failed", exc_info=exc)


async def _has_checkpoint(graph: Any, config: dict) -> bool:
    """Whether LangGraph already has a checkpoint for this thread_id.

    This — not ``Job.started_at`` — is the correct proxy for "does this run
    need an initial state". ``started_at`` is set the moment a run starts,
    but a crash before the graph's first node commits its checkpoint leaves
    ``started_at`` populated with zero checkpoints on disk. Passing
    ``initial_state=None`` (LangGraph's "resume" shape) in that situation
    invokes the graph with no input and no prior state, which raises
    ``EmptyInputError`` instead of ever running the job.
    """
    checkpointer = getattr(graph, "checkpointer", None)
    if checkpointer is None:
        return False
    checkpoint_tuple = await checkpointer.aget_tuple(config)
    return checkpoint_tuple is not None


async def _mark_running(
    session_factory: SessionFactory,
    tenant_id: UUID,
    job_id: UUID,
    *,
    needs_initial_state: bool,
) -> dict | None:
    """Set RUNNING + ``started_at`` (once). Returns the graph's initial state
    when ``needs_initial_state`` (no checkpoint exists yet), or ``None`` to
    resume from an existing checkpoint."""
    async with session_factory(tenant_id) as session:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)
        if job.status in TERMINAL_STATUSES:
            raise AppError(
                "JOB_ALREADY_TERMINAL",
                f"Job is already in terminal status {job.status.value}",
                http_status=409,
            )

        if job.started_at is None:
            job.started_at = datetime.now(UTC)
        job.status = JobStatus.RUNNING

        initial_state = None
        if needs_initial_state:
            initial_state = {
                "job_id": str(job.id),
                "tenant_id": str(job.tenant_id),
                "prompt": job.prompt,
                "budget_used_usd": float(job.budget_used_usd),
                "budget_used_tokens": job.budget_used_tokens,
                "budget_used_iterations": job.budget_used_iterations,
                "budget_max_usd": float(job.budget_max_usd),
                "budget_max_tokens": job.budget_max_tokens,
                "budget_max_iterations": job.budget_max_iterations,
                "budget_max_wall_clock_seconds": job.budget_max_wall_clock_seconds,
                "started_at_iso": job.started_at.isoformat(),
            }
        await session.commit()
        return initial_state


async def _set_status(
    session_factory: SessionFactory,
    tenant_id: UUID,
    job_id: UUID,
    status: JobStatus,
) -> None:
    async with session_factory(tenant_id) as session:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)
        job.status = status
        await session.commit()


async def _ensure_terminal(
    session_factory: SessionFactory,
    tenant_id: UUID,
    job_id: UUID,
    status: JobStatus,
) -> None:
    async with session_factory(tenant_id) as session:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            return
        if job.status not in TERMINAL_STATUSES:
            job.status = status
            await session.commit()
