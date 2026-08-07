# Video Agent

One prompt becomes a continuous 40-second story — four 10-second shots with enforced
narrative and visual continuity. See `docs/architecture/video-agent-prd-summary.md` and
`docs/architecture/platform-spec-summary.md` for the condensed specs this project is built
against.

## Working in this repo with Cursor

Read `PROJECT_MEMORY.md` first — it explains how the `.cursor/rules/`, `docs/`, and
`.cursor/prompts/` layers fit together and what's durable vs. what's proposed-but-unbuilt.

Quick map:

```
.cursor/
  rules/        Cursor Project Rules (.mdc) — enforced conventions, split by scope
  prompts/      Reusable prompt templates (copy-paste or @-reference, not slash commands)
docs/
  architecture/ Condensed specs, proposed system architecture, proposed data model, ADRs
  playbooks/    Longer-form how-to for recurring situations
  checklists/   Tick-box checklists for PRs, new nodes, and pre-deploy
  workflows/    How to plan / decompose / implement / refactor in this repo
  glossary.md   Terms used throughout
PROJECT_MEMORY.md  Durable facts worth not re-explaining every session
```

## Status

**M1 + M3a implemented** — job lifecycle through continuity bible, plus sequential
shot generation and frame chaining gated by `FEATURE_SHOT_GENERATION`, ending at
`SHOTS_READY` with clips on local `MEDIA_ROOT`. Assemble, deliver, and QC/repair
are not wired yet.

Specs: `docs/superpowers/specs/2026-08-03-m1-job-lifecycle-design.md`,
`docs/superpowers/specs/2026-08-07-m3a-shot-generation-design.md` · Schema:
`docs/architecture/data-model.md`

## Stack (see `docs/architecture/platform-spec-summary.md` for full detail)

Python 3.12 · FastAPI (async) · LiteLLM gateway · LangGraph · Langfuse · PostgreSQL 16 ·
Redis 7 · Higgsfield MCP (video generation, behind a provider abstraction).
