#!/usr/bin/env bash
# Local OpenAI-compatible proxy (not LiteLLM). LiteLLM's proxy requires Prisma
# whenever DATABASE_URL is set; this repo's .env points DATABASE_URL at Neon.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="${PYTHON:-python3}"
fi
if [[ -z "${GEMINI_API_KEY:-}" ]]; then
  echo "Set GEMINI_API_KEY first: https://aistudio.google.com/apikey" >&2
  exit 1
fi
exec env -u DATABASE_URL -u DATABASE_URL_SWEEP \
  "$PY" "$ROOT/scripts/openai_compat_proxy.py"
