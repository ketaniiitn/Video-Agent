# Prompt template: Refactor safely

Fill in `{target}` and `{goal}`.

---

Refactor {target} to {goal}. No new features or behaviour changes in this same change —
if you notice something worth adding while doing this, list it separately instead of
including it.

Follow `docs/workflows/refactoring-workflow.md`: confirm this doesn't alter checkpoint or
idempotency semantics, run the full test suite, and note that I still need to do a manual
Langfuse trace inspection for the affected node before merging.
