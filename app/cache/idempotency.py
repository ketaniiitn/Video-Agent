import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.db.models import IdempotencyKey
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


async def _write_cache(redis, tenant_id: UUID, key: str, row: IdempotencyKey) -> None:
    value = json.dumps(
        {"job_id": str(row.job_id), "request_hash": row.request_hash},
        sort_keys=True,
        separators=(",", ":"),
    )
    await redis.set(
        _redis_key(tenant_id, key),
        value,
        ex=Settings().idempotency_ttl_seconds,
    )


async def begin_idempotent(
    session,
    redis,
    tenant_id: UUID,
    key: str,
    request_hash: str,
    job_id: UUID,
) -> Literal["created"]:
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

    await _write_cache(redis, tenant_id, key, row)
    return "created"


async def resolve_idempotency(
    session,
    redis,
    tenant_id: UUID,
    key: str,
    request_hash: str,
    job_id: UUID,
) -> IdempotencyOutcome:
    cached = await redis.get(_redis_key(tenant_id, key))
    if cached is not None:
        return _cached_outcome(cached, request_hash)

    try:
        await begin_idempotent(
            session, redis, tenant_id, key, request_hash, job_id
        )
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
        await _write_cache(redis, tenant_id, key, existing)
        return IdempotencyOutcome(kind="replay", job_id=existing.job_id)

    return IdempotencyOutcome(kind="created", job_id=job_id)
