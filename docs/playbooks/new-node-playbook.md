# Playbook: Adding a new LangGraph node

1. Define the state delta — what does this node read from and write to the shared typed
   state? Keep it minimal.
2. Write the node function. Single responsibility only (`.cursor/rules/10-langgraph-conventions.mdc`).
3. Wire in checkpointing — this should be automatic via the graph's checkpointer, but
   confirm it's not accidentally bypassed.
4. Raise typed exceptions on failure, mapped to the platform error taxonomy — never a bare
   `Exception`.
5. Add/update conditional edges, including any loop-with-cap logic, with the cap read from
   state.
6. Write tests per terminal state the node can reach (`.cursor/rules/17-testing-standards.mdc`).
7. Update `docs/architecture/system-architecture.md`'s topology diagram — treat a stale
   diagram as a bug.
8. Self-review against `docs/checklists/new-agent-node-checklist.md` before opening the PR.
