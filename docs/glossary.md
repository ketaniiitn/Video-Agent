# Glossary

- **StateGraph** — a LangGraph-compiled graph; the unit every agent is built as.
- **Checkpoint** — durable save of graph state after a node runs, enabling resume-not-restart.
- **Continuity bible** — canonical character/wardrobe/location/lighting/palette/lens spec,
  locked and immutable for the life of a job.
- **Story beat** — one of the 4 segments (setup, development, turn, resolution) of the
  40-second arc.
- **Frame chaining** — conditioning shot *n+1*'s generation on shot *n*'s final frame, to
  carry identity forward.
- **Capability negotiation** — a provider abstraction pattern where providers declare what
  they can do, so failover picks a genuinely equivalent alternate.
- **Idempotency key** — client-supplied key ensuring a repeated request doesn't redo or
  re-bill work.
- **RLS** — row-level security; Postgres policy enforcing tenant isolation at the query
  level, not just the application level.
- **Alias (model)** — logical name (e.g. `reasoning-high`) that the LiteLLM gateway maps to
  an actual model; application code never sees the real model name.
- **Trace / span / generation** — Langfuse's hierarchy: one job = one trace, one node = one
  span, one LLM call = one generation.
- **Degrade** — returning a cached/stale/partial result rather than failing outright,
  always explicitly flagged as such.
- **Circuit break** — temporarily stopping calls to a failing dependency after a failure
  threshold, to avoid compounding the failure.
- **No-progress signature** — the same failure repeating twice, which stops retries
  immediately (`FAILED_NO_PROGRESS`) rather than looping indefinitely.
- **Presigned URL** — HMAC-signed download link for local artifacts; expires after
  `PRESIGNED_URL_TTL_SECONDS`. Cloud object storage is deferred — v1 serves from disk.
- **Feature flag** — env var (`FEATURE_*`) gating graph wiring and config validation for
  each pipeline stage; new behaviour ships behind a flag per platform non-negotiables.
- **Local test console** — developer UI at `GET /` for exercising the job API without
  Swagger; not a product editor.
