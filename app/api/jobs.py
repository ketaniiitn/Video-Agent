"""``POST /jobs``, ``GET /jobs/{id}``, ``POST /jobs/{id}/resume``.

Exact ordering on ``POST /jobs`` (trace id is minted earlier, by the ASGI
middleware in ``app/main.py``, so it is already set before this module runs):

1. Validate ``Idempotency-Key`` + ``X-Tenant-Id`` headers -> 400 if missing.
2. Feature flag ``FEATURE_STORY_PLANNING`` -> 403 ``FEATURE_DISABLED`` (no
   idempotency write, no job row).
3. Resolve idempotency (Redis fast path verified against Postgres, then
   Postgres as source of truth). The candidate ``Job`` row is added and
   flushed *before* the idempotency key row that has a foreign key to it,
   both inside the same transaction/SAVEPOINT, so a conflicting key rolls
   both back together instead of leaving an orphaned job.
4. On a fresh key: commit (job + idempotency key together), mirror to
   Redis, write initial progress, schedule the background run, return 202.
   On replay: return the existing job id + status untouched. If the
   replay target no longer exists in Postgres, fail honestly instead of
   fabricating a status.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import AppState, get_app_state
from app.cache.idempotency import (
    mirror_idempotency_to_redis,
    request_hash,
    resolve_idempotency,
)
from app.cache.locks import release_job_lock, try_acquire_job_lock
from app.cache.progress import write_progress
from app.db.models import ContinuityBibleRow, Job, StoryPlanRow
from app.domain.errors import AppError
from app.domain.schemas import (
    BudgetCaps,
    ContinuityBible,
    CreateJobRequest,
    JobStatus,
    StoryPlan,
)
from app.jobs.runner import (
    TERMINAL_STATUSES,
    run_job,
    run_locked_job,
    schedule_background_task,
)
from app.observability.tracing import resolve_trace_id

router = APIRouter(prefix="/jobs", tags=["jobs"])


class CreateJobResponse(BaseModel):
    job_id: UUID
    status: JobStatus


class JobDetailResponse(BaseModel):
    job_id: UUID
    status: JobStatus
    budget_used_usd: float
    budget_used_tokens: int
    budget_used_iterations: int
    budget_max_usd: float
    budget_max_tokens: int
    budget_max_iterations: int
    budget_max_wall_clock_seconds: int
    story_plan: StoryPlan | None = None
    continuity_bible: ContinuityBible | None = None


class ResumeResponse(BaseModel):
    job_id: UUID
    status: JobStatus


def _require_header(value: str | None, code: str, message: str) -> str:
    if not value:
        raise AppError(code, message, http_status=400)
    return value


def _parse_tenant_id(raw: str) -> UUID:
    try:
        return UUID(raw)
    except ValueError as exc:
        raise AppError(
            "TENANT_ID_INVALID", "X-Tenant-Id must be a UUID", http_status=400
        ) from exc


@router.post("", status_code=202, response_model=CreateJobResponse)
async def create_job(
    body: CreateJobRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    state: AppState = Depends(get_app_state),
) -> CreateJobResponse:
    idempotency_key = _require_header(
        idempotency_key,
        "IDEMPOTENCY_KEY_MISSING",
        "Idempotency-Key header is required",
    )
    x_tenant_id = _require_header(
        x_tenant_id, "TENANT_ID_MISSING", "X-Tenant-Id header is required"
    )
    tenant_id = _parse_tenant_id(x_tenant_id)

    if not state.settings.feature_story_planning:
        raise AppError(
            "FEATURE_DISABLED",
            "Story planning is currently disabled",
            http_status=403,
        )

    digest = request_hash(body.prompt, body.budget)
    candidate_job_id = uuid4()
    budget = body.budget or BudgetCaps()
    candidate_job = Job(
        id=candidate_job_id,
        tenant_id=tenant_id,
        status=JobStatus.QUEUED,
        prompt=body.prompt,
        trace_id=resolve_trace_id(),
        budget_max_usd=budget.budget_max_usd,
        budget_max_tokens=budget.budget_max_tokens,
        budget_max_iterations=budget.budget_max_iterations,
        budget_max_wall_clock_seconds=budget.budget_max_wall_clock_seconds,
    )

    async with state.session_factory(tenant_id) as session:
        outcome = await resolve_idempotency(
            session,
            state.redis,
            tenant_id,
            idempotency_key,
            digest,
            candidate_job_id,
            job=candidate_job,
        )
        if outcome.kind == "replay":
            job = await session.get(Job, outcome.job_id)
            if job is None:
                # Postgres is the source of truth and never fabricates a
                # status for a job that isn't there; the idempotency key
                # row FKs to jobs with ON DELETE CASCADE so this should be
                # unreachable in practice, but fail honestly rather than
                # lie about QUEUED if it ever happens.
                raise AppError(
                    "JOB_NOT_FOUND",
                    "Idempotency key resolved to a job that no longer exists",
                    http_status=404,
                )
            return CreateJobResponse(job_id=outcome.job_id, status=job.status)

        await session.commit()

    await mirror_idempotency_to_redis(
        state.redis, tenant_id, idempotency_key, digest, candidate_job_id
    )
    await write_progress(
        state.redis, candidate_job_id, {"status": JobStatus.QUEUED.value}
    )

    schedule_background_task(
        state,
        run_job(
            job_id=candidate_job_id,
            tenant_id=tenant_id,
            redis=state.redis,
            session_factory=state.session_factory,
            graph=state.graph,
            gateway=state.gateway,
        )
    )
    return CreateJobResponse(job_id=candidate_job_id, status=JobStatus.QUEUED)


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job(
    job_id: UUID,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    state: AppState = Depends(get_app_state),
) -> JobDetailResponse:
    x_tenant_id = _require_header(
        x_tenant_id, "TENANT_ID_MISSING", "X-Tenant-Id header is required"
    )
    tenant_id = _parse_tenant_id(x_tenant_id)

    async with state.session_factory(tenant_id) as session:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)

        plan_row = await session.scalar(
            select(StoryPlanRow).where(
                StoryPlanRow.job_id == job_id, StoryPlanRow.tenant_id == tenant_id
            )
        )
        bible_row = await session.scalar(
            select(ContinuityBibleRow).where(
                ContinuityBibleRow.job_id == job_id,
                ContinuityBibleRow.tenant_id == tenant_id,
            )
        )

    return JobDetailResponse(
        job_id=job.id,
        status=job.status,
        budget_used_usd=float(job.budget_used_usd),
        budget_used_tokens=job.budget_used_tokens,
        budget_used_iterations=job.budget_used_iterations,
        budget_max_usd=float(job.budget_max_usd),
        budget_max_tokens=job.budget_max_tokens,
        budget_max_iterations=job.budget_max_iterations,
        budget_max_wall_clock_seconds=job.budget_max_wall_clock_seconds,
        story_plan=StoryPlan.model_validate(plan_row.beats_json) if plan_row else None,
        continuity_bible=(
            ContinuityBible.model_validate(bible_row.bible_json) if bible_row else None
        ),
    )


@router.post("/{job_id}/resume", status_code=202, response_model=ResumeResponse)
async def resume_job(
    job_id: UUID,
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id"),
    state: AppState = Depends(get_app_state),
) -> ResumeResponse:
    x_tenant_id = _require_header(
        x_tenant_id, "TENANT_ID_MISSING", "X-Tenant-Id header is required"
    )
    tenant_id = _parse_tenant_id(x_tenant_id)

    async with state.session_factory(tenant_id) as session:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)
        if job.status in TERMINAL_STATUSES:
            raise AppError(
                "JOB_ALREADY_TERMINAL",
                f"Job is already in terminal status {job.status.value}",
                http_status=409,
            )
        current_status = job.status

    # Acquire the lock synchronously (not via a background task) so a
    # concurrent resume call gets a deterministic 409 in this same
    # request/response cycle, rather than racing against task scheduling.
    lock_token = await try_acquire_job_lock(state.redis, str(job_id))
    if lock_token is None:
        raise AppError("JOB_LOCKED", "job is already running", http_status=409)

    try:
        async with state.session_factory(tenant_id) as session:
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise AppError("JOB_NOT_FOUND", "Job was not found", http_status=404)
            if job.status in TERMINAL_STATUSES:
                raise AppError(
                    "JOB_ALREADY_TERMINAL",
                    f"Job is already in terminal status {job.status.value}",
                    http_status=409,
                )
            current_status = job.status
    except Exception:
        await release_job_lock(state.redis, str(job_id), lock_token)
        raise

    async def _resume_and_release() -> None:
        try:
            await run_locked_job(
                job_id=job_id,
                tenant_id=tenant_id,
                redis=state.redis,
                session_factory=state.session_factory,
                graph=state.graph,
            )
        finally:
            await release_job_lock(state.redis, str(job_id), lock_token)

    schedule_background_task(state, _resume_and_release())
    return ResumeResponse(job_id=job_id, status=current_status)
