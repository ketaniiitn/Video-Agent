from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_env: str = "development"

    litellm_proxy_url: str = "http://localhost:4000"
    litellm_master_key: str = ""

    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/video_agent"
    )

    redis_url: str = "redis://localhost:6379/0"

    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"

    video_mcp_url: str = Field(
        default="",
        validation_alias=AliasChoices("VIDEO_MCP_URL", "HIGGSFIELD_MCP_URL"),
    )
    video_mcp_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("VIDEO_MCP_API_KEY", "HIGGSFIELD_MCP_API_KEY"),
    )

    storage_bucket: str = ""
    presigned_url_ttl_seconds: int = 3600

    feature_story_planning: bool = True
    idempotency_ttl_seconds: int = 86400
