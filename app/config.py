from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )

    app_env: str = "development"

    litellm_proxy_url: str = "http://localhost:4000"
    litellm_master_key: str = ""

    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/video_agent"
    )

    # DSN used only by the startup sweep (finds non-terminal jobs across all
    # tenants). Every tenant table has FORCE ROW LEVEL SECURITY, so the
    # normal ``database_url`` role — even unprivileged, tenant-context-free —
    # sees zero rows there; RLS applies to every role, including the table
    # owner, once FORCE is set. The sweep therefore needs to connect as a
    # role with BYPASSRLS (or via ``SET ROLE`` to one), on a role dedicated
    # to the sweep rather than the general per-request role. If unset, this
    # falls back to ``database_url`` — fine for local/dev/tests (SQLite has
    # no RLS; a Postgres dev role with BYPASSRLS also works) but in
    # production this must point at a distinct, privileged DSN or the sweep
    # will silently see nothing to resume.
    database_url_sweep: str | None = None

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
    media_root: str = "./data/media"

    feature_story_planning: bool = True
    feature_shot_generation: bool = False
    idempotency_ttl_seconds: int = 86400

    def database_url_for_sweep(self) -> str:
        """DSN the startup sweep should connect with.

        Falls back to ``database_url`` when ``database_url_sweep`` is unset
        — correct for dev/tests (SQLite has no RLS), but production must
        set ``DATABASE_URL_SWEEP`` to a role with ``BYPASSRLS`` or the sweep
        will silently see no non-terminal jobs under FORCE ROW LEVEL
        SECURITY.
        """
        return self.database_url_sweep or self.database_url
