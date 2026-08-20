# M1 Job Lifecycle + Story Planning + Continuity Bible — Design

**Date:** 2026-08-03  
**Status:** Implemented  
**Scope:** M1 only (PRD milestones M1–M2 planning slice): job API, `plan_story`, `lock_continuity_bible`. Shot generation, QC, assemble, and Higgsfield are out of scope.

## Decisions locked in brainstorming

| Topic | Choice |
|---|---|
| Scope | M1 only |
| Model calls | LiteLLM gateway client + injectable fake; stub fixture JSON when proxy URL unset |
| Tenancy | `X-Tenant-Id` header; `SET LOCAL app.tenant_id` for RLS; no real auth yet |
| Graph execution | In-process `asyncio.create_task` after `202` |
| Structure | Thin vertical slice (Approach 1) |

## 1. Architecture

### M1 graph

```
POST /jobs
  → mint trace_id
  → validate headers
  → feature flag (global env; before idempotency / job row)
  → idempotency (Postgres SoT + Redis fast path)
  → create job QUEUED → 202
  → asyncio task: RUNNING
       plan_story                 [checkpoint]
         → lock_continuity_bible  [checkpoint]
              → END → harness SUCCESS → API BIBLE_LOCKED
```

### Status mapping

Harness states are canonical. The API exposes job-facing labels. The Postgres enum includes every value from the first migration.

| Harness state | Job status via API | M1 producer? |
|---|---|---|
| (non-terminal) | `QUEUED`, `RUNNING` | Yes |
| `SUCCESS` | `BIBLE_LOCKED` | Yes |
| `PARTIAL` | `PARTIAL` | Yes (budget cap) |
| `FAILED` | `FAILED` | Yes |
| `ESCALATED` | `ESCALATED` | Enum only (optional stub hook) |
| `FAILED_NO_PROGRESS` | `FAILED_NO_PROGRESS` | **No** — reserved for M4 QC/repair loop |

**`FAILED_NO_PROGRESS` in M1:** unreachable. The graph is linear with no harness-level “repeat this step” loop. Gateway retries exhausted inside one node resolve to `FAILED`. The enum value exists so M3+ does not require an Alembic enum migration.

### Non-negotiables in this slice

- Checkpoint after every node (Postgres-backed LangGraph checkpointer).
- Hard budget caps (iterations / wall-clock / tokens / USD) on every run.
- Idempotency key on `POST /jobs`.
- Untrusted user prompt is data only (delimited), never instructions.
- Planning behaviour behind feature flag `FEATURE_STORY_PLANNING` (global env).
- Migrations expand/contract; RLS on every tenant-scoped table including checkpointer storage.

### Feature flag placement

Evaluated at the **API layer** before consuming the idempotency key and before creating a job row. Flagged-off → **`403 FEATURE_DISABLED`** with `trace_id`; no hung empty job.

### Checkpointer storage

LangGraph Postgres checkpoint table(s) are first-class in the data model: carry `tenant_id`, RLS via `app.tenant_id`. No framework-owned exception to `12-database-rls.mdc`.

## 2. Components & data model

### Package layout

```
app/
  main.py
  config.py
  api/
    jobs.py                 # POST /jobs, GET /jobs/{id}, POST /jobs/{id}/resume
    deps.py
    errors.py               # { code, message, trace_id }
  graph/
    state.py
    compile.py
    budgets.py
  nodes/
    plan_story.py
    lock_continuity_bible.py
  gateway/
    client.py               # LiteLLM by alias; stub if URL unset
    protocols.py
  db/
    models.py
    session.py
    rls.py
  cache/
    idempotency.py
    progress.py
    locks.py                # lock:{job_id}
  prompts/
    registry.py             # local fallback until Langfuse wired
    story_plan.py
    continuity_bible.py
migrations/
tests/
```

### Rule change required

Add always-applied `.cursor/rules/05-failure-ladder.mdc` encoding retry → fallback (alternate model within alias group) → circuit break → degrade → fail honestly. Applies to model calls via `app/gateway/**` and provider calls via `app/providers/**`. Keep `14-provider-abstraction-mcp.mdc` for MCP/capability negotiation only — so the gateway folder does not fall between folder-scoped globs.

### Tables (M1 migration)

All tenant-scoped tables have `tenant_id` + RLS unless noted.

