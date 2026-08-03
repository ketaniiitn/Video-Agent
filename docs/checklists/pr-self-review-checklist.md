# PR Self-Review Checklist

Solo-dev project — this stands in for a second reviewer. Go through it before every merge,
not just for "big" changes; the non-negotiables apply regardless of change size.

- [ ] No provider named directly in application code — aliases only (`02-model-routing.mdc`)
- [ ] Every new/changed node checkpoints correctly; no bypassed checkpointing
- [ ] Budget checks (iterations/time/tokens/USD) present anywhere a loop or external call
      could run away
- [ ] Every new work-creating POST has an idempotency-key check
- [ ] Untrusted content (user input, retrieved/tool output) is handled strictly as data,
      never concatenated into instructions unguarded
- [ ] Errors are typed, mapped to the failure ladder, and the response carries a `trace_id`
- [ ] Tests cover the relevant terminal states, not just the happy path
- [ ] No out-of-scope v1 feature snuck in (`04-scope-guardrails.mdc`)
- [ ] Nothing that could trip the >3% eval regression or >20% cost regression CI gates
      without a benchmark test to catch it first
- [ ] `docs/architecture/system-architecture.md` / `data-model.md` updated if topology or
      schema changed
