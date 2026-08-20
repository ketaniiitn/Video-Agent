# Platform Spec Summary

Condensed, human-readable reference. Source of truth: *Entermind Common Platform
Specification*, v1.0, 2 Aug 2026 (`Guidelines.pdf`). The `.cursor/rules/0X-*.mdc` files are
the machine-enforced version of this document — if this summary and a rule ever disagree,
the rule wins for code, this doc wins for understanding *why*.

## Canonical stack

| Layer | Choice |
|---|---|
| Language / API | Python 3.12, FastAPI (async) |
| LLM gateway | LiteLLM proxy — single egress for every model call |
| Models | Gemini, OpenAI, Claude — referenced only by logical alias |
| Orchestration | LangGraph — every agent is a compiled StateGraph |
| Observability | Langfuse — traces, generations, scores, prompt registry |
| Relational | PostgreSQL 16 — system of record, RLS per tenant |
| Vector | pgvector (default) or MongoDB Atlas, behind one protocol |
| Cache | Redis 7 — cache, locks, rate limits, idempotency, progress |

## Harness & loop

`observe → think → act → evaluate → repeat | terminate | escalate`. The harness owns
context, tools, budgets, and termination — the model is a component inside it, never the
controller.

| Termination condition | Outcome |
|---|---|
| Evaluator satisfied | SUCCESS |
| Budget exhausted (iterations/time/tokens/USD) | PARTIAL — best-so-far, flagged degraded |
| Same failure signature twice | FAILED_NO_PROGRESS — stop immediately |
| Non-retryable error / human trigger | FAILED / ESCALATED |

## Model routing aliases

`reasoning-high` (planning, SQL generation, critique) · `reasoning-fast` (routing,
classification, extraction) · `realtime-voice` (low-latency conversational turns) ·
`embed-default` (all embeddings) · `vision-default` (frame inspection, continuity QC).

## Failure ladder

Retry (backoff + jitter, retryable only, max 3) → Fallback (alternate model in the alias
group) → Circuit break (per dependency, 5 failures/30s) → Degrade (cached/stale/partial,
always flagged) → Fail honestly (what happened, what was preserved, what to do next).
Every error response carries a stable code + `trace_id`.

## Observability

Trace = one unit of work. Spans = graph nodes. Generations = LLM calls (model, tokens,
cost, prompt version). Logs are JSON keyed by `trace_id`. Never logged: credentials, raw
PII, full media payloads, row-level query results.

## Non-negotiables

Checkpoint after every node · hard budget caps (iterations/wall-clock/tokens/USD) ·
idempotency keys on every work-creating POST · untrusted content never issues instructions
· CI gates on eval regression >3% and cost regression >20% · migrations are
expand/contract, applied before deploy · every new agent behaviour behind a feature flag ·
model/prompt changes go to 10% of traffic first, promoted only after Langfuse scores hold.

## Video Agent repo (Aug 2026)

This repo implements the platform patterns above for the Video Agent product. Current
status: M1–M5 complete (see `README.md`). Deliberate gaps vs the full platform spec:

- Vector storage deferred (ADR-0005) — no pgvector/Mongo in this repo
- Jobs run in-process, not a separate worker queue
- Object storage is local disk; cloud bucket adapter not wired
- Auth is tenant header only; no API keys or OAuth yet
- Prompt rollout is feature-flag on/off, not 10% traffic splitting
