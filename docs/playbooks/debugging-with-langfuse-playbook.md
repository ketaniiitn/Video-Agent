# Playbook: Debugging with Langfuse

- Start from the trace, not the code. A trace = one job; walk its spans (nodes) in order
  before opening any source file.
- For a cost anomaly: look at the generations under the suspect span — model, tokens, and
  cost are all attached per generation, so you don't need to guess which call was expensive.
- For a quality anomaly (bad continuity, low QC score): check the generation's prompt
  version against the registry — a silent prompt drift (someone edited a "live" prompt) is
  a common root cause and should be treated as a process bug, not just fixed once.
- Correlate with logs via `trace_id` — every JSON log line carries it, so you can pull
  exactly the log lines for one job without grepping.
- If the trace shows a circuit breaker open or a budget cap hit, that's expected behaviour
  working — don't debug it as a bug until you've confirmed the thresholds themselves are
  wrong.
