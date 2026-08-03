# Playbook: Incident response

Longer-form version of `.cursor/rules/91-incident-debug-with-trace.mdc`.

1. Get the `trace_id` — from the error response, a log line, or the user's report. If none
   exists, that's itself a bug (every error response must carry one).
2. Open the trace in Langfuse. Identify the failing span (node) and, if applicable, the
   generation (LLM call) that triggered it.
3. Identify the terminal state reached and the failure-ladder rung that fired (retry /
   fallback / circuit break / degrade / fail honestly).
4. Check checkpoint state: can the job resume from its last checkpoint, or is a manual
   re-run needed? Prefer resume — never manually re-run a job that can resume.
5. Check budget and circuit-breaker state for the dependency involved: was this a genuine
   failure, or an expected budget/circuit-break trip working as designed?
6. Write up: what happened, what was preserved (per the "fail honestly" principle), what
   was done, and what (if anything) should change — a new rule, a new ADR, or a test that
   would have caught it.
7. If the root cause implies a rule or ADR should change, propose that change explicitly
   rather than just fixing the immediate bug.
