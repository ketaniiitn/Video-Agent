# Implementation Workflow

1. Open the folder(s) the task touches so the relevant Auto Attached rules load
   (`app/pipeline/**`, `app/providers/**`, etc.) rather than relying on memory of what they
   say.
2. Reference the specific ADR/doc the task relates to explicitly in the prompt — don't make
   Claude re-derive context that already exists in `docs/`.
3. Implement, writing tests alongside (not after) — especially for terminal-state coverage,
   which is easy to forget once the happy path works.
4. Self-review against `docs/checklists/pr-self-review-checklist.md`.
5. Update `docs/architecture/system-architecture.md` if the graph topology changed.
