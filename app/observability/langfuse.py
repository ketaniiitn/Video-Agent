"""Optional Langfuse HTTP tracer. Jobs never fail because telemetry failed."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import Settings
from app.observability.logging import log_json

logger = logging.getLogger(__name__)


class LangfuseTracer:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.enabled = bool(settings.langfuse_public_key and settings.langfuse_secret_key)

    async def ingest(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{self.settings.langfuse_host.rstrip('/')}/api/public/ingestion",
                    json={"batch": [event]},
                    auth=(
                        self.settings.langfuse_public_key,
                        self.settings.langfuse_secret_key,
                    ),
                )
        except httpx.HTTPError as exc:
            log_json(logger, "langfuse_ingest_failed", error=str(exc))
