# New Agent Node Checklist

Tick-box companion to `docs/playbooks/new-node-playbook.md`.

- [ ] Single responsibility — this node does one thing
- [ ] State delta is minimal and typed
- [ ] Checkpoints after execution
- [ ] All external calls (model, provider) go through the gateway/provider abstraction,
      never a direct client
- [ ] Failure raises a typed exception, not a bare `Exception`
- [ ] Any loop/cap logic reads its cap from state, not a scattered constant
- [ ] Tests exist for every terminal state this node can reach
- [ ] `system-architecture.md` diagram updated