| Table | Key columns / notes |
|---|---|
| `tenants` | RLS root |
| `jobs` | Full status enum; `prompt`; `trace_id`; caps: `budget_max_usd`, `budget_max_tokens`, `budget_max_iterations`, `budget_max_wall_clock_seconds`; used: `budget_used_usd`, `budget_used_tokens`, `budget_used_iterations`; `started_at` for wall-clock |
| `story_plans` | `job_id`, `beats_json` (exactly 4 beats, 40s) |
| `continuity_bibles` | `bible_json`, `locked_at` — immutable once set |
| `idempotency_keys` | `UNIQUE(tenant_id, key)`; `request_hash`; `job_id`; `expires_at` |
| LangGraph checkpoint tables | Must include `tenant_id` + RLS; name aligned with LangGraph Postgres saver |
| `cost_ledger` | **Deferred to M3** |

Deferred to M3+: `shots`, `qc_scores`.

### Idempotency

- **Postgres is the source of truth** for dedup (`UNIQUE(tenant_id, key)`). On unique violation → look up and return the existing job.
- Redis `idem:{tenant_id}:{key}` is the fast path; **TTL = 24 hours**.
- `request_hash` = hash of normalized body (prompt + budget). Same key + same hash → replay (`job_id` + current status). Same key + different hash → **422 `IDEMPOTENCY_KEY_REUSE_MISMATCH`**. Replay does not re-run the graph, re-consume budget, or insert a second job.

### Budget vs cost_ledger

Running totals live on `jobs` (`budget_used_usd`, `budget_used_tokens`, `budget_used_iterations`), updated inline after each gateway call. Wall-clock is derived from `started_at`. `graph/budgets.py` reads those fields (mirrored in graph state). **`cost_ledger` is not required for M1 budget enforcement.**

### Domain schemas

- **`StoryPlan`:** 4 beats (`setup`, `development`, `turn`, `resolution`); each `duration_seconds=10`, action text, camera note.
- **`ContinuityBible`:** character, wardrobe, location, lighting, palette, lens. Immutable after `locked_at`.

### Prompt registry interim

M1 ships a local name+version registry when Langfuse credentials are unset. This is a deliberate, time-boxed deviation from `18-prompt-engineering.mdc`. Switch trigger: Langfuse credentials configured. Schema-constrained JSON and untrusted-content delimiting still apply. Recorded in `PROJECT_MEMORY.md`.

### API surface

| Method | Behaviour |
|---|---|
| `POST /jobs` | Headers: `Idempotency-Key`, `X-Tenant-Id`. Body: `{ prompt, budget? }`. Feature flag → idempotency → create → `202 { job_id, status }`. |
| `GET /jobs/{id}` | Tenant-scoped. Status, budgets used, `story_plan` and/or `continuity_bible` when present (including `PARTIAL` with plan only). Reads **Postgres only**. |
| `POST /jobs/{id}/resume` | Manual / test kick. See §3. |

## 3. Data flow & error handling

### Trace first

Mint `trace_id` / Langfuse trace (or local stand-in) **before** header validation and feature-flag check. A `FEATURE_DISABLED` response still carries `trace_id`.

### Feature flag scope

Global env toggle `FEATURE_STORY_PLANNING`. Ordering: trace → validate headers → flag → idempotency → job. (If the flag later becomes per-tenant, establish tenant/RLS before the flag lookup.)

### Happy path

1. Client `POST /jobs`.
2. Mint `trace_id`.
3. Validate `Idempotency-Key` and `X-Tenant-Id` (missing → `400`).
4. Feature flag; if off → `403 FEATURE_DISABLED` (no idempotency write, no job).
5. Compute `request_hash`; Postgres insert into `idempotency_keys` (+ Redis set). Unique hit → hash match returns existing job; mismatch → `422`.
6. Insert `jobs` (`QUEUED`); write Redis `progress:{job_id}`; return `202`.
7. Background task: `RUNNING`; invoke graph with `thread_id=job_id`.
8. `plan_story`: registry prompt; gateway `reasoning-high` (failure ladder); validate `StoryPlan`; persist + budget update (one transaction); checkpoint; progress.
9. `lock_continuity_bible`: same for bible; set `locked_at`; persist + budget; checkpoint; progress.
10. END → harness `SUCCESS` → API `BIBLE_LOCKED`.

### Node write atomicity

Within each node: **domain persist + `jobs.budget_used_*` commit in one DB transaction**; LangGraph checkpoint is written only **after** that commit succeeds. On resume, if the checkpoint is missing the node re-runs; domain writes are **idempotent** (upsert by `job_id`) so a crash after commit but before checkpoint does not duplicate rows or double-bill. Checkpoint is never ahead of durable domain state.

### Resume (both mechanisms)

