from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.domain.errors import AppError
from app.providers.protocols import GenerateClipRequest, GenerateClipResult

Transport = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class HiggsfieldMcpClient:
    """Minimal MCP JSON-RPC client over HTTP for video generation tools."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        transport: Transport | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._transport = transport
        self._timeout = timeout_seconds
        self._request_id = 0

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self._request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        if self._transport is not None:
            return await self._transport(name, payload)
        return await self._http_post(payload)

    async def _http_post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(
                    self.base_url, json=payload, headers=headers
                )
            except httpx.HTTPError as exc:
                raise AppError(
                    "PROVIDER_RETRYABLE",
                    f"Higgsfield MCP transport error: {exc}",
                    http_status=503,
                ) from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise AppError(
                "PROVIDER_RETRYABLE",
                f"Higgsfield MCP HTTP {response.status_code}",
                http_status=503,
            )
        if response.status_code >= 400:
            raise AppError(
                "PROVIDER_REJECTED",
                f"Higgsfield MCP HTTP {response.status_code}: {response.text[:300]}",
                http_status=502,
            )
        data = response.json()
        if "error" in data:
            raise AppError(
                "PROVIDER_REJECTED",
                f"Higgsfield MCP error: {data['error']}",
                http_status=502,
            )
        return data.get("result", data)


class HiggsfieldVideoProvider:
    """VideoProvider adapter — nodes never import this module directly."""

    TOOL_NAME = "generate_video"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        client: HiggsfieldMcpClient | None = None,
    ) -> None:
        self._client = client or HiggsfieldMcpClient(
            base_url=base_url, api_key=api_key
        )

    def capabilities(self) -> set[str]:
        return {"frame_conditioning", "text_to_video"}

    async def generate_clip(self, req: GenerateClipRequest) -> GenerateClipResult:
        arguments: dict[str, Any] = {
            "prompt": req.prompt,
            "duration_seconds": req.duration_seconds,
        }
        if req.prior_frame_path:
            arguments["prior_frame_path"] = req.prior_frame_path
        if req.seed is not None:
            arguments["seed"] = req.seed

        result = await self._client.call_tool(self.TOOL_NAME, arguments)
        video_b64 = result.get("video_base64") or result.get("content")
        if isinstance(video_b64, list):
            # MCP content array shape
            for part in video_b64:
                if isinstance(part, dict) and part.get("type") == "resource":
                    video_b64 = part.get("blob") or part.get("text")
                    break
        if not isinstance(video_b64, str) or not video_b64:
            # Allow raw bytes path for mocked transports
            raw = result.get("video_bytes")
            if isinstance(raw, (bytes, bytearray)):
                video_bytes = bytes(raw)
            else:
                raise AppError(
                    "PROVIDER_INVALID_RESPONSE",
                    "Higgsfield MCP response missing video payload",
                    http_status=502,
                )
        else:
            import base64

            try:
                video_bytes = base64.b64decode(video_b64)
            except Exception as exc:  # noqa: BLE001
                raise AppError(
                    "PROVIDER_INVALID_RESPONSE",
                    "Higgsfield MCP returned invalid base64 video",
                    http_status=502,
                ) from exc

        cost = float(result.get("cost_usd") or 0.0)
        seed = result.get("seed", req.seed)
        return GenerateClipResult(
            video_bytes=video_bytes,
            cost_usd=cost,
            provider_id="higgsfield-mcp",
            seed=seed if seed is None or isinstance(seed, int) else req.seed,
        )
