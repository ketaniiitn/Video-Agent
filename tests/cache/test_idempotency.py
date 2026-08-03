import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import fakeredis.aioredis
import pytest
from sqlalchemy.exc import IntegrityError

from app.cache.idempotency import (
    mirror_idempotency_to_redis,
    request_hash,
    resolve_idempotency,
)
from app.db.models import IdempotencyKey
from app.domain.errors import AppError
from app.domain.schemas import BudgetCaps


class FakeSession:
    def __init__(self, existing=None):
        self.added = None
        self.existing = existing
        self.committed = False
        self.rolled_back = False

    @asynccontextmanager
    async def begin_nested(self):
        yield

    def add(self, row):
        self.added = row

    async def flush(self):
        if self.existing is not None:
            raise IntegrityError("INSERT", {}, Exception("unique violation"))

    async def scalar(self, _statement):
        return self.existing

    async def rollback(self):
        self.rolled_back = True

    async def commit(self):
        self.committed = True


def test_request_hash_stable_for_same_body():
    budget = BudgetCaps()

    assert request_hash("hello", budget) == request_hash("hello", budget)


def test_request_hash_changes_with_prompt():
    assert request_hash("a", None) != request_hash("b", None)


def test_request_hash_uses_canonical_json():
    body = {
        "budget": BudgetCaps().model_dump(mode="json"),
        "prompt": "hello",
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))

    assert request_hash("hello", BudgetCaps()) == hashlib.sha256(
        canonical.encode()
    ).hexdigest()


@pytest.mark.asyncio
async def test_redis_fast_path_replays_matching_request():
    redis = fakeredis.aioredis.FakeRedis()
    tenant_id = uuid4()
    job_id = uuid4()
    digest = request_hash("hello", None)
    await redis.set(
        f"idem:{tenant_id}:request-key",
        json.dumps({"request_hash": digest, "job_id": str(job_id)}),
    )

    outcome = await resolve_idempotency(
        None, redis, tenant_id, "request-key", digest, uuid4()
    )

    assert outcome.kind == "replay"
    assert outcome.job_id == job_id


@pytest.mark.asyncio
async def test_redis_fast_path_rejects_hash_mismatch():
    redis = fakeredis.aioredis.FakeRedis()
    tenant_id = uuid4()
    await redis.set(
        f"idem:{tenant_id}:request-key",
        json.dumps(
            {
                "request_hash": request_hash("first", None),
                "job_id": str(uuid4()),
            }
        ),
    )

    with pytest.raises(AppError) as error:
        await resolve_idempotency(
            None,
            redis,
            tenant_id,
            "request-key",
            request_hash("different", None),
            uuid4(),
        )

    assert error.value.code == "IDEMPOTENCY_KEY_REUSE_MISMATCH"
    assert error.value.http_status == 422


@pytest.mark.asyncio
async def test_outer_rollback_after_insert_does_not_populate_redis():
    redis = fakeredis.aioredis.FakeRedis()
    session = FakeSession()
    tenant_id = uuid4()
    job_id = uuid4()
    digest = request_hash("hello", None)

    outcome = await resolve_idempotency(
        session, redis, tenant_id, "request-key", digest, job_id
    )
    await session.rollback()

    assert outcome.kind == "created"
    assert outcome.job_id == job_id
    assert session.added.job_id == job_id
    assert session.rolled_back is True
    assert await redis.get(f"idem:{tenant_id}:request-key") is None


@pytest.mark.asyncio
async def test_redis_mirror_is_explicit_after_commit():
    redis = fakeredis.aioredis.FakeRedis()
    session = FakeSession()
    tenant_id = uuid4()
    job_id = uuid4()
    digest = request_hash("hello", None)

    await resolve_idempotency(
        session, redis, tenant_id, "request-key", digest, job_id
    )
    assert await redis.get(f"idem:{tenant_id}:request-key") is None

    await session.commit()
    await mirror_idempotency_to_redis(
        redis, tenant_id, "request-key", digest, job_id
    )

    assert session.committed is True
    cached = json.loads(await redis.get(f"idem:{tenant_id}:request-key"))
    assert cached == {"job_id": str(job_id), "request_hash": digest}
    assert 86399 <= await redis.ttl(f"idem:{tenant_id}:request-key") <= 86400


@pytest.mark.asyncio
async def test_postgres_unique_violation_replays_existing_job():
    redis = fakeredis.aioredis.FakeRedis()
    tenant_id = uuid4()
    existing_job_id = uuid4()
    digest = request_hash("hello", None)
    existing = IdempotencyKey(
        tenant_id=tenant_id,
        key="request-key",
        request_hash=digest,
        job_id=existing_job_id,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )

    outcome = await resolve_idempotency(
        FakeSession(existing),
        redis,
        tenant_id,
        "request-key",
        digest,
        uuid4(),
    )

    assert outcome.kind == "replay"
    assert outcome.job_id == existing_job_id


@pytest.mark.asyncio
async def test_postgres_unique_violation_rejects_hash_mismatch():
    redis = fakeredis.aioredis.FakeRedis()
    tenant_id = uuid4()
    existing = IdempotencyKey(
        tenant_id=tenant_id,
        key="request-key",
        request_hash=request_hash("first", None),
        job_id=uuid4(),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )

    with pytest.raises(AppError) as error:
        await resolve_idempotency(
            FakeSession(existing),
            redis,
            tenant_id,
            "request-key",
            request_hash("different", None),
            uuid4(),
        )

    assert error.value.code == "IDEMPOTENCY_KEY_REUSE_MISMATCH"
    assert error.value.http_status == 422
