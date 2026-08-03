# Prompt template: Implement a LangGraph node

Fill in `{node_name}` and `{responsibility}`.

---

Implement the `{node_name}` node. Its single responsibility: {responsibility}.

Follow `.cursor/rules/10-langgraph-conventions.mdc`:
- Minimal typed state delta only.
- Checkpoints via the graph's checkpointer — don't hand-roll resume logic.
- No direct model/provider calls — go through the gateway alias or provider abstraction.
- Typed exceptions on failure, mapped to the error taxonomy.

Then write tests per `docs/checklists/new-agent-node-checklist.md` before considering this
done.
