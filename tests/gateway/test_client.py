import pytest

from app.config import Settings
from app.domain.errors import AppError
from app.gateway.client import LiteLLMGateway, Usage, build_gateway


@pytest.mark.asyncio
async def test_stub_returns_story_plan_fixture():
    settings = Settings(litellm_proxy_url="")
    gw = build_gateway(settings)
    data, usage = await gw.complete_json(
        "reasoning-high",
        [{"role": "user", "content": "x"}],
        schema_name="story_plan",
    )
    assert "beats" in data
    assert usage.tokens >= 0


class _FlakyTransport:
    """Stand-in used by LiteLLMGateway under test via dependency injection."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    async def post_chat(self, alias: str, messages: list[dict]) -> tuple[dict, Usage]:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise AppError("GATEWAY_RETRYABLE", "transient", http_status=503)
        return {"beats": []}, Usage(usd=0.01, tokens=10)


@pytest.mark.asyncio
async def test_retry_then_succeed():
    transport = _FlakyTransport(fail_times=2)
    gw = LiteLLMGateway(transport=transport, max_attempts=3)
    data, _ = await gw.complete_json("reasoning-high", [], schema_name="story_plan")
    assert transport.calls == 3
    assert "beats" in data


@pytest.mark.asyncio
async def test_exhausted_retries_raise_app_error():
    transport = _FlakyTransport(fail_times=99)
    gw = LiteLLMGateway(transport=transport, max_attempts=3)
    with pytest.raises(AppError) as ei:
        await gw.complete_json("reasoning-high", [], schema_name="story_plan")
    assert ei.value.code == "GATEWAY_EXHAUSTED"
    assert transport.calls == 3
