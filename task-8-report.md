# Task 8 review findings — fixes

Scope: Critical + Important findings on Task 8 (job API, in-process runner,
resume, startup sweep), plus the Minor error-envelope fix. All five
regressions below were verified to actually reproduce against the
pre-fix code (temporarily reverted and re-run) before being fixed.

## Critical

### 1. FK order on `POST /jobs`

**Bug:** `resolve_idempotency` flushed the `idempotency_keys` row (which
FKs to `jobs`) before the `Job` row was ever added to the session — the
`Job` insert only happened later, and only on the non-replay branch. Under
real FK enforcement (always on in Postgres) this raises
`IntegrityError: insert or update on table "idempotency_keys" violates
foreign key constraint` on every single `POST /jobs`. SQLite's tests
didn't catch it because SQLite ignores FK constraints unless
`PRAGMA foreign_keys=ON` is set per connection, which the test fixture
didn't do.

**Fix:**
- `app/cache/idempotency.py`: `resolve_idempotency` now accepts an
  optional `job: Job` (the not-yet-persisted candidate row). When
  provided, `job` is added and flushed *before* the idempotency key row,
  both inside one `SAVEPOINT` (`session.begin_nested()`), so a conflicting
  key rolls both back together instead of leaving an orphaned job.
- `app/api/jobs.py`: `create_job` builds the candidate `Job` up front and
  passes it into `resolve_idempotency`. Replay/mismatch semantics are
  unchanged — verified by the existing `test_idempotency_replay_...` and
  `test_idempotency_mismatch_returns_422` tests, now running with FK
  enforcement on.
- `tests/api/conftest.py`: added a `connect` event listener that runs
  `PRAGMA foreign_keys=ON` on the shared SQLite engine, matching
  Postgres's always-on FK enforcement.
- New regression tests: `test_sqlite_foreign_keys_are_enforced_in_test_fixture`
  (guards the guard) and `test_create_job_succeeds_with_foreign_keys_enforced`
  in `tests/api/test_jobs.py`.

### 2. Resume before first checkpoint

**Bug:** `_mark_running` used `Job.started_at is None` as the proxy for
"this run needs an initial state". But `started_at` is set the moment a
run transitions to `RUNNING` — if the process crashes before the graph's
first node commits its first LangGraph checkpoint, `started_at` is
populated with zero checkpoints on disk. A later resume (via the startup
sweep or `POST /jobs/{id}/resume`) then saw `started_at is not None`,
inferred "resume from checkpoint", and called `graph.ainvoke(None, config)`
against a `thread_id` with no checkpoint — LangGraph raises
`EmptyInputError` and the job is stuck at `RUNNING` forever.

**Fix:**
- `app/jobs/runner.py`: added `_has_checkpoint(graph, config)`, which asks
  the compiled graph's own checkpointer (`graph.checkpointer.aget_tuple`)
  whether a checkpoint exists for the `thread_id`. `run_locked_job` now
  computes this once and passes `needs_initial_state=not has_checkpoint`
  into `_mark_running`, which builds the initial state whenever there's no
  checkpoint — regardless of what `started_at` says. `started_at` is still
  set once, for wall-clock budget purposes only.
- New regression tests in `tests/api/test_resume.py`:
  `test_sweep_resumes_job_with_started_at_set_but_no_checkpoint` (via the
  startup sweep) and `test_resume_endpoint_with_started_at_set_but_no_checkpoint`
  (via `POST /jobs/{id}/resume`). Both seed a job with `started_at` set and
  status `RUNNING`/`QUEUED` but no prior `graph.ainvoke` call for that
  `thread_id` (so the `MemorySaver` genuinely has no checkpoint), and
  assert the job reaches `BIBLE_LOCKED` instead of raising
  `EmptyInputError`. Confirmed both tests fail with the old logic
  (`EmptyInputError: Received no input for __start__`) and pass with the fix.

## Important

### 3. Sweep RLS

**Bug:** `get_raw_session` (used only by the startup sweep) shared the
same engine/sessionmaker as the per-request path. Every tenant table has
`FORCE ROW LEVEL SECURITY`, which binds every role including the table
owner — so without a role with `BYPASSRLS` on a distinct connection, the
sweep would silently see zero non-terminal jobs in production, defeating
its entire purpose, without any error.

**Fix:**
- `app/config.py`: added `Settings.database_url_sweep: str | None = None`
  and `Settings.database_url_for_sweep()`, which falls back to
  `database_url` when unset (correct for dev/tests — SQLite has no RLS).
- `app/main.py` lifespan: builds a second engine/sessionmaker from
  `database_url_for_sweep()` only when it differs from `database_url`, and
  wires it into `AppState.sweep_session_factory`. The per-request
  `session_factory` is untouched and keeps using the RLS-forced DSN
  unconditionally.
