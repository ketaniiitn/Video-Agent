import json

import fakeredis.aioredis
import pytest

from app.cache.locks import release_job_lock, try_acquire_job_lock
from app.cache.progress import clear_progress, write_progress


@pytest.mark.asyncio
async def test_lock_is_non_blocking():
    redis = fakeredis.aioredis.FakeRedis()

    token = await try_acquire_job_lock(redis, "j1")
    assert token is not None
    assert await redis.get("lock:j1") == token.encode()
    assert await try_acquire_job_lock(redis, "j1") is None
    await release_job_lock(redis, "j1", token)
    assert await try_acquire_job_lock(redis, "j1") is not None


@pytest.mark.asyncio
async def test_wrong_token_cannot_release_lock():
    redis = fakeredis.aioredis.FakeRedis()
    token = await try_acquire_job_lock(redis, "j1")
    assert token is not None

    await release_job_lock(redis, "j1", "not-the-owner")

    assert await redis.get("lock:j1") == token.encode()
    assert await try_acquire_job_lock(redis, "j1") is None


@pytest.mark.asyncio
async def test_lock_uses_requested_ttl():
    redis = fakeredis.aioredis.FakeRedis()

    await try_acquire_job_lock(redis, "j1", ttl=42)

    assert await redis.ttl("lock:j1") == 42


@pytest.mark.asyncio
async def test_progress_is_json_with_one_day_ttl():
    redis = fakeredis.aioredis.FakeRedis()
    payload = {"node": "story_plan", "percent": 25}

    await write_progress(redis, "j1", payload)

    assert json.loads(await redis.get("progress:j1")) == payload
    assert 86399 <= await redis.ttl("progress:j1") <= 86400


@pytest.mark.asyncio
async def test_clear_progress_removes_value():
    redis = fakeredis.aioredis.FakeRedis()
    await write_progress(redis, "j1", {"percent": 25})

    await clear_progress(redis, "j1")

    assert await redis.get("progress:j1") is None
