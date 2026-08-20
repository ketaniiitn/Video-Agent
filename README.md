# Video Agent

One prompt becomes a continuous 40-second story — four 10-second shots with enforced
narrative and visual continuity. See `docs/architecture/video-agent-prd-summary.md` and
`docs/architecture/platform-spec-summary.md` for the condensed specs this project is built
against.

## Status

**M1 through M5 implemented** — the full v1 pipeline from job creation through delivery.
Happy path ends at `DELIVERED`; QC repair exhaustion or budget pressure can still produce a
`PARTIAL` stitched video instead of dropping shots.

| Milestone | Scope | Status |
|---|---|---|
| M1 | Job lifecycle, story planning, continuity bible, idempotency, RLS, checkpoints | Done |
| M3a | Sequential shot generation, frame chaining, Higgsfield MCP provider | Done |
| M3b | ffmpeg assemble, HMAC presigned download URLs, artifact serving | Done |
| M4 | Vision QC scoring, bounded repair loop (≤2 per shot), degraded flagging | Done |
| M5 | JSON logging, optional Langfuse, gateway resilience, CI eval/cost gates | Done |

Specs: `docs/superpowers/specs/2026-08-03-m1-job-lifecycle-design.md`,
`docs/superpowers/specs/2026-08-07-m3a-shot-generation-design.md`,
`docs/superpowers/specs/2026-08-20-m3b-m4-m5-design.md`,
`docs/superpowers/specs/2026-08-20-local-test-console-design.md`

Schema: `docs/architecture/data-model.md` · Graph: `docs/architecture/system-architecture.md`

---

## Local development

The API runs on your machine. Postgres is Neon, Redis is a cloud URL, LLMs go through a
local OpenAI-compatible proxy, and video generation goes through Higgsfield MCP. ffmpeg must
be installed on the host.

Jobs execute **inside the FastAPI process** (asyncio background tasks). There is no separate
worker. You need **two terminals**: one for the API, one for the LLM proxy.

### Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.12+ | `python3.12 -m venv .venv` |
| ffmpeg on `PATH` | `brew install ffmpeg` / `apt install ffmpeg` |
| Neon Postgres | Connection string with `?ssl=require` |
| Cloud Redis | `redis://` or `rediss://` URL |
| Google AI Studio key | Free tier for the local LLM proxy — https://aistudio.google.com/apikey |
| Higgsfield MCP | `VIDEO_MCP_API_KEY` from https://cloud.higgsfield.ai — video is **not** free |
| Tenant UUID | Any UUID you will send as `X-Tenant-Id` |
| HMAC secret | Any long random string for `PRESIGN_SECRET` (download URL signing) |

Optional: Langfuse keys, `DATABASE_URL_SWEEP`, `GATEWAY_FALLBACK_ALIASES`, legacy
`HIGGSFIELD_MCP_*` aliases.

### 1. Install

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure environment

```bash
cp .env.example .env
```

Fill every **REQUIRED** value in `.env`. Comments in that file explain each variable.
With all `FEATURE_*` flags set to `true` (the default in `.env.example`), startup validates:

- `DATABASE_URL` — Neon async URL (`postgresql+asyncpg://...?ssl=require`)
- `REDIS_URL` — cloud Redis
- `LITELLM_PROXY_URL` — `http://127.0.0.1:4000` (local proxy, already in example)
- `VIDEO_MCP_URL` + `VIDEO_MCP_API_KEY` — Higgsfield video generation
- `PRESIGN_SECRET` — random string for HMAC download URLs
- `TENANT_ID` — UUID for your dev tenant
- ffmpeg available on `PATH`

Generate a presign secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Generate a tenant UUID:

```bash
python -c "import uuid; print(uuid.uuid4())"
```

Disabled feature flags relax their provider requirements (e.g. with
`FEATURE_SHOT_GENERATION=false`, Higgsfield credentials are not required).

### 3. Database

```bash
alembic upgrade head
python -m app seed-tenant
```

`seed-tenant` idempotently inserts the `TENANT_ID` from `.env` into the `tenants` table.

### 4. Start the LLM proxy (terminal 2)

