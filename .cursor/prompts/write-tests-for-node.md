# Prompt template: Write tests for a node

Fill in `{node_name}`.

---

Write tests for the `{node_name}` node covering:
- Happy path → `SUCCESS`
- Budget exhaustion → `PARTIAL` (best-so-far, flagged degraded)
- Repeated identical failure → `FAILED_NO_PROGRESS` (stop after 2nd occurrence, not a 3rd retry)
- Non-retryable error → `FAILED` / `ESCALATED`
- Resume from checkpoint after a simulated mid-run crash
- Idempotent re-invocation with the same key produces no duplicate work

No live model or provider calls — stub the gateway/provider abstraction or use a recorded
cassette, per `.cursor/rules/17-testing-standards.mdc`.
