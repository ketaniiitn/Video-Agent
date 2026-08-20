import json

import pytest

from app.config import Settings
from app.domain.errors import AppError
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
        if name == "models_explore":
            return {"structuredContent": {"items": []}}
        assert name == "generate_video"
        args = payload["params"]["arguments"]
        assert "model" not in args
        assert args["params"]["model"] == "seedance_2_0"
        assert args["params"]["prompt"] == "hero walks"
        assert args["params"]["duration"] == 10
        assert args["params"]["aspect_ratio"] == "16:9"
        assert "duration_seconds" not in args
        assert "prior_frame_path" not in args
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


@pytest.mark.asyncio
async def test_higgsfield_adapter_downloads_video_url(monkeypatch):
    async def transport(name: str, payload: dict):
        if name == "models_explore":
            return {"structuredContent": {"items": []}}
        return {"video_url": "https://cdn.example/clip.mp4", "cost_usd": 0.1}

    class _FakeResponse:
        status_code = 200
        content = b"DOWNLOADED"

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            assert url == "https://cdn.example/clip.mp4"
            return _FakeResponse()

    monkeypatch.setattr("app.providers.higgsfield.adapter.httpx.AsyncClient", _FakeClient)
    from app.providers.higgsfield.adapter import HiggsfieldMcpClient

    client = HiggsfieldMcpClient(
        base_url="https://example", api_key="k", transport=transport
    )
    provider = HiggsfieldVideoProvider(
        base_url="https://example", api_key="k", client=client
    )
    result = await provider.generate_clip(GenerateClipRequest(prompt="walk"))
    assert result.video_bytes == b"DOWNLOADED"


@pytest.mark.asyncio
async def test_higgsfield_polls_job_status_then_downloads(monkeypatch):
    calls: list[str] = []

    async def transport(name: str, payload: dict):
        calls.append(name)
        args = payload["params"]["arguments"]
        if name == "models_explore":
            return {"structuredContent": {"items": []}}
        if name == "generate_video":
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps({"job_id": "job-123", "status": "queued"}),
                    }
                ]
            }
        if name == "job_status":
            assert args["job_id"] == "job-123"
            return {"status": "completed", "video_url": "https://cdn.example/shot.mp4"}
        if name == "job_display":
            return {"url": "https://cdn.example/shot.mp4"}
        raise AssertionError(name)

    class _FakeResponse:
        status_code = 200
        content = b"CLIP"

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            assert url == "https://cdn.example/shot.mp4"
            return _FakeResponse()

        async def post(self, *args, **kwargs):
            raise AssertionError("HTTP post should not run when transport is set")

    monkeypatch.setattr("app.providers.higgsfield.adapter.httpx.AsyncClient", _FakeClient)
    from app.providers.higgsfield.adapter import HiggsfieldMcpClient

    client = HiggsfieldMcpClient(
        base_url="https://example", api_key="k", transport=transport
    )
    provider = HiggsfieldVideoProvider(
        base_url="https://example",
        api_key="k",
        client=client,
        poll_interval_seconds=0,
    )
    result = await provider.generate_clip(GenerateClipRequest(prompt="walk"))
    assert result.video_bytes == b"CLIP"
    assert calls[:3] == ["models_explore", "generate_video", "job_status"]


@pytest.mark.asyncio
async def test_higgsfield_is_error_is_rejected():
    async def transport(name: str, payload: dict):
        del payload
        if name == "models_explore":
            return {"structuredContent": {"items": []}}
        assert name == "generate_video"
        return {
            "isError": True,
            "content": [{"type": "text", "text": "workspace required"}],
        }

    from app.providers.higgsfield.adapter import HiggsfieldMcpClient

    client = HiggsfieldMcpClient(
        base_url="https://example", api_key="k", transport=transport
    )
    provider = HiggsfieldVideoProvider(
        base_url="https://example", api_key="k", client=client
    )
    with pytest.raises(AppError) as exc:
        await provider.generate_clip(GenerateClipRequest(prompt="walk"))
    assert "workspace required" in str(exc.value)


def test_extract_job_id_from_text():
    from app.providers.higgsfield.adapter import extract_job_id

    assert (
        extract_job_id(
            {
                "content": [
                    {
                        "type": "text",
                        "text": "Queued job_id: abcdefgh-1234-1234-1234-1234567890ab",
                    }
                ]
            }
        )
        == "abcdefgh-1234-1234-1234-1234567890ab"
    )


def test_parse_sse_takes_jsonrpc_result():
    from app.providers.higgsfield.adapter import parse_sse_jsonrpc

    body = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","method":"notifications/progress"}\n'
        "\n"
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":2,"result":{"video_base64":"QQ=="}}\n'
        "\n"
    )
    parsed = parse_sse_jsonrpc(body)
    assert parsed["result"]["video_base64"] == "QQ=="


def test_fit_generate_video_arguments_puts_model_inside_params():
    from app.providers.higgsfield.adapter import fit_generate_video_arguments

    args = fit_generate_video_arguments(
        GenerateClipRequest(prompt="hero walks", duration_seconds=10)
    )
    assert args == {
        "params": {
            "model": "seedance_2_0",
            "prompt": "hero walks",
            "duration": 10,
            "aspect_ratio": "16:9",
        },
    }
    assert "model" not in args


