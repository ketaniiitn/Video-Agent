# Prompt template: Plan a feature

Copy into Cursor chat, filling in `{feature}`. Reference this file with `@.cursor/prompts/plan-feature.md`
if you'd rather point at it than paste it.

---

I want to implement: {feature}

Before writing any code:
1. Restate which pipeline stage(s)/node(s) this touches, referencing
   `docs/architecture/system-architecture.md`.
2. Name which `.cursor/rules/*.mdc` files apply.
3. Flag any non-negotiable (`01-platform-non-negotiables.mdc`) or out-of-scope item
   (`04-scope-guardrails.mdc`) this could put at risk.
4. Propose an approach.
5. List open questions.

Wait for my confirmation before implementing anything.
