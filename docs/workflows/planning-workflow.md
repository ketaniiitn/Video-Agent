# Planning Workflow

Use before writing code for anything non-trivial. Pairs with `.cursor/prompts/plan-feature.md`.

1. State which pipeline stage(s) / node(s) the feature touches, referencing
   `docs/architecture/system-architecture.md`.
2. Name which `.cursor/rules/*.mdc` files apply — if none obviously do, that's worth
   noting explicitly rather than proceeding assumption-free.
3. Flag any non-negotiable at risk (`01-platform-non-negotiables.mdc`) before proposing an
   approach, not after.
4. Propose an approach and list open questions.
5. Get explicit confirmation before implementing — especially for anything touching
   checkpointing, idempotency, the sequential-generation constraint, or scope boundaries.
6. If the plan implies an architectural decision not already covered by an ADR, draft one
   using `docs/architecture/adr/template.md` as part of the plan, not as an afterthought.