def test_fit_generate_video_arguments_unwraps_params_anyof_schema():
    from app.providers.higgsfield.adapter import fit_generate_video_arguments

    schema = {
        "type": "object",
        "properties": {
            "params": {
                "anyOf": [
                    {
                        "type": "object",
                        "properties": {
                            "model": {"type": "string"},
                            "prompt": {"type": "string"},
                            "duration": {"type": "integer"},
                            "aspect_ratio": {"type": "string"},
                        },
                        "required": ["model"],
                    },
                    {"type": "string"},
                ]
            }
        },
        "required": ["params"],
    }
    args = fit_generate_video_arguments(
        GenerateClipRequest(prompt="walk", duration_seconds=10),
        schema=schema,
    )
    assert args["params"]["model"] == "seedance_2_0"
    assert args["params"]["prompt"] == "walk"
    assert "model" not in args


def test_pick_video_model_keeps_preferred_when_in_catalog():
    from app.providers.higgsfield.adapter import pick_video_model

    assert (
        pick_video_model(
            "seedance_2_0",
            ["cinematic_studio_3_0", "seedance_2_0", "clipify"],
        )
        == "seedance_2_0"
    )


def test_pick_video_model_falls_back_when_seedance_missing():
    from app.providers.higgsfield.adapter import pick_video_model

    assert (
        pick_video_model(
            "seedance_2_0",
            ["clipify", "cinematic_studio_3_0", "marketing_studio_video"],
        )
        == "cinematic_studio_3_0"
    )


def test_clamp_duration_picks_nearest_allowed():
    from app.providers.higgsfield.adapter import clamp_duration

    assert clamp_duration(10, allowed=[5, 10]) == 10
    assert clamp_duration(10, allowed=[4, 5, 8]) == 8
    assert clamp_duration(10, duration_range={"min": 2, "max": 12}) == 10
    assert clamp_duration(10, duration_range={"min": 2, "max": 8}) == 8


@pytest.mark.asyncio
async def test_higgsfield_uses_catalog_model_when_seedance_missing():
    import base64

    async def transport(name: str, payload: dict):
        args = payload["params"]["arguments"]
        if name == "models_explore":
            if args.get("action") == "get":
                return {
                    "structuredContent": {
                        "id": "cinematic_studio_3_0",
                        "durations": [5, 10],
                    }
                }
            return {
                "structuredContent": {
                    "items": [
                        {"id": "clipify"},
                        {"id": "cinematic_studio_3_0"},
                    ]
                }
            }
        assert args["params"]["model"] == "cinematic_studio_3_0"
        assert args["params"]["duration"] == 10
        return {
            "video_base64": base64.b64encode(b"OK").decode("ascii"),
            "cost_usd": 0.0,
        }

    from app.providers.higgsfield.adapter import HiggsfieldMcpClient

    client = HiggsfieldMcpClient(
        base_url="https://example", api_key="k", transport=transport
    )
    provider = HiggsfieldVideoProvider(
        base_url="https://example", api_key="k", client=client
    )
    result = await provider.generate_clip(GenerateClipRequest(prompt="walk"))
    assert result.video_bytes == b"OK"


def test_fit_generate_video_arguments_respects_flat_schema():
    from app.providers.higgsfield.adapter import fit_generate_video_arguments

    schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string"},
            "duration": {"type": "integer"},
            "aspect_ratio": {"type": "string"},
            "model": {"type": "string"},
        },
    }
    args = fit_generate_video_arguments(
        GenerateClipRequest(prompt="walk", duration_seconds=10),
        schema=schema,
    )
    assert args["prompt"] == "walk"
    assert args["duration"] == 10
    assert "params" not in args


@pytest.mark.asyncio
async def test_mcp_http_sends_streamable_accept_and_initializes():
    import json

    import httpx

    from app.providers.higgsfield.adapter import HiggsfieldMcpClient

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        accept = request.headers.get("accept", "")
        assert "application/json" in accept
        assert "text/event-stream" in accept
        payload = json.loads(request.content.decode("utf-8") or "{}")
        method = payload.get("method")
        if method == "initialize":
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "mcp-session-id": "sess-1",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "serverInfo": {"name": "higgsfield"},
                    },
                },
            )
        if method == "notifications/initialized":
            assert request.headers.get("mcp-session-id") == "sess-1"
            return httpx.Response(202)
        if method == "tools/call":
            assert request.headers.get("mcp-session-id") == "sess-1"
            sse = (
                "event: message\n"
                'data: {"jsonrpc":"2.0","id":3,'
                '"result":{"video_base64":"UkVBTA==","cost_usd":0.2}}\n'
                "\n"
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=sse,
            )
        return httpx.Response(400, json={"error": "unexpected"})

    client = HiggsfieldMcpClient(
        base_url="https://mcp.example/mcp",
        api_key="k",
        timeout_seconds=5.0,
        httpx_transport=httpx.MockTransport(handler),
    )
    result = await client.call_tool("generate_video", {"prompt": "walk"})
    assert result["video_base64"] == "UkVBTA=="
    assert [json.loads(r.content)["method"] for r in seen] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
