import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db.models import IdempotencyKey, Job
from app.domain.errors import AppError
from app.domain.schemas import BudgetCaps


@dataclass(frozen=True)
class IdempotencyOutcome:
    kind: Literal["replay", "created", "mismatch"]
    job_id: UUID


def request_hash(prompt: str, budget: BudgetCaps | None) -> str:
    body = {
        "budget": budget.model_dump(mode="json") if budget is not None else None,
        "prompt": prompt,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _redis_key(tenant_id: UUID, key: str) -> str:
    return f"idem:{tenant_id}:{key}"


def _raise_mismatch() -> None:
    raise AppError(
        "IDEMPOTENCY_KEY_REUSE_MISMATCH",
        "Idempotency key was already used with a different request body.",
        422,
    )


def _cached_outcome(value: bytes | str, digest: str) -> IdempotencyOutcome:
    cached = json.loads(value)
    if cached["request_hash"] != digest:
        _raise_mismatch()
    return IdempotencyOutcome(kind="replay", job_id=UUID(cached["job_id"]))


async def mirror_idempotency_to_redis(
    redis,
    tenant_id: UUID,
    key: str,
    request_hash: str,
    job_id: UUID,
) -> None:
    """Mirror a durably committed idempotency row to Redis."""
    value = json.dumps(
        {"job_id": str(job_id), "request_hash": request_hash},
        sort_keys=True,
        separators=(",", ":"),
    )
    await redis.set(
        _redis_key(tenant_id, key),
        value,
        ex=Settings().idempotency_ttl_seconds,
    )


async def persist_idempotency_key(
    session,
    tenant_id: UUID,
    key: str,
    request_hash: str,
    job_id: UUID,
) -> Literal["created"]:
    """Insert the idempotency row without committing or touching Redis."""
    row = IdempotencyKey(
        tenant_id=tenant_id,
        key=key,
        request_hash=request_hash,
        job_id=job_id,
        expires_at=datetime.now(UTC)
        + timedelta(seconds=Settings().idempotency_ttl_seconds),
    )
    async with session.begin_nested():
        session.add(row)
        await session.flush()

    return "created"


async def _cached_job_still_exists(session, tenant_id: UUID, job_id: UUID) -> bool:
    """Postgres is the source of truth; Redis only mirrors it.

    A cached mapping can outlive the job it points at (e.g. the job row was
    deleted after the mirror was written, or the mirror was written by a
    process that later rolled back). Treat that as a stale cache entry, not
    as a valid replay.
    """
    job = await session.get(Job, job_id)
    return job is not None and job.tenant_id == tenant_id


async def resolve_idempotency(
    session,
    redis,
    tenant_id: UUID,
    key: str,
    request_hash: str,
    job_id: UUID,
    job: Job | None = None,
) -> IdempotencyOutcome:
    """Resolve or persist an idempotency key without writing Redis.

    ``job`` is the not-yet-persisted ``Job`` row for a fresh creation
    attempt. When provided, it is added to ``session`` and flushed *before*
    the idempotency key row (which has a foreign key to it) is flushed, all
    inside one SAVEPOINT — so either both rows land together, or a
    conflicting idempotency key rolls both back together and this falls
    back to the existing row. Callers that only need to resolve/lookup
    (no candidate job to create) may omit it.

    After this returns, the caller must commit the PostgreSQL transaction before
    calling ``mirror_idempotency_to_redis``.
    """
    cached = await redis.get(_redis_key(tenant_id, key))
    if cached is not None:
        outcome = _cached_outcome(cached, request_hash)
        if job is None or await _cached_job_still_exists(session, tenant_id, outcome.job_id):
            return outcome
        # Stale mirror: drop it and fall through to the Postgres-authoritative
        # path below instead of replaying a job that no longer exists.
        await redis.delete(_redis_key(tenant_id, key))

    try:
        if job is not None:
            async with session.begin_nested():
                session.add(job)
                await session.flush()
                await persist_idempotency_key(session, tenant_id, key, request_hash, job_id)
        else:
            await persist_idempotency_key(session, tenant_id, key, request_hash, job_id)
    except IntegrityError:
        existing = await session.scalar(
            select(IdempotencyKey).where(
                IdempotencyKey.tenant_id == tenant_id,
                IdempotencyKey.key == key,
            )
        )
        if existing is None:
            raise
        if existing.request_hash != request_hash:
            _raise_mismatch()
        return IdempotencyOutcome(kind="replay", job_id=existing.job_id)

    return IdempotencyOutcome(kind="created", job_id=job_id)
