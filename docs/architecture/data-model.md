# Data Model

Postgres 16 is the system of record. Migration `001_m1_initial` owns the complete
M1 application schema and the LangGraph checkpoint schema.

## M1 application tables

- `tenants`: `id`, `name`, `created_at`. This is the tenant identity root and is
  accessed only by privileged provisioning code; it does not contain a `tenant_id`.
- `jobs`: `id`, `tenant_id`, `status`, `prompt`, `trace_id`, `created_at`,
  `updated_at`, nullable `started_at`, all four `budget_max_*` fields, and
  `budget_used_usd`, `budget_used_tokens`, and `budget_used_iterations`.
  `status` is the native `job_status` enum containing all harness outcomes.
- `story_plans`: `id`, `tenant_id`, unique `job_id`, `beats_json`, `created_at`.
- `continuity_bibles`: `id`, `tenant_id`, unique `job_id`, `bible_json`,
  nullable `locked_at`, `created_at`.
- `idempotency_keys`: `id`, `tenant_id`, `key`, `request_hash`, `job_id`,
  `created_at`, `expires_at`. `(tenant_id, key)` is unique, making durable
  work creation idempotent per tenant.

Foreign keys from tenant data use `ON DELETE CASCADE`. Story plans and
continuity bibles are one-to-one with jobs.

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
USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
```

Application transactions set `app.tenant_id` locally before accessing tenant
data. A missing setting exposes no tenant rows.

## Deferred

`shots`, `qc_scores`, and `cost_ledger` arrive with later M1 pipeline tasks.
Vector storage remains deferred by ADR-0005 because Video Agent v1 has no
semantic-search requirement.