The app speaks to LiteLLM's OpenAI-compatible `/chat/completions` contract. For local dev
we run a minimal sidecar proxy (`scripts/openai_compat_proxy.py`) that forwards to Google
Gemini — **not** the LiteLLM CLI, which conflicts with Neon `DATABASE_URL` in this repo's
`.env`.

```bash
export GEMINI_API_KEY=your-ai-studio-key
./scripts/run_litellm.sh
```

Leave `LITELLM_MASTER_KEY` empty. The proxy listens on `http://127.0.0.1:4000`.
Gemini keys go into the proxy process only — never into Video Agent's `.env`.

`config/litellm.yaml` documents alias → model mappings for a full LiteLLM deployment;
the local sidecar ignores aliases and uses Gemini for all logical names.

### 5. Start the API (terminal 1)

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Startup fails fast if required env vars or ffmpeg are missing (`app/config_validate.py`).

### 6. Generate a video

**Local test console** — http://127.0.0.1:8000/

Enter a prompt, click Generate. The console reads `TENANT_ID` from `GET /ui/config`, sends
`X-Tenant-Id` and a fresh `Idempotency-Key`, polls every 2s, and plays the result via
blob URL (artifact endpoints require the tenant header).

**Swagger** — http://127.0.0.1:8000/docs

```bash
# Create job
curl -X POST http://127.0.0.1:8000/jobs \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -H "X-Tenant-Id: YOUR-TENANT-UUID" \
  -d '{"prompt": "A barista discovers a hidden door behind the espresso machine"}'

# Poll until DELIVERED, PARTIAL, or FAILED*
curl http://127.0.0.1:8000/jobs/{job_id} \
  -H "X-Tenant-Id: YOUR-TENANT-UUID"
```

**Health checks**

| Endpoint | Purpose |
|---|---|
| `GET /healthz` | Liveness |
| `GET /readyz` | Database, Redis, ffmpeg, config |

---

## What's implemented

### Pipeline (LangGraph)

```
plan_story → lock_continuity_bible
  → generate_shot(n) → chain_frame(n) → qc_score(n)
       pass → next shot or assemble
       fail, repairs < 2 → repair_shot(n) → chain_frame → qc_score
       fail, repairs == 2 → flag_degraded(n) → next shot or assemble
  → assemble → deliver
```

- **Checkpoint after every node** — crash resumes, never restarts (`AsyncPostgresSaver` + RLS).
- **Sequential shots only** — frame chaining requires strict ordering (ADR-0001).
- **QC threshold** — pass if score ≥ 0.75; repair cap = 2 per shot; exhaustion flags degraded.
- **Budget caps** — iterations, wall-clock, tokens, USD enforced before each generation/repair.
- **Terminal mapping** — harness `SUCCESS` → `DELIVERED` / `BIBLE_LOCKED` / `SHOTS_READY`;
  `PARTIAL` when any shot is degraded; `FAILED_NO_PROGRESS` on repeated identical failures.

### API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/jobs` | Create job (202). Requires `Idempotency-Key` + `X-Tenant-Id`. |
| `GET` | `/jobs/{id}` | Job detail, story plan, bible, shots, download URLs. |
| `POST` | `/jobs/{id}/resume` | Resume a non-terminal job (202). |
| `GET` | `/jobs/{id}/artifacts/{name}` | Serve clip/frame/assembled/thumbnail bytes (HMAC presigned). |
| `GET` | `/` | Local test console. |
| `GET` | `/ui/config` | Tenant ID + feature flags for the console. |

All error responses carry a stable `code`, human `message`, and `trace_id`.

### Platform mechanics

- **Idempotency** — Redis fast path + Postgres source of truth; `(tenant_id, key)` unique.
- **Tenant isolation** — Postgres RLS on every table with `tenant_id`; forced for all roles.
- **Job locking** — Redis lock per job; concurrent resume returns `409 JOB_LOCKED`.
- **Stale job sweep** — on startup, re-queues non-terminal jobs across all tenants.
- **Feature flags** — `FEATURE_STORY_PLANNING`, `FEATURE_SHOT_GENERATION`, `FEATURE_QC_REPAIR`,
  `FEATURE_ASSEMBLE_DELIVER` gate graph wiring and config validation.
