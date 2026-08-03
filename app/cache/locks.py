from uuid import uuid4


async def try_acquire_job_lock(redis, job_id, ttl: int = 600) -> str | None:
    token = str(uuid4())
    acquired = await redis.set(f"lock:{job_id}", token, nx=True, ex=ttl)
    return token if acquired else None


async def release_job_lock(redis, job_id, token: str) -> None:
    key = f"lock:{job_id}"
    stored_token = await redis.get(key)
    if isinstance(stored_token, bytes):
        stored_token = stored_token.decode()
    if stored_token == token:
        await redis.delete(key)
