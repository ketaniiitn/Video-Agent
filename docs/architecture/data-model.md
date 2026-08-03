# Data Model (proposed)

Proposed Postgres 16 schema. **Nothing here is implemented yet** — the first migration PR
should treat this file as the spec to implement against, and update it if reality diverges.
Every table below is tenant-scoped and needs an RLS policy (`.cursor/rules/12-database-rls.mdc`).

| Table | Key columns | Notes |
|---|---|---|
| `tenants` | `id`, `name` | RLS root |
| `jobs` | `id`, `tenant_id`, `status`, `prompt`, `created_at`, `budget_*` | `status` mirrors harness terminal states |
| `story_plans` | `id`, `job_id`, `beats_json` | 4-beat arc, machine-readable |
| `continuity_bibles` | `id`, `job_id`, `bible_json`, `locked_at` | Immutable once `locked_at` is set |
| `shots` | `id`, `job_id`, `beat_index`, `status`, `attempt_count`, `frame_ref`, `cost_usd`, `model_alias`, `seed`, `prompt` | One row per beat (4 per job); `attempt_count` caps at repair limit |
| `qc_scores` | `id`, `shot_id`, `score`, `attempt_number`, `rationale` | One row per QC pass, not overwritten |
| `cost_ledger` | `id`, `job_id`, `shot_id` (nullable), `usd`, `tokens`, `model_alias`, `created_at` | Feeds cost-regression CI gate and per-job reproducibility |
| `idempotency_keys` | `key`, `tenant_id`, `job_id`, `created_at`, `expires_at` | Mirrors Redis `idem:` entries; Redis is the fast path, this is the durable record |

## Deferred

Vector storage (pgvector vs MongoDB Atlas) is **not** included above — see
`adr/0005-pgvector-vs-mongo-atlas.md`. Video Agent v1 has no specified semantic-search use
case; don't add a vector table speculatively.
