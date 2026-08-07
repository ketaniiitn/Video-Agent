from app.config import Settings
from app.providers.fake import FakeVideoProvider
from app.providers.protocols import VideoProvider


def build_provider(settings: Settings) -> VideoProvider:
    """Return Fake when MCP creds unset; Higgsfield adapter when configured."""
    if settings.video_mcp_url and settings.video_mcp_api_key:
        from app.providers.higgsfield.adapter import HiggsfieldVideoProvider

        return HiggsfieldVideoProvider(
            base_url=settings.video_mcp_url,
            api_key=settings.video_mcp_api_key,
        )
    return FakeVideoProvider()
