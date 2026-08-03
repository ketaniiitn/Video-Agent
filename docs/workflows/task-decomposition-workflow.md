# Task Decomposition Workflow

Break a feature into node-sized units that match the LangGraph structure rather than
arbitrary code chunks:

1. Schema/state change (if any) — the typed state delta the feature needs.
2. Node implementation(s) — one node, one responsibility, per
   `.cursor/rules/10-langgraph-conventions.mdc`.
3. Tests — per terminal state, plus checkpoint/resume and idempotency where relevant.
4. Wiring into the graph — edges, conditional routing, cap logic.
5. Docs update — `system-architecture.md`, `data-model.md`, and any relevant ADR.

Prefer one PR per node where the feature spans multiple nodes — easier to self-review
against the checklists, and easier to bisect later if something regresses.
