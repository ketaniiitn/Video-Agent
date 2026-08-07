import pytest

from app.config import Settings
from app.providers.fake import FakeVideoProvider
from app.providers.higgsfield.adapter import HiggsfieldVideoProvider
from app.providers.protocols import GenerateClipRequest
from app.providers.registry import build_provider


@pytest.mark.asyncio
async def test_fake_provider_records_ordered_calls_and_prior_frame():
    provider = FakeVideoProvider()
    assert "frame_conditioning" in provider.capabilities()
    r1 = await provider.generate_clip(GenerateClipRequest(prompt="a"))
    r2 = await provider.generate_clip(
        GenerateClipRequest(prompt="b", prior_frame_path="/tmp/frame_1.jpg")
    )
    assert r1.provider_id == "fake"
    assert len(provider.calls) == 2
    assert provider.calls[0].prior_frame_path is None
    assert provider.calls[1].prior_frame_path == "/tmp/frame_1.jpg"
    assert provider.calls[1].prompt == "b"


def test_build_provider_without_creds_returns_fake(monkeypatch):
    monkeypatch.delenv("VIDEO_MCP_URL", raising=False)
    monkeypatch.delenv("VIDEO_MCP_API_KEY", raising=False)
    monkeypatch.delenv("HIGGSFIELD_MCP_URL", raising=False)
    monkeypatch.delenv("HIGGSFIELD_MCP_API_KEY", raising=False)
    settings = Settings(
        _env_file=None,
        video_mcp_url="",
        video_mcp_api_key="",
    )
    provider = build_provider(settings)
    assert isinstance(provider, FakeVideoProvider)


def test_build_provider_with_creds_returns_higgsfield():
    settings = Settings(
        _env_file=None,
        video_mcp_url="https://mcp.example/mcp",
        video_mcp_api_key="secret",
    )
    provider = build_provider(settings)
    assert isinstance(provider, HiggsfieldVideoProvider)


@pytest.mark.asyncio
async def test_higgsfield_adapter_uses_transport_and_decodes_video():
    import base64

    async def transport(name: str, payload: dict):
        assert name == "generate_video"
        args = payload["params"]["arguments"]
        assert args["prompt"] == "hero walks"
        assert args["prior_frame_path"] == "/f.jpg"
        return {
            "video_base64": base64.b64encode(b"REALBYTES").decode("ascii"),
            "cost_usd": 0.25,
            "seed": 7,
        }

    from app.providers.higgsfield.adapter import HiggsfieldMcpClient

    client = HiggsfieldMcpClient(
        base_url="https://example", api_key="k", transport=transport
    )
    provider = HiggsfieldVideoProvider(
        base_url="https://example", api_key="k", client=client
    )
    result = await provider.generate_clip(
        GenerateClipRequest(prompt="hero walks", prior_frame_path="/f.jpg")
    )
    assert result.video_bytes == b"REALBYTES"
    assert result.cost_usd == 0.25
    assert result.provider_id == "higgsfield-mcp"
    assert result.seed == 7