1. **Startup sweep** (app lifespan): find non-terminal `RUNNING` / stuck `QUEUED` jobs and re-invoke each `thread_id` — operational guarantee after process crash.
2. **`POST /jobs/{id}/resume`**: deterministic test / manual kick.

Before invoke: acquire Redis `lock:{job_id}` (non-blocking); release when the task finishes or on error. Concurrent sweep + manual → one winner; the other gets **`409 JOB_LOCKED`** immediately (no wait).

Resume on a **terminal** status (`BIBLE_LOCKED`, `PARTIAL`, `FAILED`, `FAILED_NO_PROGRESS`, `ESCALATED`) → **409 `JOB_ALREADY_TERMINAL`** (not a silent checkpointer no-op).

### Progress Redis keys

`progress:{job_id}` is **write-only in M1** (forward-looking for later SSE). `GET /jobs/{id}` does not read it. TTL = 24h; **delete on terminal status**.

### Error → status

| Condition | Harness | API status |
|---|---|---|
| Both nodes succeed | `SUCCESS` | `BIBLE_LOCKED` |
| Budget cap before/during a node | `PARTIAL` | `PARTIAL` — expose checkpointed artifacts |
| Non-retryable / ladder exhausted | `FAILED` | `FAILED` |
| Human/ops stop (optional stub) | `ESCALATED` | `ESCALATED` |
| Same failure twice in harness loop | `FAILED_NO_PROGRESS` | No M1 producer |

### Pre-graph API errors

| Condition | Response |
|---|---|
| Missing `Idempotency-Key` | `400` |
| Missing/invalid `X-Tenant-Id` | `400` |
| Feature off | `403 FEATURE_DISABLED` (+ `trace_id`) |
| Idempotency key reuse, different body | `422 IDEMPOTENCY_KEY_REUSE_MISMATCH` |
| Wrong tenant / unknown job | `404` |
| Resume on terminal job | `409 JOB_ALREADY_TERMINAL` |
| Resume while lock held | `409 JOB_LOCKED` |

All errors use `{ code, message, trace_id }`.

### Untrusted content

User `prompt` appears only inside a delimited data block in the story-plan messages; never concatenated into system instructions.

### Gateway

- Code references aliases only (`reasoning-high` for both M1 nodes).
- Failure ladder from `05-failure-ladder.mdc`.
- Injectable fake in tests; stub returns fixture JSON when `LITELLM_PROXY_URL` unset.

## 4. Testing

**Stack:** `pytest` + `pytest-asyncio`. No live LiteLLM / Langfuse / Higgsfield. Gateway faked; Redis via fakeredis or testcontainer; Postgres with RLS (testcontainer or equivalent).

| Area | Assertions |
|---|---|
| Happy path | `POST` → `BIBLE_LOCKED`; 4×10s plan + locked bible on `GET` |
| Idempotency replay | Same key + hash → same `job_id`, one row, graph not re-run |
| Idempotency mismatch | Different prompt → `422` |
| Feature flag off | No idempotency row, no job; `trace_id` + `403 FEATURE_DISABLED` |
| Budget → `PARTIAL` | Cap after `plan_story`; plan present, bible absent |
| Node `FAILED` | Ladder exhausted → `FAILED` |
| Resume (endpoint) | Kill after plan commit; resume → bible locks; plan not re-billed |
| Resume (sweep) | Seed mid-graph `RUNNING`; lifespan resumes |
| Resume guards | Terminal → `409 JOB_ALREADY_TERMINAL`; concurrent → `lock:{job_id}` |
| RLS | Tenant A cannot `GET` tenant B’s job |
| Schema validation | Invalid JSON → retry then `FAILED` |
| Untrusted prompt | User text only in delimited data block |

**Not required in M1:** `FAILED_NO_PROGRESS` producer tests; live eval/cost CI gates (placeholders only); Higgsfield tests.

## 5. Docs / repo updates accompanying implementation

- Update `docs/architecture/data-model.md` with checkpointer tables, `request_hash`, budget_used columns, deferred `cost_ledger`.
- Update `docs/architecture/system-architecture.md` to note M1 subgraph ends at `BIBLE_LOCKED`.
- Add `.cursor/rules/05-failure-ladder.mdc`.
- Note local prompt-registry interim in `PROJECT_MEMORY.md`.
- Align `.env.example` with `FEATURE_STORY_PLANNING` if missing.

## 6. Out of scope (v1 / later milestones)

Dialogue, non-40s durations, user reference characters, voiceover, editing timeline, >1080p (`04-scope-guardrails.mdc`). Shot generation, frame chaining, QC/repair, assemble/deliver, `cost_ledger`, Langfuse-as-primary prompt registry (until creds configured).
