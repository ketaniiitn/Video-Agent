from collections.abc import Awaitable, Callable
from typing import Any
import asyncio
import json
import logging
import re

import httpx

from app.domain.errors import AppError
from app.observability.logging import log_json
from app.providers.protocols import GenerateClipRequest, GenerateClipResult

Transport = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
_MAX_ATTEMPTS = 3
_MCP_ACCEPT = "application/json, text/event-stream"
_MCP_PROTOCOL = "2025-03-26"
_DONE = {"completed", "complete", "succeeded", "success", "done", "ready", "finished"}
_FAILED = {"failed", "error", "cancelled", "canceled", "nsfw", "rejected"}
logger = logging.getLogger(__name__)


def parse_sse_jsonrpc(body: str) -> dict[str, Any]:
    """Parse a Streamable HTTP SSE body into the last JSON-RPC result/error."""
    messages: list[dict[str, Any]] = []
    data_lines: list[str] = []

    def flush() -> None:
        if not data_lines:
            return
        raw = "\n".join(data_lines)
        data_lines.clear()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return
        if isinstance(parsed, dict):
            messages.append(parsed)

    for line in body.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif line == "":
            flush()
    flush()
    if not messages:
        raise AppError(
            "PROVIDER_INVALID_RESPONSE",
            "Higgsfield MCP SSE stream contained no JSON-RPC message",
            http_status=502,
        )
    for message in reversed(messages):
        if "result" in message or "error" in message:
            return message
    return messages[-1]


