---
tags: [moc, trials, backlog]
related: ["[[agents]]", "[[2026-04-15-finmem-layered-memory]]"]
---

# Trials — Improvement & Trial Backlog

**Up**: [[dashboard]]
**Across**: [[_architecture]] · [[_modules]] · [[_decisions]] · [[_standards]] · [[_tasks]] · [[_issues]] · [[_code]]

Candidates for empirical trialling. **Not decisions** (undecided) and **not tasks**
(unspecced). Each is a hypothesis surfaced while studying the system, with the
metric you'd measure to resolve it.

**Lifecycle**: `candidate` → (ready to run) split into own `trials/YYYY-MM-DD-slug.md`
with full method + "record result here" (mirrors `.claude/rules/testing/calibration_tests.md`) → on adoption
becomes a `decisions/` entry, or on build a `tasks/` entry.

Priority = expected signal **now** (given the DB has ~2 completed trades and a young KB)
× impact. High-signal-now items rank above things that need live history to test.

> [!done] 2026-08-09 — the KB-deletion bug is fixed
> `reclassify_layers()` repaired 71 mislabelled rows; purge exposure went 72/126 → 1.
> Snapshots in `backups/`. See [[2026-08-09-finmem-faithful-compound-score]].

**Trade-linked metrics are still unavailable** (2 trades, 0 resolved), but they are no
longer the blocker they were. Retrieval quality is now judged on **outcome metrics that
need no live trades** — repeat-failure rate, OOS composite score of the accepted
strategy, attempts-to-acceptance. Hand-labelled relevance judgements were dropped:
they measure agreement with a human's guess about relevance, where the outcome measures
the thing itself. See [[2026-08-08-memory-design-options]]. Reopen trade-linked
comparison at ≥50 resolved trades.

## Backlog — knowledge base / layered memory

| ID | Priority | Candidate | Type | What to measure | Status |
|---|---|---|---|---|---|
| T0 | ✅ Superseded | **Do the regime ×1.5 / mechanism ×1.3 boosts surface better context?** | calibration | Rank of first human-judged-relevant finding, with boosts vs without. The ×1.5/×1.3 boosts were replaced by FinMem's additive `S_relevancy`; question folded into M1. | superseded |
| T1 | ✅ Resolved | **Score formula: ours vs FinMem canonical** | deviation | Rank the same KB against `importance×recency ×1.5/×1.3` (ours) vs `recency + importance/100` (FinMem). **Measured 2026-08-07: produces a byte-identical ranking to baseline** — 70/74 entries share one layer and importance has only 3 sampled values, so the formulas are monotone in the same variables. Unfalsifiable then; **after the 2026-08-09 backfill all variants rank differently**. Superseded by adopting FinMem's summed form — [[2026-08-09-finmem-faithful-compound-score]]. | resolved as a decision |
| T2 | ✅ Resolved | **Resolve alpha-vs-Q redundancy** (age is decayed twice) | design | Collapse to one decay, or keep both? **Measured 2026-08-07: also ranking-identical to baseline** on this corpus, same cause as T1. Resolved by adopting the paper faithfully: FinMem applies both `α` on importance and `Q` on recency, and we now do too. | resolved as a decision |
| T3 | Med | **+5 win-boost size** | calibration | Sweep boost ∈ {2,5,10}. Measure how fast a genuinely-useful finding promotes vs how often noise promotes. Needs synthetic trade outcomes to have signal. | candidate |
| T4 | Med | **`COGNITIVE_SPAN_K` (top-K per shelf, =5)** | calibration | Vary 3/5/8. Measure prompt size vs whether the finding that *should* drive the decision is inside the bundle. | candidate |
| T5 | ✅ **Done** | **Per-retrieved-memory attribution** (FinMem access counter) | deviation / fix | Record which KB ids were in the working-memory bundle for a decision; boost *those* on win, not all rows sharing `strategy_id`. Measure: does credit concentrate on findings that actually recur in winning bundles? **Prereq for T6.** Implemented 2026-08-09: Loop 1 writes `kb_entries_used`; feedback credits those ids. | done |
| T6 | Med | **Reinforce successful *discoveries*, not only trade wins** | deviation / feature | Emit a smaller boost (e.g. +2) when a finding was in the bundle for an analyst-**passed** strategy. Weight below a live trade win. Depends on T5's retrieval-tracking link. | candidate |
| T7 | Low | **Q retention horizons (14/90/365)** | calibration | Undocumented defaults. Trial optimum shelf lifespans. Low signal until KB is larger & older. Rank below T2 (resolve redundancy first). | candidate |
| T8 | Low | **alpha exact values (0.90/0.967/0.988)** | calibration | Undocumented defaults. Only meaningful *after* T2 decides whether alpha survives at all. | candidate |

