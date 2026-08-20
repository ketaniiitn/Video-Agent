# System Architecture

Target LangGraph topology consistent with the PRD's 6 stages and the platform's
checkpoint-per-node rule. Update this file whenever the graph topology actually changes —
it's the map Claude (and you) should trust over re-deriving it from code each time.

## M1–M5 implemented subgraph

As of M3b/M4/M5 (`app/graph/compile.py`):

```
plan_story → lock_continuity_bible
  → [FEATURE_SHOT_GENERATION off] → END (API BIBLE_LOCKED)
  → [flag on]
       generate_shot_n → chain_frame_n
         → [FEATURE_QC_REPAIR off] → next n or assemble
         → [flag on] qc_score_n
              --pass--> next n or assemble
              --fail, repairs<2--> repair_shot_n → chain_frame_n → qc_score_n
              --fail, repairs==2--> flag_degraded_n → next n or assemble
       → [FEATURE_ASSEMBLE_DELIVER on] assemble → deliver → END
```

- Planning nodes checkpoint; when the shot flag is off the runner maps harness
  `SUCCESS` → `BIBLE_LOCKED`.
- When the shot flag is on and assemble is off, sequential generate+chain runs
  for beats 1–4; final success maps to `SHOTS_READY`.
- When assemble/deliver is on, success maps to `DELIVERED` and QC-cap
  exhaustion maps to `PARTIAL` with a stitched artifact and presigned URL.
- Provider calls go through `VideoProvider` (Higgsfield MCP adapter or fake) —
  never direct MCP from nodes (`ADR-0004`).
- Specs: `docs/superpowers/specs/2026-08-03-m1-job-lifecycle-design.md`,
  `docs/superpowers/specs/2026-08-07-m3a-shot-generation-design.md`,
  `docs/superpowers/specs/2026-08-20-m3b-m4-m5-design.md`.

## Full graph topology (text form)

```
plan_story
   -> lock_continuity_bible                       [checkpoint]
        -> generate_shot(n=1)                      [checkpoint, budget check]
             -> chain_frame(n=1)                   [checkpoint]
                  -> qc_score(n=1)                  [checkpoint]
                       --pass--> generate_shot(n=2) ... repeats through n=4
                       --fail, attempts<2--> repair_shot(n=1) -> qc_score(n=1)
                       --fail, attempts==2--> flag_degraded(n=1) -> generate_shot(n=2)
        -> assemble                                [checkpoint, requires all 4 shots resolved]
             -> deliver                            [checkpoint]
```

- **Sequential only** across `generate_shot(n)` — never fan these out in parallel
  (`.cursor/rules/15-video-pipeline.mdc`, ADR-0001).
- **Budget check** happens before every `generate_shot` / `repair_shot` call, evaluated
  against the job's iteration/time/token/USD caps. A cap trip resolves the job to
  `PARTIAL`, not `FAILED`, provided ≥1 shot succeeded.
- **QC/repair loop** is bounded per-shot at 2 repair attempts; cap exhaustion routes to
  `flag_degraded`, not to failing the job.
- **`assemble`** only runs once all 4 shot slots are resolved (success or flagged-degraded)
  — never on a partially-populated shot list without going through `flag_degraded` first.

## Implemented components (code map)

| Layer | Location | Notes |
|---|---|---|
| Graph compile + resume | `app/graph/compile.py` | Feature-flag wiring, tenant-aware checkpointer |
| Nodes | `app/nodes/*.py` | One module per graph node; factory pattern for DI |
| Job runner | `app/jobs/runner.py` | In-process asyncio; lock, sweep, status mapping |
| API | `app/api/jobs.py`, `artifacts.py`, `ui.py` | Jobs, artifacts, local test console |
| Gateway | `app/gateway/client.py`, `circuit.py` | LiteLLM proxy client + resilience ladder |
| Provider | `app/providers/higgsfield/`, `fake.py` | Higgsfield MCP adapter; fake for tests |
| Storage | `app/storage/local.py`, `presign.py` | Local disk + HMAC URLs |
| Media | `app/media/ffmpeg.py` | Stitch, frame extract |
| Cache | `app/cache/` | Redis idempotency, locks, progress |
| Observability | `app/observability/` | JSON logs, optional Langfuse HTTP tracer |
| Config | `app/config.py`, `config_validate.py` | Flag-aware fail-fast validation |
| CLI | `app/cli.py` | `python -m app seed-tenant` |

`qc_score` and `repair_shot` are **separate nodes** (checkpoint-per-node, repair resumable
independently of the QC call that triggered it).
