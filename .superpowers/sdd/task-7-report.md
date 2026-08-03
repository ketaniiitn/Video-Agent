# Task 7 report — Graph state, budgets, nodes, compile

## Status

Implemented the M1 LangGraph slice on `feat/m1-job-lifecycle`.

- Added typed graph state and hard cap checks for USD, tokens, iterations, and wall clock.
- Added `plan_story` and `lock_continuity_bible` nodes using only `reasoning-high` and versioned prompt registry templates.
- Domain rows and job budget usage commit in one transaction before node return, so LangGraph checkpoints only after the commit.
- Both domain writes short-circuit idempotently by `job_id`, preventing duplicate calls and billing after a commit/checkpoint crash window.
- Schema-invalid completions retry three times, account for every gateway call, mark the job `FAILED`, and raise `AppError("SCHEMA_INVALID")`.
- Budget exhaustion marks the job and graph outcome `PARTIAL` without calling the gateway.
- Compiled `plan_story → lock_continuity_bible → END` with conditional early termination on `PARTIAL`.
- Added `TenantAwarePostgresSaver`, which applies and resets `app.tenant_id` on the same locked cursor used for checkpoint reads/writes; unit resume uses `MemorySaver`.

## Verification

- `.venv/bin/pytest -q`: **41 passed, 5 skipped**
- IDE diagnostics: **no linter errors**
- Resume test proves a failed continuity-bible node resumes from the real memory checkpoint and leaves story-plan gateway call count at 1.

## Concern

`TEST_DATABASE_URL` was unset, so the five existing PostgreSQL/RLS tests were skipped and the production `TenantAwarePostgresSaver` was not integration-tested against PostgreSQL. SQLite-backed node transaction tests and `MemorySaver` resume tests passed.

## Critical/Important findings follow-up

- Each graph node now reloads authoritative `budget_used_*` values from the `jobs` row before its first budget check.
- Every schema attempt re-checks all hard caps immediately before calling the gateway.
- Every successful gateway response updates the node's in-memory budget state and atomically increments the persisted job usage before validation or another attempt.
- Reaching a cap between schema attempts returns `PARTIAL` with the current budget delta, marks the job `PARTIAL`, and makes no further gateway call.
- Schema exhaustion leaves every consumed attempt persisted before marking the job `FAILED`; a stale checkpoint retry reloads that usage from the job row.
- Story-plan and continuity-bible writes now use dialect-specific `INSERT ... ON CONFLICT DO UPDATE` statements on the unique `job_id` constraint (PostgreSQL in production, SQLite in node tests).
- Added regressions for stale-state hydration, cap exhaustion between attempts, graph-level next-node stopping after a tiny plan budget, and forced double invocation through both upsert paths.

## Follow-up verification

- `.venv/bin/pytest -q tests/graph tests/nodes`: **16 passed**
- `.venv/bin/pytest -q`: **47 passed, 5 skipped, 1 dependency deprecation warning**
- IDE diagnostics on changed Python files: **no linter errors**
- The five skipped tests still require `TEST_DATABASE_URL`; PostgreSQL/RLS integration coverage remains environment-gated.

## Important finding follow-up — atomic artifact and budget commit

- Successful story-plan and continuity-bible completions now increment `jobs.budget_used_*`
  and upsert their domain artifact in the same session transaction, followed by one commit.
- Schema-invalid attempts still commit their consumed usage immediately, preserving durable
  accounting when schema exhaustion marks the job `FAILED` or a hard cap returns `PARTIAL`.
- Budget hydration at node start, pre-call hard-cap checks, and dialect-specific atomic
  upserts remain unchanged.
- Added one-commit regressions for both successful node paths; each test also verifies the
  artifact and all three budget counters persisted.

### Verification

- `.venv/bin/pytest -q tests/graph tests/nodes`: **18 passed**
- `.venv/bin/pytest -q`: **49 passed, 5 skipped, 1 dependency deprecation warning**
- IDE diagnostics on changed Python and test files: **no linter errors**
- The five skipped tests still require `TEST_DATABASE_URL`.
