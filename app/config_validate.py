"""Fail-fast checks for the enabled pipeline. Tests skip this via APP_ENV=test."""

from __future__ import annotations

from app.config import Settings
from app.media.ffmpeg import ffmpeg_available


def missing_runtime_variables(settings: Settings) -> list[str]:
    """Names of env vars required for the currently enabled feature flags."""
    missing: list[str] = []
    if not settings.database_url:
        missing.append("DATABASE_URL")
    if not settings.redis_url:
        missing.append("REDIS_URL")
    if (settings.feature_story_planning or settings.feature_qc_repair) and not settings.litellm_proxy_url:
        missing.append("LITELLM_PROXY_URL")
    if settings.feature_shot_generation:
        if not settings.video_mcp_url:
            missing.append("VIDEO_MCP_URL")
        if not settings.video_mcp_api_key:
            missing.append("VIDEO_MCP_API_KEY")
    if settings.feature_shot_generation or settings.feature_assemble_deliver:
        if not ffmpeg_available(settings.ffmpeg_binary):
            missing.append("FFMPEG_BINARY")
    if settings.feature_assemble_deliver and not settings.presign_secret:
        missing.append("PRESIGN_SECRET")
    return missing


def format_missing_config(missing: list[str]) -> str:
    lines = ["Missing required environment variables:"]
    lines.extend(f"- {name}" for name in missing)
    return "\n".join(lines)
