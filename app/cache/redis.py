"""Redis client factory — supports redis:// and rediss:// (TLS)."""

from __future__ import annotations

import re

import redis.asyncio as redis_asyncio

_SCHEME_RE = re.compile(r"(rediss?://\S+)", re.IGNORECASE)


def normalize_redis_url(url: str) -> str:
    """Accept a DSN, including a pasted ``redis-cli -u <dsn>`` command."""
    raw = (url or "").strip().strip("'").strip('"')
    if not raw:
        raise ValueError("REDIS_URL is required")
    match = _SCHEME_RE.search(raw)
    if match:
        return match.group(1).rstrip("\"'")
    raise ValueError(
        "REDIS_URL must be redis://... or rediss://... "
        "(paste the URL only, not a redis-cli command)"
    )


def create_redis(url: str) -> redis_asyncio.Redis:
    return redis_asyncio.from_url(
        normalize_redis_url(url),
        socket_connect_timeout=5.0,
        socket_timeout=10.0,
        health_check_interval=30,
        retry_on_timeout=True,
    )
