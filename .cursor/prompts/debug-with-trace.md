# Prompt template: Debug from a trace

Fill in `{error_code}` and `{trace_id}`.

---

I'm seeing error code `{error_code}` with trace_id `{trace_id}`. Walk the span tree for this
trace and hypothesize root cause, referencing the failure ladder in
`.cursor/rules/14-provider-abstraction-mcp.mdc` (retry → fallback → circuit break → degrade
→ fail honestly). Check whether this job can resume from its last checkpoint before
suggesting a manual re-run. Follow `docs/playbooks/debugging-with-langfuse-playbook.md`.
