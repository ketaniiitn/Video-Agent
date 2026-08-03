import asyncio
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TYPE_CHECKING

import httpx

from app.domain.errors import AppError

if TYPE_CHECKING:
    from app.config import Settings
    from app.gateway.protocols import GatewayClient


@dataclass(frozen=True, slots=True)
class Usage:
    usd: float
    tokens: int


class _Transport(Protocol):
    async def post_chat(
        self, alias: str, messages: list[dict]
    ) -> tuple[dict, Usage]: ...


class FakeGateway:
    def __init__(
        self,
        responses: dict[str, dict] | None = None,
        usage: Usage | None = None,
    ):
        self.responses = responses or {}
        self.usage = usage or Usage(usd=0.0, tokens=0)

    async def complete_json(
        self,
        alias: str,
        messages: list[dict],
        schema_name: str,
    ) -> tuple[dict, Usage]:
        del alias, messages
        if schema_name not in self.responses:
            raise AppError(
                "GATEWAY_FAKE_RESPONSE_MISSING",
                f"No fake gateway response configured for schema '{schema_name}'",
                http_status=500,
            )
        return self.responses[schema_name], self.usage


class _FixtureGateway:
    def __init__(self, fixture_dir: Path):
        self.fixture_dir = fixture_dir

    async def complete_json(
        self,
        alias: str,
        messages: list[dict],
        schema_name: str,
    ) -> tuple[dict, Usage]:
        del alias, messages
        fixture = self.fixture_dir / f"{schema_name}.json"
        try:
            data = json.loads(fixture.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AppError(
                "GATEWAY_STUB_FIXTURE_MISSING",
                f"Gateway stub fixture unavailable for schema '{schema_name}'",
                http_status=500,
            ) from exc
        return data, Usage(usd=0.0, tokens=0)


class _LiteLLMTransport:
    def __init__(self, proxy_url: str, api_key: str):
        self.proxy_url = proxy_url.rstrip("/")
        self.api_key = api_key

    async def post_chat(
        self, alias: str, messages: list[dict]
    ) -> tuple[dict, Usage]:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.proxy_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": alias,
                        "messages": messages,
                        "response_format": {"type": "json_object"},
                    },
                )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise AppError(
                "GATEWAY_RETRYABLE",
                "LiteLLM proxy request failed transiently",
                http_status=503,
            ) from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise AppError(
                "GATEWAY_RETRYABLE",
                f"LiteLLM proxy returned retryable HTTP {response.status_code}",
                http_status=503,
            )
        if response.is_error:
            raise AppError(
                "GATEWAY_REJECTED",
                f"LiteLLM proxy rejected the request with HTTP {response.status_code}",
                http_status=502,
            )

        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            data = content if isinstance(content, dict) else json.loads(content)
            usage_data = payload.get("usage", {})
            usage = Usage(
                usd=float(payload.get("response_cost", 0.0)),
                tokens=int(usage_data.get("total_tokens", 0)),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AppError(
                "GATEWAY_INVALID_RESPONSE",
                "LiteLLM proxy returned an invalid JSON completion",
                http_status=502,
            ) from exc
        return data, usage


class LiteLLMGateway:
    def __init__(self, transport: _Transport, max_attempts: int = 3):
        if not 1 <= max_attempts <= 3:
            raise ValueError("max_attempts must be between 1 and 3")
        self.transport = transport
        self.max_attempts = max_attempts

    async def complete_json(
        self,
        alias: str,
        messages: list[dict],
        schema_name: str,
    ) -> tuple[dict, Usage]:
        del schema_name
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await self.transport.post_chat(alias, messages)
            except AppError as exc:
                if exc.code != "GATEWAY_RETRYABLE":
                    raise
                if attempt == self.max_attempts:
                    raise AppError(
                        "GATEWAY_EXHAUSTED",
                        (
                            "LiteLLM proxy retries exhausted; no completion was "
                            "produced. Retry the job after the dependency recovers."
                        ),
                        http_status=502,
                    ) from exc
                backoff = 0.1 * (2 ** (attempt - 1))
                await asyncio.sleep(backoff + random.uniform(0, backoff))
        raise RuntimeError("unreachable")


def build_gateway(settings: "Settings") -> "GatewayClient":
    if not settings.litellm_proxy_url:
        fixture_dir = Path(__file__).parents[2] / "tests" / "gateway" / "fixtures"
        return _FixtureGateway(fixture_dir)
    return LiteLLMGateway(
        transport=_LiteLLMTransport(
            proxy_url=settings.litellm_proxy_url,
            api_key=settings.litellm_master_key,
        ),
        max_attempts=3,
    )
