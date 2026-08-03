# System Architecture (proposed)

This is a proposed LangGraph topology consistent with the PRD's 6 stages and the platform's
checkpoint-per-node rule. Update this file whenever the graph topology actually changes —
it's the map Claude (and you) should trust over re-deriving it from code each time.

## Graph topology (text form)

```
plan_story
   -> lock_continuity_bible                       [checkpoint]
        -> generate_shot(n=1)                      [checkpoint, budget check]
             -> chain_frame(n=1)                   [checkpoint]
                  -> qc_score(n=1)                  [checkpoint]
                       --pass--> generate_shot(n=2) ... repeats through n=4
                       --fail, attempts<2--> repair_shot(n=1) -> qc_score(n=1)
                       --fail, attempts==2--> flag_degraded(n=1) -> generate_shot(n=2)
        -> assemble                                [checkpoint, requires all 4 shots resolved]
             -> deliver                            [checkpoint]
```

- **Sequential only** across `generate_shot(n)` — never fan these out in parallel
  (`.cursor/rules/15-video-pipeline.mdc`, ADR-0001).
- **Budget check** happens before every `generate_shot` / `repair_shot` call, evaluated
  against the job's iteration/time/token/USD caps. A cap trip resolves the job to
  `PARTIAL`, not `FAILED`, provided ≥1 shot succeeded.
- **QC/repair loop** is bounded per-shot at 2 repair attempts; cap exhaustion routes to
  `flag_degraded`, not to failing the job.
- **`assemble`** only runs once all 4 shot slots are resolved (success or flagged-degraded)
  — never on a partially-populated shot list without going through `flag_degraded` first.

## Open questions

- Exact checkpointer backend for LangGraph (Postgres-backed, per ADR-0003) — schema TBD,
  see `data-model.md`.
- Whether `qc_score` and `repair_shot` are separate nodes or a single node with internal
  branching — lean separate, since checkpoint-per-node implies repair should be resumable
  independently of the QC call that triggered it.