class HiggsfieldMcpClient:
    """MCP Streamable HTTP client for video generation tools."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        transport: Transport | None = None,
        timeout_seconds: float = 300.0,
        httpx_transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._transport = transport
        self._timeout = timeout_seconds
        self._httpx_transport = httpx_transport
        self._request_id = 0
        self._session_id: str | None = None
        self._initialized = False
        self._tools_by_name: dict[str, dict[str, Any]] | None = None

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
        await self._ensure_initialized()
        return await self._http_post(payload)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": _MCP_ACCEPT,
            "MCP-Protocol-Version": _MCP_PROTOCOL,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        self._request_id += 1
        await self._http_post(
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": _MCP_PROTOCOL,
                    "capabilities": {},
                    "clientInfo": {"name": "video-agent", "version": "0.1.0"},
                },
            }
        )
        await self._http_post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            expect_result=False,
        )
        self._initialized = True

    async def tool_input_schema(self, name: str) -> dict[str, Any] | None:
        if self._transport is not None:
            return None
        await self._ensure_initialized()
        if self._tools_by_name is None:
            self._request_id += 1
            listed = await self._http_post(
                {
                    "jsonrpc": "2.0",
                    "id": self._request_id,
                    "method": "tools/list",
                    "params": {},
                }
            )
            self._tools_by_name = {}
            for tool in listed.get("tools") or []:
                if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                    self._tools_by_name[tool["name"]] = tool
        tool = (self._tools_by_name or {}).get(name) or {}
        schema = tool.get("inputSchema") or tool.get("input_schema")
        return schema if isinstance(schema, dict) else None

    async def _http_post(
        self, payload: dict[str, Any], *, expect_result: bool = True
    ) -> dict[str, Any]:
        last_error: AppError | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return await self._post_once(payload, expect_result=expect_result)
            except AppError as exc:
                last_error = exc
                if exc.code != "PROVIDER_RETRYABLE" or attempt == _MAX_ATTEMPTS:
                    raise
                import asyncio
                import random

                backoff = 0.5 * (2 ** (attempt - 1))
                await asyncio.sleep(backoff + random.uniform(0, backoff))
        raise last_error or AppError(
            "PROVIDER_RETRYABLE",
            "Higgsfield MCP retries exhausted",
            http_status=503,
        )

    async def _post_once(
        self, payload: dict[str, Any], *, expect_result: bool = True
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._httpx_transport
        ) as client:
            try:
                response = await client.post(
                    self.base_url, json=payload, headers=self._headers()
                )
            except httpx.HTTPError as exc:
                raise AppError(
                    "PROVIDER_RETRYABLE",
                    f"Higgsfield MCP transport error: {exc}",
                    http_status=503,
                ) from exc
        session = response.headers.get("mcp-session-id")
        if session:
            self._session_id = session
        if response.status_code == 404 and self._session_id:
            self._session_id = None
            self._initialized = False
            raise AppError(
                "PROVIDER_RETRYABLE",
                "Higgsfield MCP session expired",
                http_status=503,
            )
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
        if not expect_result or response.status_code == 202 or not response.content:
            return {}
        content_type = response.headers.get("content-type", "")
        try:
            if "text/event-stream" in content_type:
                data = parse_sse_jsonrpc(response.text)
            else:
                data = response.json()
        except AppError:
            raise
        except ValueError as exc:
            raise AppError(
                "PROVIDER_INVALID_RESPONSE",
                "Higgsfield MCP returned an invalid JSON-RPC body",
                http_status=502,
            ) from exc
        if not isinstance(data, dict):
            raise AppError(
                "PROVIDER_INVALID_RESPONSE",
                "Higgsfield MCP returned a non-object JSON-RPC body",
                http_status=502,
            )
        if "error" in data:
            raise AppError(
                "PROVIDER_REJECTED",
                f"Higgsfield MCP error: {data['error']}",
                http_status=502,
            )
        result = data.get("result", data)
        return result if isinstance(result, dict) else {"result": result}


def _unwrap_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return {**result, **structured}
    content = result.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "text":
                continue
            text = part.get("text") or ""
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return {**result, **parsed}
    return result


def summarize_mcp_result(result: dict[str, Any], *, limit: int = 400) -> str:
    texts: list[str] = []
    content = result.get("content")
    if isinstance(content, list):
        for part in content[:3]:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                texts.append(part["text"][:160])
    payload = {
        "keys": sorted(str(key) for key in result),
        "isError": result.get("isError"),
        "status": result.get("status") or result.get("state"),
        "job_id": result.get("job_id") or result.get("id") or result.get("request_id"),
        "text": texts,
    }
    return json.dumps(payload, default=str)[:limit]


def extract_job_id(result: dict[str, Any]) -> str | None:
    for key in ("job_id", "request_id", "generation_id", "jobId", "id"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            if key == "id" and len(value.strip()) < 8:
                continue
            return value.strip()
    blob = " ".join(_text_blobs(result))
    labeled = re.search(
        r"\b(?:job[_-]?id|request[_-]?id)\b[:\s]+([A-Za-z0-9_-]{8,})",
        blob,
        re.I,
    )
    if labeled:
        return labeled.group(1)
    uuid = re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        blob,
        re.I,
    )
    if uuid:
        return uuid.group(0)
    return None


def _text_blobs(result: dict[str, Any]) -> list[str]:
    blobs: list[str] = []
    content = result.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                blobs.append(part["text"])
    for key in ("message", "detail", "error"):
        value = result.get(key)
        if isinstance(value, str):
            blobs.append(value)
    return blobs


def _raise_if_tool_error(result: dict[str, Any]) -> None:
    if not result.get("isError"):
        return
    detail = " ".join(_text_blobs(result)) or summarize_mcp_result(result)
    raise AppError(
        "PROVIDER_REJECTED",
        f"Higgsfield MCP tool error: {detail[:400]}",
        http_status=502,
    )


def _status_of(result: dict[str, Any]) -> str:
    raw = result.get("status") or result.get("state") or result.get("job_status")
    return str(raw or "").strip().lower()


DEFAULT_VIDEO_MODEL = "seedance_2_0"


def fit_generate_video_arguments(
    req: GenerateClipRequest,
    *,
    model: str = DEFAULT_VIDEO_MODEL,
    schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Map a clip request onto the Higgsfield MCP generate_video schema.

    Official ``generate_video`` requires a nested ``params`` object whose
    required field is ``model``. A sibling top-level ``model`` is ignored, and
    Zod then reports ``params: Invalid input``.
    """
    candidate = {
        "params": {
            "model": model or DEFAULT_VIDEO_MODEL,
            "prompt": req.prompt,
            "duration": int(req.duration_seconds),
            "aspect_ratio": "16:9",
        }
    }
    return _fit_to_schema(schema, candidate)