- `.env.example` documents `DATABASE_URL_SWEEP` (optional; must be a
  `BYPASSRLS` role in production).
- `app/db/session.py::get_raw_session` docstring updated to point at the
  new setting instead of describing an unimplemented intent.
- `docs/architecture/data-model.md` RLS section documents `FORCE ROW LEVEL
  SECURITY` + the sweep DSN requirement.
- New tests in `tests/test_config.py` cover the fallback and override
  behavior of `database_url_for_sweep()`. Tests continue to pass the same
  SQLite maker for both session factories (no RLS to bypass there), as
  suggested — the fallback path (`database_url_sweep` unset) is exactly
  that case.

### 4. Redis not authoritative

**Bug:** On a Redis cache hit, `resolve_idempotency` returned the cached
`(job_id, request_hash)` mapping without ever checking whether that job
still exists in Postgres. Separately, `create_job`'s replay branch
defaulted to `JobStatus.QUEUED` whenever `session.get(Job, ...)` returned
`None` — fabricating a status for a job that doesn't exist instead of
failing honestly (failure ladder rung 5).

**Fix:**
- `app/cache/idempotency.py`: added `_cached_job_still_exists`. On a Redis
  hit, when a candidate `job` was supplied (the real `POST /jobs` path),
  the referenced job is looked up in Postgres. If it's gone, the stale
  Redis key is deleted and the code falls through to the normal
  Postgres-authoritative path (treated as a cache miss — this naturally
  creates a fresh job, since `ON DELETE CASCADE` means the old
  `idempotency_keys` row was deleted along with the job).
- `app/api/jobs.py`: the replay branch now raises `AppError("JOB_NOT_FOUND", ...,
  http_status=404)` instead of defaulting to `QUEUED` if the replay target
  doesn't exist — a defense-in-depth backstop for the (FK-cascade-should-
  prevent-this) case where Postgres itself has an inconsistent state.
- New regression test:
  `test_redis_stale_mirror_after_job_deleted_does_not_fabricate_queued` in
  `tests/api/test_jobs.py` — creates a job, deletes it (cascading its
  idempotency key row), replays the same `Idempotency-Key`, and asserts a
  *new* job is created (not a fabricated `QUEUED` for the deleted one) and
  the Redis mirror is updated to point at the new job. Confirmed this test
  fails with `IntegrityError` against the pre-fix code.

### 5. Tests / mapping gap

- `app/jobs/runner.py`: added `"FAILED_NO_PROGRESS": JobStatus.FAILED_NO_PROGRESS`
  to `_OUTCOME_TO_STATUS`. No node currently emits this outcome, but per
  `.cursor/rules/03-harness-loop-and-termination.mdc` it's a valid harness
  terminal state; leaving it out of the map means a future node that does
  emit it would leave the job silently stuck at `RUNNING`
  (`_OUTCOME_TO_STATUS.get(...)` returns `None`).
- New `tests/jobs/test_runner.py` covers the mapping and `_has_checkpoint`
  directly (no checkpointer attr, checkpointer with/without a checkpoint).
- FK-on create and Redis-stale-mirror coverage: see items 1 and 4 above.

## Minor

### `RequestValidationError` envelope

**Bug:** A body that failed Pydantic validation returned FastAPI's default
`{"detail": [...]}` shape instead of the platform's `{code, message,
trace_id}` envelope every other error path uses.

**Fix:** `app/api/errors.py` registers a `RequestValidationError` handler
that returns `code="REQUEST_VALIDATION_FAILED"`, `message=str(exc)`, and
the request's `trace_id`, at `422`. New test
`test_request_validation_error_returns_stable_envelope` in
`tests/api/test_error_envelope.py`.

## Verification

- `tests/api` and the full suite were re-run after each fix.
- Every new regression test was confirmed to fail against the pre-fix code
  (verified by temporarily reverting `app/jobs/runner.py`,
  `app/api/jobs.py`, and `app/cache/idempotency.py` and re-running) and
  pass with the fix restored.
- Final full suite: **73 passed, 5 skipped** (skips are the
  `TEST_DATABASE_URL`-gated Postgres RLS tests in `tests/db/test_rls.py`,
  unaffected by this change — no live Postgres in this environment).
  Baseline before this task was 61 passed, 5 skipped.

## Files changed

- `app/api/errors.py`, `app/api/jobs.py`, `app/cache/idempotency.py`,
  `app/config.py`, `app/db/session.py`, `app/jobs/runner.py`,
  `app/main.py`, `.env.example`, `docs/architecture/data-model.md`
- Tests: `tests/api/conftest.py`, `tests/api/test_jobs.py`,
  `tests/api/test_resume.py`, `tests/api/test_error_envelope.py`,
  `tests/jobs/test_runner.py` (new), `tests/test_config.py` (new)
