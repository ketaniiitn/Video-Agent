#!/usr/bin/env python3
"""Minimal OpenAI-compatible /chat/completions proxy for local Video Agent.

LiteLLM's proxy insists on Prisma whenever DATABASE_URL exists (Neon in this
repo's .env). This sidecar speaks the same HTTP contract the app already uses
and calls Google AI Studio with GEMINI_API_KEY. It is not imported by `app/`.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Google AI Studio currently serves Gemini 3.x; 2.0/2.5 Flash 404 for new keys.
DEFAULT_MODELS = [
    os.environ.get("GEMINI_MODEL") or "gemini-3.6-flash",
    "gemini-3-flash",
    "gemini-2.5-flash",
    "gemini-flash-latest",
]
_RETRY_NEXT = {400, 404, 429, 503}
PORT = int(os.environ.get("LLM_PROXY_PORT", "4000"))


def _gemini_url(model: str) -> str:
    return (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent"
    )


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(content or "")


def openai_to_gemini(messages: list) -> dict:
    system: list[str] = []
    contents: list[dict] = []
    for message in messages:
        role = message.get("role") or "user"
        text = _text(message.get("content"))
        if role == "system":
            system.append(text)
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text}]})
        else:
            contents.append({"role": "user", "parts": [{"text": text}]})
    if not contents:
        contents = [{"role": "user", "parts": [{"text": "Respond with JSON."}]}]
    body: dict = {
        "contents": contents,
        "generationConfig": {"responseMimeType": "application/json"},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
    return body


def call_gemini(api_key: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    last_error: RuntimeError | None = None
    seen: set[str] = set()
    for model in DEFAULT_MODELS:
        if model in seen:
            continue
        seen.add(model)
        for attempt in range(3):
            request = urllib.request.Request(
                _gemini_url(model),
                data=data,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": api_key,
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    sys.stderr.write(f"gemini ok model={model}\n")
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:800]
                last_error = RuntimeError(f"Gemini HTTP {exc.code} ({model}): {detail}")
                sys.stderr.write(f"{last_error}\n")
                if exc.code in {429, 503} and attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if exc.code not in _RETRY_NEXT:
                    raise last_error from exc
                break
    raise last_error or RuntimeError("Gemini request failed")


def gemini_to_openai(alias: str, gemini: dict) -> dict:
    candidates = gemini.get("candidates") or []
    text = ""
    if candidates:
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        text = "".join(str(part.get("text") or "") for part in parts)
    usage = gemini.get("usageMetadata") or {}
    tokens = int(usage.get("totalTokenCount") or 0)
    return {
        "id": "local-gemini",
        "object": "chat.completion",
        "model": alias,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
        "usage": {"total_tokens": tokens, "prompt_tokens": 0, "completion_tokens": 0},
        "response_cost": 0.0,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") in {"", "/health", "/healthz"}:
            self._send(200, {"status": "ok", "models": DEFAULT_MODELS})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": "not found"})
            return
        api_key = os.environ.get("GEMINI_API_KEY") or ""
        if not api_key:
            self._send(500, {"error": "GEMINI_API_KEY is not set"})
            return
        length = int(self.headers.get("Content-Length") or "0")
        try:
            incoming = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid JSON"})
            return
        alias = incoming.get("model") or "reasoning-high"
        try:
            gemini = call_gemini(api_key, openai_to_gemini(incoming.get("messages") or []))
            self._send(200, gemini_to_openai(alias, gemini))
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"proxy 502: {exc}\n")
            self._send(502, {"error": str(exc)})


def main() -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        sys.stderr.write("Set GEMINI_API_KEY first: https://aistudio.google.com/apikey\n")
        sys.exit(1)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    sys.stderr.write(
        f"local LLM proxy on http://127.0.0.1:{PORT}/chat/completions "
        f"({DEFAULT_MODELS[0]})\n"
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
