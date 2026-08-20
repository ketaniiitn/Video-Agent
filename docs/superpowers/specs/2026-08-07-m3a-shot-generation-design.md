# M3a Shot Generation + Frame Chaining — Design

**Date:** 2026-08-07  
**Status:** Implemented  
**Scope:** M3a only — provider abstraction, sequential `generate_shot` ×4, frame chaining, local filesystem storage. Assemble/deliver and QC/repair are out of scope.

## Decisions locked in brainstorming

| Topic | Choice |
|---|---|
| Slice | M3a (not full M3 with assemble/deliver) |
| Storage | Local filesystem for dev (`MEDIA_ROOT`) |
| Higgsfield | Real MCP when URL + API key set; injectable fake in all automated tests |
| Success status | API `SHOTS_READY` (harness `SUCCESS` for this slice) |
| Graph wiring | Extend same graph after bible lock; gate with `FEATURE_SHOT_GENERATION` |
| Structure | Provider package + looped shot nodes (Approach 1) |

## 1. Architecture

### Graph

```
plan_story → lock_continuity_bible
  → [FEATURE_SHOT_GENERATION off] → END (API BIBLE_LOCKED)
  → [flag on]
       generate_shot(1) → chain_frame(1)
       → generate_shot(2) → chain_frame(2)
       → generate_shot(3) → chain_frame(3)
       → generate_shot(4) → chain_frame(4)
       → END → harness SUCCESS → API SHOTS_READY
```

- Strictly sequential; never parallel (`ADR-0001`, `15-video-pipeline.mdc`).
- Checkpoint after every node.
- Budget check before every `generate_shot`.
- Continuity bible remains immutable once locked.

### Status mapping

| Condition | Harness | API |
|---|---|---|
| Flag off after bible | `SUCCESS` | `BIBLE_LOCKED` |
| All 4 shots + chains done | `SUCCESS` | `SHOTS_READY` |
| Budget mid-loop (≥1 shot OK) | `PARTIAL` | `PARTIAL` |
| Provider exhaust, no shots | `FAILED` | `FAILED` |
| Provider exhaust, ≥1 shot OK | `PARTIAL` | `PARTIAL` |

`SHOTS_READY` added to `job_status` enum via expand migration. Terminal set includes `SHOTS_READY`.

### Storage (dev)

`{MEDIA_ROOT}/{tenant_id}/{job_id}/shot_{n}.mp4` and `frame_{n}.jpg`. No cloud/presigned URLs in M3a.

## 2. Components & data model

### Packages

```
app/providers/
  protocols.py       # VideoProvider: capabilities, generate_clip(...)
  registry.py        # build_provider(settings) → real | fake
  fake.py
  higgsfield/
    mcp_client.py    # MCP transport only
    adapter.py       # implements VideoProvider
app/storage/local.py
app/media/ffmpeg.py  # extract last frame
app/nodes/generate_shot.py
app/nodes/chain_frame.py
```

Nodes call `VideoProvider` only — never the Higgsfield MCP client directly (`ADR-0004`).

### Schema (expand migrations)

- Enum value `SHOTS_READY` on `job_status`.
- `shots`: `job_id`, `tenant_id`, `beat_index` (1–4), `status`, `attempt_count`, `clip_path`, `frame_path`, `cost_usd`, provider identity fields, `seed`, `prompt`; unique `(job_id, beat_index)`; composite FK `(job_id, tenant_id)`; RLS.
- `cost_ledger`: `job_id`, `shot_id` nullable, `usd`, `tokens` nullable, provider, `created_at`; RLS.

### Feature flag

`FEATURE_SHOT_GENERATION` — evaluated on the **graph conditional after bible lock** (resume sees current setting). `FEATURE_STORY_PLANNING` at API unchanged.

### API

`GET /jobs/{id}` returns shot summaries when present: `beat_index`, `status`, `clip_path`, `frame_path`, `cost_usd`.

## 3. Data flow & error handling

1. M1 path through bible lock unchanged.
2. If flag off → END `BIBLE_LOCKED`.
3. For `n = 1..4`:
   - Budget check → `PARTIAL` if exceeded (keep completed shots).
   - `generate_shot(n)`: bible + beat + optional `prior_frame_path` → `VideoProvider.generate_clip` → write MP4 → upsert `shots` + `cost_ledger` + job budget in **one transaction** → checkpoint.
   - `chain_frame(n)`: ffmpeg last frame → `frame_path`; set `prior_frame_path` for next → checkpoint.
4. After shot 4 chain → `SHOTS_READY`.

Shot 1: no prior frame. Shots 2–4 require provider capability `frame_conditioning`; missing capability → fail honestly before billing shot 2+.

Resume: completed shots never regenerated/re-billed (checkpoint + upsert by `(job_id, beat_index)` + skip if already succeeded with clip on disk).

Provider failure ladder at abstraction layer. M3a has one video provider — fallback within class is a documented no-op until a second adapter exists (same pattern as deferred gateway rungs 2–4).

Idempotency, locks, startup sweep: same as M1; terminal statuses include `SHOTS_READY`.

## 4. Testing

Fake `VideoProvider` only in CI. Temp `MEDIA_ROOT`. ffmpeg mocked or fixture clips.

Required: flag off → `BIBLE_LOCKED`; happy path → `SHOTS_READY` with 4 clips/frames; sequential conditioning; resume no re-bill; budget mid-loop `PARTIAL`; provider exhaust → `FAILED`/`PARTIAL`; capability miss; idempotent upsert; nodes never import MCP client.

Manual smoke script for real MCP (not CI).

## 5. Out of scope

Assemble, deliver/presigned URLs, QC/repair (M4), music bed, dialogue/voiceover, non-40s, cloud storage.

## 6. Docs to update with implementation

- `docs/architecture/system-architecture.md` — M3a subgraph
- `docs/architecture/data-model.md` — `shots`, `cost_ledger`, `SHOTS_READY`
- `.env.example` — `FEATURE_SHOT_GENERATION`, `MEDIA_ROOT`
- `PROJECT_MEMORY.md` if interim provider/fallback notes needed
- `README.md` status
