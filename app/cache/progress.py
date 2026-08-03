import json


PROGRESS_TTL_SECONDS = 86400


async def write_progress(redis, job_id, payload: dict) -> None:
    value = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    await redis.execute_command(
        "SETEX", f"progress:{job_id}", PROGRESS_TTL_SECONDS, value
    )


async def clear_progress(redis, job_id) -> None:
    await redis.delete(f"progress:{job_id}")
