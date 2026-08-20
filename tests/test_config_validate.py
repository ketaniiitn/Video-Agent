from app.config import Settings
from app.config_validate import missing_runtime_variables


def test_empty_settings_require_database_and_redis():
    settings = Settings(_env_file=None, feature_story_planning=False)
    missing = missing_runtime_variables(settings)
    assert "DATABASE_URL" in missing
    assert "REDIS_URL" in missing
    assert "LITELLM_PROXY_URL" not in missing
    assert "VIDEO_MCP_URL" not in missing


def test_story_planning_requires_litellm():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        redis_url="rediss://default:p@h:6379",
        feature_story_planning=True,
        litellm_proxy_url="",
    )
    assert "LITELLM_PROXY_URL" in missing_runtime_variables(settings)


def test_shot_generation_requires_video_mcp():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db",
        redis_url="rediss://default:p@h:6379",
        litellm_proxy_url="https://llm.example",
        feature_story_planning=True,
        feature_shot_generation=True,
        feature_qc_repair=False,
        feature_assemble_deliver=False,
        video_mcp_url="",
        video_mcp_api_key="",
    )
    missing = missing_runtime_variables(settings)
    assert "VIDEO_MCP_URL" in missing
    assert "VIDEO_MCP_API_KEY" in missing


def test_full_flags_with_creds_only_fail_ffmpeg_if_missing(monkeypatch):
    monkeypatch.setattr("app.config_validate.ffmpeg_available", lambda _binary: True)
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/db?ssl=require",
        redis_url="rediss://default:p@h:6379",
        litellm_proxy_url="https://llm.example",
        video_mcp_url="https://mcp.example/mcp",
        video_mcp_api_key="key",
        presign_secret="secret",
        feature_story_planning=True,
        feature_shot_generation=True,
        feature_qc_repair=True,
        feature_assemble_deliver=True,
    )
    assert missing_runtime_variables(settings) == []
