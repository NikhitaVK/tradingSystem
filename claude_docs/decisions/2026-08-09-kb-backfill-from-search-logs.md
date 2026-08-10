---
tags: [adr, knowledge-base, data-recovery]
related: ["[[_decisions]]", "[[2026-04-16-llm-as-selector-empirical-search]]", "[[2026-04-15-finmem-layered-memory]]", "[[2026-08-09-finmem-faithful-compound-score]]", "[[data_pipeline]]"]
---

# Decision: Recover stranded empirical findings from `reasoning_logs` into the knowledge base

**Date**: 2026-08-09

## Decision

Backfill the knowledge base from the `empirical_search` rows in `reasoning_logs`
via `scripts/backfill_kb_from_search_logs.py`. Findings are written **aggregated
per distinct candidate strategy**, not one row per evaluation, under category
`parameter_insight`, each tagged with a `[backfilled from empirical_search logs]`
marker.

Result: 600 candidate evaluations across 52 distinct strategies became 52
findings. The KB went from 74 to 126 entries.

## Reason

Every Loop 1 attempt backtests `CANDIDATE_POOL_SIZE` (12) candidates.
`empirical_search._log_search_aggregate()` writes all 12 results into
`reasoning_logs.thinking` as an audit blob, and Loop 1 then calls
`write_finding()` **once per attempt** — the analyst's rejection diagnosis for the
single candidate that was selected.

So each attempt produced 12 empirical results and 1 knowledge-base entry. The
other 11 were real evidence about which mechanisms fail, with real Sharpe and
trade counts, discarded into a log nothing reads. Across 50 logged searches that
is 600 stranded evaluations.

This mattered beyond tidiness. Four candidate scoring formulas were measured
producing **byte-identical rankings** on the 74-entry KB, because 70 of 74 rows
shared one layer and importance took only three sampled values — the corpus could
not distinguish any retrieval design from any other. After the backfill all four
variants rank differently. The backfill is what made the memory trial possible.

## Alternatives Considered

- **One KB row per evaluation (600 rows)** — rejected: the same candidates recur
  across attempts, so this adds ~550 near-duplicates. Measured mean pairwise
  similarity within retrieved bundles is already 0.22; flooding the KB with
  repeats worsens exactly the signal-to-noise property retrieval is judged on.
- **Synthetic / generated KB entries to reach volume faster** — rejected for
  design evaluation. Generating a corpus from assumptions about what is relevant
  and then measuring which ranker recovers those assumptions is circular; it
  measures agreement with the generator, not usefulness to the trading system.
  Synthetic data remains valid for *mechanism* tests (promotion, demotion, purge)
  in a temp DB, and must never be written to `trading_system.db` — fabricated rows
  in the project's data record are an assessment-authenticity problem, not just a
  hygiene one.
- **Wait for the KB to grow organically** — rejected: at ~5 findings per Loop 1
  run this is months away, and the data already existed.
- **Category `failure_diagnosis` instead of `parameter_insight`** — rejected:
  these are empirical results about specific indicator/parameter configurations,
  which is what `parameter_insight` is for. It also routes them to the
  `intermediate` layer, previously empty in the live KB.

## Consequences

- Layer distribution went from one populated layer to three (3 / 53 / 70), which
  is what makes the decay constants observable.
- Mechanism diversity went from effectively none to five real classes
  (momentum 67, mean_reversion 32, breakout 10, volatility 2, unknown 4).
- Findings carry the real `created_at` of their source log, so they cluster in
  2026-04-17..20. The backfill fixes **relevancy** identifiability, not **recency**
  spread — age variation still requires the system to run over time.
- The marker keeps recovered rows auditable and separable from organically
  written findings, and makes the script idempotent.
- Backups: `backups/trading_system.pre-backfill.*.db`.

## Related

- MOC: [[_decisions]] · [[data_pipeline]]
- Cause: [[2026-04-16-llm-as-selector-empirical-search]] introduced the candidate
  pool whose results were being logged but not learned from
- Enables: [[2026-08-09-finmem-faithful-compound-score]]
- Gaps G5/G6 in [[_trials]]
