# Refactoring Workflow

- Never refactor and add a feature in the same change — makes both harder to review and
  impossible to bisect cleanly.
- Refactors must not silently alter checkpoint or idempotency semantics. If a refactor
  changes *when* or *how* a node checkpoints, that's not "just a refactor" — treat it as a
  change requiring the same scrutiny as new behaviour, including an ADR update if it
  touches ADR-0003's assumptions.
- Run the full test suite, plus a manual trace inspection in Langfuse for at least one real
  (or cassette-replayed) run through the affected node, before merging — tests can pass
  while the trace shows something subtly different (e.g. extra generations, different
  token counts).
