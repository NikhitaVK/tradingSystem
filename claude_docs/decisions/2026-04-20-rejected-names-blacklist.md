# Decision: Loop 1 retry feedback via rejected_names blacklist

**Date**: 2026-04-20

## Decision
When the analyst rejects a candidate on a Loop 1 attempt, its name is added to a `rejected_names` set that is threaded into the next call to `candidate_generator.generate(...)` so the same template is excluded from the next pool.

## Reason
The candidate generator is deterministic and templated, so without intervention every retry attempt produced the identical top candidate, which the analyst then re-rejected. Loop 1 would exhaust `LOOP1_MAX_ATTEMPTS` against the same strategy. A name-level blacklist costs nothing, preserves determinism elsewhere, and immediately forced attempt 2 to surface a different candidate (MACD_Cross_PureSLTP_Mid was approved on the very next try).

## Alternatives Considered
- **Random parameter jitter inside the generator** — rejected: breaks determinism and reproducibility of tests
- **Pass full diagnosis history to LLM** — rejected: bloats context, KB already stores the history
- **KB mechanism blacklist** — exists for mechanism family but does not target the specific name; insufficient on its own


## Related

- MOC: [[agents]]
- [[2026-04-16-llm-as-selector-empirical-search]]
