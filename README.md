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

Greenfield — no application code yet. The Cursor workspace (rules, docs, ADRs, prompts) is
intentionally built first, so the first real implementation prompt already inherits full
context. See `docs/workflows/planning-workflow.md` for how to start the first feature.

## Stack (see `docs/architecture/platform-spec-summary.md` for full detail)

Python 3.12 · FastAPI (async) · LiteLLM gateway · LangGraph · Langfuse · PostgreSQL 16 ·
Redis 7 · Higgsfield MCP (video generation, behind a provider abstraction).
