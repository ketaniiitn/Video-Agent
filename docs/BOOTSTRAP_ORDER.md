# Bootstrap Order

The order this workspace was actually built in, and why — useful if you extend this pattern
to Sales Agent / SQL Agent later, or explain it to someone else.

## Phase 0 — Ground truth, no rules yet
`PROJECT_MEMORY.md` → `docs/architecture/platform-spec-summary.md` →
`docs/architecture/video-agent-prd-summary.md` → `docs/glossary.md`

Why first: everything else quotes or references these. Writing them last would mean
rewriting rules once the summaries existed anyway.

## Phase 1 — Always-Apply guardrail rules
`.cursor/rules/00` through `04`

Why second: these load on *every* subsequent Cursor conversation, including the ones where
you're still writing docs and ADRs. Getting the non-negotiables, routing rule, termination
model, and scope guardrails in early means nothing written after this point can drift from
them unnoticed.

## Phase 2 — Architectural decisions, locked before code exists
`docs/architecture/adr/template.md` → ADR-0001 through ADR-0005 →
`docs/architecture/system-architecture.md` → `docs/architecture/data-model.md`

Why third: locking decisions (sequential generation, LiteLLM gateway, checkpointing
strategy, provider abstraction, deferred vector storage) *before* any code exists means
Claude never has to reverse-engineer intent from an implementation, and never proposes an
architecture that contradicts a decision already made.

## Phase 3 — Folder-scoped domain rules
`.cursor/rules/10` through `18`, then `90`–`91`

Why fourth: these encode the ADRs and PRD mechanics into per-folder conventions
(LangGraph, FastAPI, DB/RLS, Redis, provider abstraction, the pipeline itself,
observability, testing, prompts). They reference the ADRs from Phase 2, so writing them
after the ADRs exist means no forward references to decisions not yet written down.

## Phase 4 — Process: how work gets planned and done
`docs/workflows/*.md` → `docs/checklists/*.md` → `docs/playbooks/*.md`

Why fifth: process docs are most useful once there's something (rules, ADRs) to point at
from within them — several playbooks and checklists directly cite specific rule files.

## Phase 5 — Prompt library
`.cursor/prompts/*.md`

Why sixth: these are thin wrappers that invoke the workflows/rules by reference — writing
them last means they can cite real file paths instead of placeholders.

## Phase 6 — Tooling
`.cursor/mcp.json` → `.cursorignore` → `.env.example` → `README.md`

Why last: purely mechanical config, no project-understanding dependency — but `README.md`
does forward-reference the whole tree, so it has to come after everything else exists.

## Phase 7 — First real prompt

Only now do you write the first line of application code, using
`docs/workflows/planning-workflow.md` plus `.cursor/prompts/plan-feature.md` to plan
`M1`: job lifecycle + story planning + continuity bible (per the PRD's milestone table).
Every rule, ADR, and doc above loads automatically from that first prompt onward — this is
the point the investment in Phases 0–6 starts paying for itself.

## Phase 8 — Milestone implementation (Aug 2026)

Built in order, each with a superpowers spec + plan:

1. **M1** — job lifecycle, story planning, continuity bible, idempotency, RLS, checkpoints
2. **M3a** — sequential shot generation, frame chaining, Higgsfield MCP provider
3. **M3b + M4 + M5** — assemble, deliver, QC/repair, observability, CI gates
4. **Local test console** — same-origin dev UI at `GET /`

See `README.md` for current status and local setup. Specs live in `docs/superpowers/specs/`.
