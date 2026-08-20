import pytest

from app.domain.errors import AppError
from app.gateway.circuit import CircuitBreaker
from app.gateway.client import LiteLLMGateway, Usage


def test_circuit_opens_after_five_failures_in_window():
    breaker = CircuitBreaker(threshold=5, window_seconds=30)
    for _ in range(5):
        assert breaker.allow()
        breaker.record_failure()
    assert breaker.state == "open"
    assert breaker.allow() is False


def test_circuit_half_open_after_window():
    breaker = CircuitBreaker(threshold=1, window_seconds=0)
    assert breaker.allow()
    breaker.record_failure()
    assert breaker.state == "open"
    assert breaker.allow() is True
    assert breaker.state == "half_open"
    breaker.record_success()
    assert breaker.state == "closed"


class _AlwaysFail:
    async def post_chat(self, alias: str, messages: list[dict]) -> tuple[dict, Usage]:
        raise AppError("GATEWAY_RETRYABLE", "down", http_status=503)


class _FlakyThenOk:
    def __init__(self):
        self.calls = 0

    async def post_chat(self, alias: str, messages: list[dict]) -> tuple[dict, Usage]:
        self.calls += 1
        if alias == "reasoning-high":
            raise AppError("GATEWAY_RETRYABLE", "down", http_status=503)
        return {"ok": True}, Usage(usd=0.0, tokens=1)


@pytest.mark.asyncio
async def test_fallback_alias_is_used_after_primary_exhausts():
    transport = _FlakyThenOk()
    gw = LiteLLMGateway(
        transport=transport,
        max_attempts=1,
        fallbacks={"reasoning-high": "reasoning-fast"},
    )
    data, usage = await gw.complete_json(
        "reasoning-high", [], schema_name="story_plan"
    )
    assert data == {"ok": True}
    assert usage.degraded is False
    assert transport.calls == 2


@pytest.mark.asyncio
async def test_open_circuit_returns_cached_degraded_result():
    class _Once:
        def __init__(self):
            self.calls = 0

        async def post_chat(self, alias, messages):
            self.calls += 1
            if self.calls == 1:
                return {"beats": []}, Usage(usd=0.01, tokens=1)
            raise AppError("GATEWAY_RETRYABLE", "down", http_status=503)

    gw = LiteLLMGateway(transport=_Once(), max_attempts=1)
    await gw.complete_json("reasoning-high", [], schema_name="story_plan")
    gw._breaker("reasoning-high").record_failure()
    gw._breaker("reasoning-high").record_failure()
    gw._breaker("reasoning-high").record_failure()
    gw._breaker("reasoning-high").record_failure()
    gw._breaker("reasoning-high").record_failure()
    data, usage = await gw.complete_json("reasoning-high", [], schema_name="story_plan")
    assert data == {"beats": []}
    assert usage.degraded is True