- **Gateway resilience** — retry (3×), same-alias fallback, per-alias circuit (5 fails / 30s),
  cached JSON degrade, typed `AppError` (see `.cursor/rules/05-failure-ladder.mdc`).
- **Observability** — structured JSON logs with `trace_id`; optional Langfuse HTTP ingestion;
  prompt registry tries Langfuse first, falls back to local templates.
- **Storage** — local disk under `MEDIA_ROOT`; HMAC presigned URLs for download (no cloud bucket yet).
- **CLI** — `python -m app seed-tenant`.

### Tests & CI

```bash
# Unit + integration (no live Neon / Redis / Higgsfield / LLM)
python -m pytest tests --ignore=tests/db -q

# Optional Postgres RLS tests against Neon:
TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/db -q
```

GitHub Actions (`.github/workflows/ci.yml`): install, unit tests, eval/cost gates against
`tests/eval/baselines.json` (>3% eval drop or >20% cost rise fails the build).

Test coverage includes: full pipeline, idempotency under load, no-progress termination,
gateway circuit breaker, presign URLs, UI console, health endpoints, QC/assemble nodes,
chaos scenarios, and config validation.

---

## What can be improved

These are deliberate v1 shortcuts or gaps — not bugs — ordered by likely next priority:

| Area | Current state | Improvement |
|---|---|---|
| **Worker process** | Jobs run in-process via asyncio | Separate worker for production scale and isolation |
| **Object storage** | Local disk + HMAC URLs | S3/GCS adapter; `STORAGE_BUCKET` is reserved but unused |
| **LLM proxy** | Custom Gemini sidecar for local dev | Full LiteLLM proxy with alias routing, spend logs, fallbacks |
| **Music bed** | PRD mentions optional music in assemble | Not implemented; ffmpeg stitch is video-only |
| **Prompt rollout** | Feature flags on/off only | 10% traffic staging with Langfuse score comparison |
| **Auth** | Tenant header only; console has no login | API keys, OAuth, or session auth for non-dev use |
| **Job listing** | Single-job GET only | Paginated job history, filters, WebSocket progress |
| **Thumbnail** | Copies first frame JPEG | Proper frame extraction / poster generation via ffmpeg |
| **Video normalization** | Concat without re-encode policy docs | Explicit resolution/fps normalization to 1080p cap |
| **Eval baselines** | Placeholder values in `tests/eval/baselines.json` | Calibrated baselines from labelled QC set |
| **Success metrics** | Not measured in-app | p90 latency, coherence scores, continuity pass rate dashboards |
| **Second video provider** | Higgsfield MCP only (fake for tests) | Additional provider behind `VideoProvider` interface |
| **Langfuse depth** | HTTP ingestion + prompt fetch | Full span/generation/score hierarchy per node |
| **Vision QC input** | Text + schema scoring | Actual frame image upload to vision model for true visual QC |

---

## Out of scope (v1)

Per the PRD — do not add without an ADR override:

- Dialogue and lip-sync
- Durations other than exactly 40 seconds (4 × 10s)
- User-supplied reference characters
- Voiceover
- Editing timeline / manual re-ordering UI
- Resolution above 1080p

---

## Working in this repo with Cursor

Read `PROJECT_MEMORY.md` first — it explains how the `.cursor/rules/`, `docs/`, and
`.cursor/prompts/` layers fit together.

```
.cursor/
  rules/        Cursor Project Rules (.mdc) — enforced conventions, split by scope
  prompts/      Reusable prompt templates (copy-paste or @-reference)
docs/
  architecture/ Condensed specs, system architecture, data model, ADRs
  playbooks/    Longer-form how-to for recurring situations
  checklists/   Tick-box checklists for PRs, new nodes, and pre-deploy
  workflows/    How to plan / decompose / implement / refactor
  glossary.md   Terms used throughout
  superpowers/  Milestone specs and implementation plans
PROJECT_MEMORY.md  Durable facts worth not re-explaining every session
```
