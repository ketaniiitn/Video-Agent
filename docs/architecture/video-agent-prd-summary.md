# Video Agent PRD Summary

Condensed, human-readable reference. Source of truth: *Video Agent PRD*, v1.0, 2 Aug 2026
(`Video-Agent.pdf`).

## The problem

Text-to-video models generate isolated 5–10s clips well. Four clips from four prompts are
four unrelated clips — face, room colour, and story don't hold across shots. Generation is
solved; continuity is not.

## The six stages

1. **Plan the story** — one LLM pass produces a 4-beat arc (setup, development, turn,
   resolution) summing to exactly 40s.
2. **Lock a continuity bible** — canonical character, wardrobe, location, lighting, palette,
   lens language. Immutable for the life of the job.
3. **Generate shots sequentially** — via Higgsfield MCP behind a provider abstraction. Each
   prompt = bible + beat action + camera move.
4. **Chain the frames** — the final frame of shot *n* conditions shot *n+1*, carrying
   identity forward.
5. **QC and repair** — a vision model scores each shot against the bible; failures
   regenerate that shot only, capped at 2 attempts.
6. **Assemble and deliver** — ffmpeg stitch, normalise, optional music bed, presigned URLs.

## The deliberate trade-off

Shots run sequentially, not in parallel. Parallel is ~4× faster but breaks frame chaining,
and frame chaining is what makes the product work. Latency was traded for the core value
proposition. See `adr/0001-sequential-shot-generation.md` — do not "fix" this later without
reopening that ADR.

## Success metrics (v1 target)

| Metric | Target |
|---|---|
| Story coherence (human, 1–5) | ≥ 4.0 |
| Jobs with continuity score ≥ 0.75 | ≥ 85% |
| p90 end-to-end job latency | ≤ 8 min |
| Jobs failing with zero deliverable | < 1% |

## Resilience

Never returns nothing — a stitched partial with a working resume if one shot succeeded ·
resume, don't restart — completed shots never regenerated or re-billed · shot-level
regeneration — fix shot 3, leave 1/2/4 byte-identical · provider abstraction — capability
negotiation + failover so an API change isn't an outage.

## Key risks

| Risk | Mitigation |
|---|---|
| Provider can't hold identity across clips | Frame chaining + locked bible + QC loop |
| QC itself unreliable → wasted spend | Calibrate on labelled set; cap attempts |
| Repair loops blow the budget | Hard USD cap; no-progress detection |

## What's delivered

Stitched 40s MP4 + each 10s clip separately · thumbnail + extracted continuity frames ·
`StoryPlan` and `ContinuityBible` as machine-readable JSON · per-shot cost, model, seed,
prompt (every job reproducible).

## Milestones

| Milestone | PRD scope | Repo status |
|---|---|---|
| M1 | Job lifecycle, planning, continuity bible | **Done** — idempotency, RLS, checkpoints, API |
| M3a | Higgsfield MCP, sequential generation, frame chaining | **Done** — provider abstraction, shot persistence |
| M3b | Assembly, presigned delivery | **Done** — ffmpeg stitch, HMAC local URLs, artifacts API |
| M4 | QC loop, partial results, resume | **Done** — vision QC, ≤2 repairs, `PARTIAL` / degraded |
| M5 | Observability, cost caps, load + chaos | **Done** — JSON logs, Langfuse optional, CI gates, chaos tests |

Not yet built (post-v1 candidates): cloud object storage, separate worker process, music bed
in assemble, 10% prompt rollout, job listing API, production auth.

## Out of scope (v1)

Dialogue and lip-sync · durations other than 40s · user-supplied reference characters ·
voiceover · editing timeline · above 1080p.
