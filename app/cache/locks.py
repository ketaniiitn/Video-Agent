async def try_acquire_job_lock(redis, job_id, ttl: int = 600) -> bool:
    acquired = await redis.set(f"lock:{job_id}", "1", nx=True, ex=ttl)
    return bool(acquired)


async def release_job_lock(redis, job_id) -> None:
    await redis.delete(f"lock:{job_id}")
