from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
        env_ignore_empty=True,
    )

    app_env: str = "development"

    # Empty defaults: localhost is not assumed. Tests pass explicit values
    # or use in-memory substitutes (SQLite / fakeredis / FakeGateway).
    litellm_proxy_url: str = ""
    litellm_master_key: str = Field(
        default="",
        validation_alias=AliasChoices("LITELLM_MASTER_KEY", "LITELLM_API_KEY"),
    )

    database_url: str = ""
    database_url_sweep: str | None = None

    redis_url: str = ""

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
    video_mcp_timeout_seconds: float = 300.0
    video_mcp_model: str = Field(
        default="seedance_2_0",
        validation_alias=AliasChoices("VIDEO_MCP_MODEL", "HIGGSFIELD_MCP_MODEL"),
    )

    storage_bucket: str = ""
    presigned_url_ttl_seconds: int = 3600
    media_root: str = "./data/media"
    app_base_url: str = "http://127.0.0.1:8000"
    presign_secret: str = ""

    ffmpeg_binary: str = "ffmpeg"
    ffmpeg_timeout_seconds: float = 120.0

    tenant_id: str = ""
    tenant_name: str = "dev"

    feature_story_planning: bool = True
    feature_shot_generation: bool = False
    feature_qc_repair: bool = False
    feature_assemble_deliver: bool = False
    idempotency_ttl_seconds: int = 86400

    gateway_fallback_aliases: dict[str, str] = Field(default_factory=dict)

    @field_validator("gateway_fallback_aliases", mode="before")
    @classmethod
    def _parse_fallback_aliases(cls, value):
        if value in (None, "", {}):
            return {}
        if isinstance(value, dict):
            return value
        import json

        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("GATEWAY_FALLBACK_ALIASES must be a JSON object")
        return {str(key): str(item) for key, item in parsed.items()}

    def database_url_for_sweep(self) -> str:
        """DSN the startup sweep should connect with.

        Falls back to ``database_url`` when ``database_url_sweep`` is unset.
        The sweep no longer requires BYPASSRLS: it lists tenants (no RLS)
        then opens a tenant-scoped session per tenant.
        """
        return self.database_url_sweep or self.database_url
