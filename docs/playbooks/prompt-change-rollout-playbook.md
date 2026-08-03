# Playbook: Rolling out a prompt or model change

1. Bump the prompt's version in the Langfuse prompt registry — never overwrite a live
   version in place.
2. Define, before rollout, the specific Langfuse score/metric that must hold (e.g. story
   coherence, continuity score) and the threshold for "held" vs "regressed."
3. Ship behind a feature flag at 10% of traffic.
4. Let it run long enough to get a meaningful sample against the defined metric — don't
   promote on a handful of jobs.
5. Decision gate: promote to 100% only if the metric holds against the incumbent. If it
   doesn't, roll back the flag — don't patch the new prompt in place mid-rollout.
6. Record the outcome (held / rolled back, and why) somewhere durable — a short note in the
   relevant ADR or a new one if the change was structural, not just a prompt tweak.
