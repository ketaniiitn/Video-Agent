# ADR-0003: Postgres-backed LangGraph checkpointing, checkpoint after every node

**Status:** Accepted

## Context

The platform's non-negotiables require that a crash resume rather than restart, and that
state survives process restarts (this repo's Postgres is already the system of record).

## Decision

Every node in the Video Agent graph checkpoints via LangGraph's checkpointer, backed by
Postgres. No node is exempt, including cheap/fast nodes — consistency of the mechanism
matters more than shaving checkpoint overhead on any single node.

## Consequences

- Enables shot-level regeneration: state per shot is independently addressable and
  resumable, so fixing shot 3 doesn't require replaying shots 1, 2, and 4.
- Adds a Postgres write per node — acceptable given the harness's own wall-clock budget
  already assumes non-trivial per-shot latency (frame chaining, QC, potential repair).
- Requires the checkpoint schema to be considered part of the data model
  (`../data-model.md`) — don't treat it as internal LangGraph plumbing invisible to the rest
  of the system.

## Alternatives considered

- **In-memory / Redis-only checkpointing:** rejected as the durable record — Redis already
  carries a different job (progress polling, locks, idempotency) per
  `.cursor/rules/13-redis-usage.mdc`; conflating "fast ephemeral state" with "durable resume
  state" risks losing resumability on a Redis eviction or restart.