## Backlog — memory design (judged by system performance, not proxies)

| ID | Priority | Candidate | Type | What to measure | Status |
|---|---|---|---|---|---|
| M1 | **High** | **Relevancy term: structured tags vs embeddings vs none** | design | Repeat-failure rate + OOS composite score of the strategy Loop 1 accepts, paired replay, Wilcoxon signed-rank. → [[2026-08-08-memory-design-options]] | **narrowed; label-free arm runnable, paired replay needs API budget** |
| M2 | High | **Product → scaled sum scoring** (D2/D3) | fix | Our `importance × recency` departs from FinMem's `recency + relevancy + importance`. Unscaled product is *why* a 119-day finding scores 0.0002 and gets purged. **Adopted 2026-08-09.** | done |
| M3 | High | **Add the missing relevancy term** (D1/D4) | deviation | FinMem scores on three terms; we use two. Retrieval never asks "is this memory *about* the current situation?" **Adopted 2026-08-09** as a structured-tag term; embeddings vs tags is the open M1 question. | done |
| M4 | Med | **Reset recency on access** (D5) | deviation | FinMem resets recency to 1.0 for pivotal memories so they stop decaying. Ours decay out regardless of proven value. | candidate |
| M5 | Med | **Wire `query_semantic` into the live path, or delete it** (D8) | fix / cleanup | `get_working_memory()` never calls it, so phase 05's semantic retrieval is dead code in the path Loop 1 actually uses. | candidate |
| M6 | Low | **Re-derive layers from observed half-life** (D9) | design | FinMem layers by information timeliness; we layer by category, conflating "kind of finding" with "how long it stays true". | candidate |

## Integrity / accuracy gaps (fix-or-document, not really "trials")

| ID | Candidate | Note |
|---|---|---|
| G1 | **`intermediate` shelf has no automatic feeder** | Only auto-writes tagged `parameter_insight` are probation status events ([execution_agent.py:406](../../src/agents/execution_agent.py#L406)) — not true parameter insights. Shelf is theoretical capacity. Document as known limitation. |
| G2 | **Category is asserted, not inferred** | Writer supplies `category`; no check that content matches. A mislabel → wrong shelf, wrong lifespan (e.g. probation events → `intermediate`/90-day). Consider a validation or a content→category classifier. |
| G3 | **`finding` vs `memory` naming inconsistency** | KB layer says "finding" (43×), FinMem layer says "memory". Same `knowledge_base` row. Pick one term in the writeup; optionally rename in code. |
| G4 | **Retrieval floor filters on raw stored importance** | `get_working_memory` step-1 SQL filters `importance >= 5` on the *stored* value, not the live time-decayed value. Minor; note it. |
| G5 | ~~🔴 **All 74 KB rows are `layer='shallow'` backfill artefacts**~~ ✅ **FIXED 2026-08-09** — `reclassify_layers()`; purge exposure 72→1 | The `layer` column arrived by migration with `DEFAULT 'shallow'` ([schema.py:190](../../src/data/schema.py#L190)); every row predates it, and nothing has written a finding since. `write_finding` classifies correctly now (`failure_diagnosis → deep`), so the true distribution is 70 deep / 1 intermediate / 3 shallow. Because `shallow` uses `Q=14` vs `deep`'s `Q=365`, a 119-day entry scores recency `0.0002` instead of `0.72` — below the `0.05` purge floor. **Loop 1 calls `purge_kb()` at step 9, so the next successful run wipes the KB.** Snapshot taken at `backups/trading_system.20260807-182019.db`. Fix = reclassify before the next Loop 1 run. |
| G6 | ~~**`strategy_evolutions.kb_entries_used` is never written**~~ ✅ **FIXED 2026-08-09** | The column exists in the schema but is NULL in all 47 rows. It is exactly the retrieval-attribution link T5 needs — the schema half of T5 is already built, only the writer is missing. Cheapest unblock for T5 → T6 → T1/T2. |

## Provenance to verify against the FinMem paper

- Confirm FinMem actually uses **Ebbinghaus** forgetting curve wording (recency decay). — for [[2026-04-15-finmem-layered-memory]]
- Confirm the exact **alpha constants are NOT quoted from FinMem** (code says "from FinMem" — likely only the *scheme* is; the numbers are ours). See T8.
- Confirm FinMem's **access-counter** attributes to *retrieved* memories (basis for T5).
- "retention horizon" is a teaching gloss, **not** a FinMem term — don't cite it as one.