def _object_properties(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(schema, dict):
        return None
    props = schema.get("properties")
    if isinstance(props, dict) and props:
        return props
    for key in ("anyOf", "oneOf"):
        variants = schema.get(key)
        if not isinstance(variants, list):
            continue
        for variant in variants:
            inner = _object_properties(variant)
            if inner:
                return inner
    return None


def _fit_to_schema(
    schema: dict[str, Any] | None, candidate: dict[str, Any]
) -> dict[str, Any]:
    props = _object_properties(schema)
    if not props:
        return candidate
    if "params" in props:
        params = dict(candidate.get("params") or {})
        inner_schema = props.get("params")
        inner_props = _object_properties(
            inner_schema if isinstance(inner_schema, dict) else None
        )
        if inner_props:
            params = {key: value for key, value in params.items() if key in inner_props}
        return {"params": params}
    flat = dict(candidate.get("params") or {})
    return {key: value for key, value in flat.items() if key in props}


_PREFERRED_VIDEO_MODELS = (
    "seedance_2_0",
    "cinematic_studio_3_0",
    "cinematic_studio_video",
)
_SKIP_VIDEO_MODELS = frozenset(
    {"clipify", "higgsfield_preset", "marketing_studio_video"}
)


def pick_video_model(preferred: str, catalog: list[str]) -> str:
    """Choose a generate_video model that exists in the live MCP catalog."""
    if not catalog:
        return preferred
    catalog_set = set(catalog)
    if preferred in catalog_set:
        return preferred
    for candidate in _PREFERRED_VIDEO_MODELS:
        if candidate in catalog_set:
            return candidate
    for model_id in catalog:
        if model_id not in _SKIP_VIDEO_MODELS:
            return model_id
    return catalog[0]


def clamp_duration(
    seconds: int,
    *,
    allowed: list[int] | None = None,
    duration_range: dict[str, Any] | list | None = None,
) -> int:
    wanted = int(seconds)
    if allowed:
        values = [int(value) for value in allowed]
        if wanted in values:
            return wanted
        return min(values, key=lambda value: abs(value - wanted))
    if isinstance(duration_range, dict):
        low = duration_range.get("min", duration_range.get("minimum"))
        high = duration_range.get("max", duration_range.get("maximum"))
        if low is not None:
            wanted = max(wanted, int(low))
        if high is not None:
            wanted = min(wanted, int(high))
        return wanted
    if isinstance(duration_range, list) and len(duration_range) >= 2:
        low, high = int(duration_range[0]), int(duration_range[1])
        return min(max(wanted, min(low, high)), max(low, high))
    return wanted


def _model_ids_from_explore(result: dict[str, Any]) -> list[str]:
    payload = result.get("structuredContent")
    if not isinstance(payload, dict):
        payload = result
    items = payload.get("items") or []
    ids: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("model_id")
        if isinstance(model_id, str) and model_id.strip():
            ids.append(model_id.strip())
    return ids


def _deep_video_url(obj: Any, *, depth: int = 0) -> str | None:
    if depth > 8 or obj is None:
        return None
    if isinstance(obj, str) and obj.startswith("http"):
        path = obj.split("?", 1)[0].lower()
        if path.endswith(".mp4") or path.endswith(".webm") or "video" in path:
            return obj
        return None
    if isinstance(obj, dict):
        for key in (
            "video_url",
            "output_url",
            "download_url",
            "mp4_url",
            "url",
            "uri",
            "href",
        ):
            value = obj.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for value in obj.values():
            found = _deep_video_url(value, depth=depth + 1)
            if found:
                return found
    if isinstance(obj, list):
        for item in obj:
            found = _deep_video_url(item, depth=depth + 1)
            if found:
                return found
    return None


class HiggsfieldVideoProvider:
    """VideoProvider adapter — nodes never import this module directly."""

    TOOL_NAME = "generate_video"
    STATUS_TOOL = "job_status"
    DISPLAY_TOOL = "job_display"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        client: HiggsfieldMcpClient | None = None,
        timeout_seconds: float = 300.0,
        poll_interval_seconds: float = 5.0,
        model: str = DEFAULT_VIDEO_MODEL,
    ) -> None:
        self._timeout = timeout_seconds
        self._poll_interval = poll_interval_seconds
        self._model = model or DEFAULT_VIDEO_MODEL
        self._client = client or HiggsfieldMcpClient(
            base_url=base_url, api_key=api_key, timeout_seconds=timeout_seconds
        )

    def capabilities(self) -> set[str]:
        return {"frame_conditioning", "text_to_video"}

    async def _video_catalog(self) -> list[str]:
        try:
            result = await self._client.call_tool(
                "models_explore",
                {"action": "list", "type": "video", "limit": 50},
            )
        except AppError:
            return []
        result = _unwrap_tool_result(result)
        if result.get("isError"):
            return []
        return _model_ids_from_explore(result)

    async def _model_constraints(self, model_id: str) -> dict[str, Any]:
        try:
            result = await self._client.call_tool(
                "models_explore",
                {"action": "get", "model_id": model_id},
            )
        except AppError:
            return {}
        result = _unwrap_tool_result(result)
        if result.get("isError"):
            return {}
        payload = result.get("structuredContent")
        if not isinstance(payload, dict):
            payload = result
        durations = payload.get("durations")
        allowed = (
            [int(value) for value in durations] if isinstance(durations, list) else None
        )
        return {
            "durations": allowed,
            "duration_range": payload.get("duration_range"),
        }

    async def generate_clip(self, req: GenerateClipRequest) -> GenerateClipResult:
        schema = await self._client.tool_input_schema(self.TOOL_NAME)
        catalog = await self._video_catalog()
        model = pick_video_model(self._model, catalog)
        constraints = await self._model_constraints(model) if catalog else {}
        duration = clamp_duration(
            int(req.duration_seconds),
            allowed=constraints.get("durations"),
            duration_range=constraints.get("duration_range"),
        )
        arguments = fit_generate_video_arguments(
            req.model_copy(update={"duration_seconds": duration}),
            model=model,
            schema=schema,
        )
        log_json(
            logger,
            "higgsfield_generate_video_arguments",
            model=(arguments.get("params") or {}).get("model")
            if isinstance(arguments.get("params"), dict)
            else arguments.get("model"),
            duration=duration,
            keys=sorted(str(key) for key in arguments),
            param_keys=sorted(str(key) for key in (arguments.get("params") or {}))
            if isinstance(arguments.get("params"), dict)
            else [],
        )
        result = await self._client.call_tool(self.TOOL_NAME, arguments)
        result = _unwrap_tool_result(result)
        _raise_if_tool_error(result)
        if not await self._extract_video_bytes(result):
            result = await self._await_job(result)

        video_bytes = await self._extract_video_bytes(result)
        if not video_bytes:
            summary = summarize_mcp_result(result)
            log_json(
                logger,
                "higgsfield_missing_video_payload",
                summary=summary,
            )
            raise AppError(
                "PROVIDER_INVALID_RESPONSE",
                f"Higgsfield MCP response missing video payload ({summary})",
                http_status=502,
            )

        cost = float(result.get("cost_usd") or 0.0)
        seed = result.get("seed", req.seed)
        return GenerateClipResult(
            video_bytes=video_bytes,
            cost_usd=cost,
            provider_id="higgsfield-mcp",
            seed=seed if seed is None or isinstance(seed, int) else req.seed,
        )

    async def _extract_video_bytes(self, result: dict[str, Any]) -> bytes:
        raw = result.get("video_bytes")
        if isinstance(raw, (bytes, bytearray)):
            return bytes(raw)

        video_url = _deep_video_url(result) or _first_video_url(result)
        if video_url:
            return await self._download(video_url)

        video_b64 = result.get("video_base64") or result.get("content")
        if isinstance(video_b64, list):
            for part in video_b64:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "resource":
                    blob = part.get("blob") or part.get("text")
                    if isinstance(blob, str) and blob:
                        video_b64 = blob
                        break
                    uri = (part.get("resource") or {}).get("uri") if isinstance(
                        part.get("resource"), dict
                    ) else part.get("uri")
                    if isinstance(uri, str) and uri.startswith("http"):
                        return await self._download(uri)
        if isinstance(video_b64, str) and video_b64:
            import base64

            try:
                return base64.b64decode(video_b64)
            except Exception as exc:
                raise AppError(
                    "PROVIDER_INVALID_RESPONSE",
                    "Higgsfield MCP returned invalid base64 video",
                    http_status=502,
                ) from exc
        return b""

    async def _download(self, url: str) -> bytes:
        async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                raise AppError(
                    "PROVIDER_RETRYABLE",
                    f"Failed to download generated video: {exc}",
                    http_status=503,
                ) from exc
        if response.status_code >= 400:
            raise AppError(
                "PROVIDER_INVALID_RESPONSE",
                f"Video download HTTP {response.status_code}",
                http_status=502,
            )
        if not response.content:
            raise AppError(
                "PROVIDER_INVALID_RESPONSE",
                "Downloaded video was empty",
                http_status=502,
            )
        return response.content

    async def _await_job(self, submitted: dict[str, Any]) -> dict[str, Any]:
        job_id = extract_job_id(submitted)
        if not job_id:
            return submitted
        log_json(logger, "higgsfield_job_submitted", provider_job_id=job_id)
        latest = submitted
        last_status = ""
        attempts = max(1, int(self._timeout / max(self._poll_interval, 0.01)))
        for _ in range(attempts):
            status_result = _unwrap_tool_result(
                await self._client.call_tool(self.STATUS_TOOL, {"job_id": job_id})
            )
            _raise_if_tool_error(status_result)
            latest = {**submitted, **status_result}
            status = _status_of(latest)
            if status != last_status:
                log_json(
                    logger,
                    "higgsfield_job_status",
                    provider_job_id=job_id,
                    status=status or "unknown",
                )
                last_status = status
            if status in _FAILED:
                raise AppError(
                    "PROVIDER_REJECTED",
                    f"Higgsfield job {job_id} {status}: {summarize_mcp_result(latest)}",
                    http_status=502,
                )
            if status in _DONE or _deep_video_url(latest) or _first_video_url(latest):
                try:
                    display = _unwrap_tool_result(
                        await self._client.call_tool(
                            self.DISPLAY_TOOL, {"job_id": job_id}
                        )
                    )
                    _raise_if_tool_error(display)
                    latest = {**latest, **display}
                except AppError:
                    pass
                log_json(
                    logger,
                    "higgsfield_job_ready",
                    provider_job_id=job_id,
                    status=status or "ready",
                )
                return latest
            await asyncio.sleep(self._poll_interval)
        raise AppError(
            "PROVIDER_RETRYABLE",
            f"Higgsfield job {job_id} timed out: {summarize_mcp_result(latest)}",
            http_status=503,
        )


def _first_video_url(result: dict[str, Any]) -> str | None:
    for key in ("video_url", "url", "download_url"):
        value = result.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    return None
