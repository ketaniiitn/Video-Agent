# Data Model

Postgres 16 is the system of record. Migration `001_m1_initial` owns the complete
M1 application schema and the LangGraph checkpoint schema.
`002_expand_tenant_aware_job_fks` adds tenant-aware job references without
removing the original constraints. `003_rls_nullif_tenant_setting` hardens RLS
policies against an empty `app.tenant_id` setting on pooled connections.
`004_m3a_shots` expands the job lifecycle and adds shot-generation persistence.

## Application tables

- `tenants`: `id`, `name`, `created_at`. This is the tenant identity root and is
  accessed only by privileged provisioning code; it does not contain a `tenant_id`.
- `jobs`: `id`, `tenant_id`, `status`, `prompt`, `trace_id`, `created_at`,
  `updated_at`, nullable `started_at`, four `budget_max_*` caps
  (`usd`, `tokens`, `iterations`, `wall_clock_seconds`), and
  `budget_used_usd`, `budget_used_tokens`, and `budget_used_iterations`.
  `status` is the native `job_status` enum:
  `QUEUED`, `RUNNING`, `BIBLE_LOCKED`, `SHOTS_READY`, `PARTIAL`, `FAILED`,
  `FAILED_NO_PROGRESS`, `ESCALATED`.
- `story_plans`: `id`, `tenant_id`, unique `job_id`, `beats_json`, `created_at`.
- `continuity_bibles`: `id`, `tenant_id`, unique `job_id`, `bible_json`,
  nullable `locked_at`, `created_at`.
- `idempotency_keys`: `id`, `tenant_id`, `key`, `request_hash`, `job_id`,
  `created_at`, `expires_at`. `(tenant_id, key)` is unique, making durable
  work creation idempotent per tenant.
- `shots`: `id`, `tenant_id`, `job_id`, `beat_index`, `status`,
  `attempt_count`, nullable `clip_path`, nullable `frame_path`, `cost_usd`,
  `provider_id`, nullable `seed`, `prompt`, `created_at`, and `updated_at`.
  `(job_id, beat_index)` is unique, `beat_index` is constrained to 1–4, and
  `status` is the native `shot_status` enum: `PENDING`, `RUNNING`,
  `SUCCEEDED`, `FAILED`.
- `cost_ledger`: `id`, `tenant_id`, `job_id`, nullable `shot_id`, `usd`,
  nullable `tokens`, `provider_id`, and `created_at`. It records provider cost
  entries at job or shot granularity.

Foreign keys from tenant data use `ON DELETE CASCADE`. Jobs expose unique
`(id, tenant_id)` reference columns. Story plans, continuity bibles, and
idempotency keys, shots, and cost ledger entries reference jobs through
`(job_id, tenant_id)`, preventing a tenant-owned row from pointing at another
tenant's job. Cost ledger shot references become null if a shot is removed.
Story plans and continuity bibles are one-to-one with jobs.

## Checkpoint tables

Alembic creates `checkpoint_migrations`, `checkpoints`, `checkpoint_blobs`, and
`checkpoint_writes`. Their columns and keys mirror
`langgraph-checkpoint-postgres` 3.1.1 schema version 10. The three data tables
also carry a non-null `tenant_id`, populated from the transaction-local tenant
setting so the unmodified saver can write through RLS.

Alembic is the only DDL owner. Application startup must not call
`AsyncPostgresSaver.setup()`. A checkpoint package schema upgrade requires a
new reviewed Alembic migration before the dependency is promoted.

## Row-level security

Every table containing `tenant_id` has row-level security enabled and forced.
Its policy uses:

```sql
USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
```

PostgreSQL applies the same expression as `WITH CHECK` when it is omitted, so
cross-tenant writes are rejected as well as cross-tenant reads.
Application transactions set `app.tenant_id` locally before accessing tenant
data. A missing setting exposes no tenant rows.

Every tenant table is also `FORCE ROW LEVEL SECURITY`, so RLS binds every
role, including the table owner — there is no owner-bypass escape hatch.
The startup sweep (`app/jobs/runner.py::sweep_stale_jobs`) reads
non-terminal jobs across all tenants and therefore needs a role with
`BYPASSRLS` (or `SET ROLE` to one) on a connection distinct from the
per-request pool. `Settings.database_url_sweep` (optional; falls back to
`database_url`) is that DSN — see `.env.example` and
`app/db/session.py::get_raw_session`. The per-request path must never use
this DSN or role.

## Deferred

`qc_scores` arrives with a later pipeline task. Vector storage remains deferred
by ADR-0005 because Video Agent v1 has no semantic-search requirement.
