# M3a Shot Generation + Frame Chaining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the M1 graph so that after bible lock, sequential generate+chain for 4 shots lands at API status `SHOTS_READY`, using a VideoProvider abstraction (real MCP when configured, fake in tests) and local filesystem storage.

**Architecture:** Provider protocol in `app/providers/`; nodes never call MCP directly. Graph conditional on `FEATURE_SHOT_GENERATION`. Persist `shots` + `cost_ledger` with RLS. Clips/frames under `MEDIA_ROOT`.

**Tech Stack:** Existing M1 stack + ffmpeg (CLI) + MCP client for Higgsfield adapter.

**Spec:** `docs/superpowers/specs/2026-08-07-m3a-shot-generation-design.md`

## Global Constraints

- Sequential shots only; never parallel.
- Nodes call `VideoProvider` only — never Higgsfield MCP client.
- Checkpoint after every node; domain+budget one transaction before checkpoint.
- Completed shots never re-billed on resume.
- Fake provider in all automated tests; no live MCP in CI.
- `SHOTS_READY` for M3a success; `BIBLE_LOCKED` when flag off.
- Budget mid-loop / provider fail with ≥1 shot → `PARTIAL`.
- Local FS only in M3a (`MEDIA_ROOT`).
- Out of scope: assemble, deliver, QC/repair.

## File map

| Path | Responsibility |
|---|---|
| `app/domain/schemas.py` | Add `SHOTS_READY`, `ShotStatus`, shot DTOs |
| `migrations/versions/004_*.py` | Enum + shots + cost_ledger + RLS |
| `app/db/models.py` | ShotRow, CostLedgerRow |
| `app/config.py` | `feature_shot_generation`, `media_root` |
| `app/providers/**` | Protocol, fake, higgsfield adapter, registry |
| `app/storage/local.py` | Path helpers + write bytes |
| `app/media/ffmpeg.py` | Extract last frame |
| `app/nodes/generate_shot.py` | Generate node factory by beat_index |
| `app/nodes/chain_frame.py` | Chain node factory by beat_index |
| `app/graph/compile.py` | Wire conditional + shot loop |
| `app/graph/state.py` | `prior_frame_path`, `shot_index`, etc. |
| `app/jobs/runner.py` | Map SUCCESS→SHOTS_READY when shots path taken; terminal set |
| `app/api/jobs.py` | Include shots in GET response |
| Docs / `.env.example` | Sync |

---

### Task 1: Schema — `SHOTS_READY`, Shot models, migration

**Files:** Create migration `004_m3a_shots.py`; modify `app/domain/schemas.py`, `app/db/models.py`, `docs/architecture/data-model.md`, tests.

**Produces:** `JobStatus.SHOTS_READY`; `ShotStatus` enum (`PENDING`,`RUNNING`,`SUCCEEDED`,`FAILED`); models `Shot`, `CostLedger`; Alembic 004 with RLS + composite FKs.

- [ ] Add enum + models + migration (Postgres `ALTER TYPE ... ADD VALUE` in expand step)
- [ ] Unit tests for schema; model import smoke
- [ ] Commit: `feat: add SHOTS_READY status and shots/cost_ledger schema`

---

### Task 2: Config, local storage, ffmpeg helper

**Produces:** `Settings.feature_shot_generation`, `Settings.media_root`; `clip_path`/`frame_path` builders; `save_bytes`; `extract_last_frame(video_path, out_path)` (subprocess ffmpeg; fakeable).

- [ ] TDD path helpers + ffmpeg with monkeypatched subprocess
- [ ] Update `.env.example`
- [ ] Commit: `feat: add MEDIA_ROOT storage and frame extraction helpers`

---

### Task 3: VideoProvider protocol + fake + registry

**Produces:**

```python
class GenerateClipRequest(BaseModel):
    prompt: str
    duration_seconds: int = 10
    prior_frame_path: str | None = None
    seed: int | None = None

class GenerateClipResult(BaseModel):
    video_bytes: bytes
    cost_usd: float
    provider_id: str
    seed: int | None = None

class VideoProvider(Protocol):
    def capabilities(self) -> set[str]: ...  # includes "frame_conditioning"
    async def generate_clip(self, req: GenerateClipRequest) -> GenerateClipResult: ...
```

`FakeVideoProvider` returns tiny fixture mp4 bytes; tracks call order and prior frames. `build_provider(settings)` → Fake when URL/key empty, else Higgsfield adapter (stubbed until Task 4).

- [ ] Tests: fake capabilities, ordered calls, prior_frame passed
- [ ] Commit: `feat: add VideoProvider protocol and fake provider`

---

### Task 4: Higgsfield MCP adapter

**Produces:** `HiggsfieldMcpClient` + `HiggsfieldVideoProvider` implementing protocol. Map env `VIDEO_MCP_*` / legacy `HIGGSFIELD_*`. Document tool names used; if MCP tool schema unknown, implement thin HTTP/MCP JSON-RPC with clear interface and unit-test against mocked transport.

- [ ] Adapter unit tests with mocked transport (no network)
- [ ] Commit: `feat: add Higgsfield MCP VideoProvider adapter`

---

### Task 5: `generate_shot` + `chain_frame` nodes

**Produces:** Factory `make_generate_shot_node(beat_index, *, provider, session_factory, storage, ...)` and `make_chain_frame_node(beat_index, ...)`.

Behavior per spec: budget check; skip if shot already SUCCEEDED; capability check for n>1; upsert shot+ledger+budget one txn; chain extracts frame.

- [ ] Node tests with FakeVideoProvider + temp MEDIA_ROOT + SQLite session
- [ ] Commit: `feat: add generate_shot and chain_frame nodes`

---

### Task 6: Wire graph, runner status mapping, GET shots

**Produces:** Conditional after bible; loop 1–4; runner maps to `SHOTS_READY` when outcome success and shots completed (or state flag `shots_completed`); terminal includes `SHOTS_READY`; GET returns shots.

- [ ] Integration tests from Section 4 of spec (flag off/on, resume, budget, etc.)
- [ ] Commit: `feat: wire M3a shot loop to SHOTS_READY`

---

### Task 7: Docs + README

- [ ] Update system-architecture, data-model, README, PROJECT_MEMORY if needed
- [ ] Commit: `docs: record M3a shot generation subgraph`

---

## Spec coverage

| Spec item | Task |
|---|---|
| SHOTS_READY enum | 1 |
| shots + cost_ledger RLS | 1 |
| MEDIA_ROOT layout | 2 |
| VideoProvider + fake | 3 |
| Real MCP adapter | 4 |
| generate/chain nodes | 5 |
| Feature flag conditional | 6 |
| Resume / PARTIAL / tests | 5–6 |
| Docs | 7 |
