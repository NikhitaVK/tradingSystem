---
tags: [trial, knowledge-base, finmem, retrieval, assessment-evidence]
related: ["[[_trials]]", "[[2026-04-15-finmem-layered-memory]]", "[[2026-08-07-kb-retrieval-ranking]]"]
status: complete
created: 2026-08-07
---

# Component Trial #4 (measured) — Knowledge-Base Structure

**Up**: [[_trials]] · **Amends**: `ncea-assessment/trials/4_kb_structure.md`
**Implications lens**: `ncea-assessment/implications_planning.md`
**Harness**: [scripts/trials/kb_structure_trial.py](../../scripts/trials/kb_structure_trial.py)
**Data**: `trials_out/kb_structure_summary.json` · **Chart**: `trials_out/kb_structure_dotplot.svg`

The original trial #4 chose Option C from a qualitative ✅/⚠️/❌ matrix, "evaluated from the
implemented code + schema". This run replaces those judgements with measurements against
the same implications lens. It re-confirms the decision but **overturns two of the reasons
given for it**.

---

## The options

All three are implemented and live in the codebase, so this is a real before/after
comparison rather than a paper exercise.

| | Option | How it retrieves | Call |
|---|---|---|---|
| **A** | Flat keyword | `LOWER(content) LIKE '%kw%'` across all findings, ordered by recency. No notion of market context or importance. | `query_relevant(keywords)` |
| **B** | Regime-aware | A, plus a strict `WHERE regime = ?` filter, so the agent asks "what failed *in this kind of market*". | `query_relevant(keywords, regime=…)` |
| **C** | Layered + semantic | Findings carry a layer and importance; ranked by `importance × recency × layer-decay` with regime ×1.5 / mechanism ×1.3 boosts, top-K per layer. | `get_working_memory(regime, mechanism)` |
| **C2** | C + Haiku rerank | C plus LLM semantic reranking. **Not run** — spends API credit; trial #4 is costed as no-API. Enable with `--with-semantic`. | `query_relevant(…, query_context=…)` |

## Method

6 scenarios — every `(regime, mechanism)` pair the KB actually contains, i.e. the real
retry situations Loop 1 has been in. Bundle ceiling held constant at 15 for all options so
the comparison is like-for-like. Corpus frozen at 74 findings; one pinned clock.

Every metric is **label-free and objective**, so the trial is reproducible without a
hand-labelled relevance set. Relevance *ranking* is the one thing that genuinely needs
human gold labels; that arm is specified in [[2026-08-07-kb-retrieval-ranking]] and remains
pending.

## Results

![[kb_structure_dotplot.svg]]

Means across the 6 scenarios. **Re-run 2026-08-09** on the 126-entry corpus after the
backfill, the layer reclassification, and the switch to FinMem's summed score
([[2026-08-09-kb-backfill-from-search-logs]], [[2026-08-09-finmem-faithful-compound-score]]).
Original 74-entry figures in brackets.

| Metric | Implication | A flat | B regime | **C layered** | Better |
|---|---|---|---|---|---|
| Median age of retrieved findings (days) | Functionality — staleness | 112.2 *(111.2)* | 113.4 *(111.6)* | 112.9 *(111.5)* | lower |
| Mean pairwise similarity in bundle | Functionality — signal vs noise | 0.230 *(0.277)* | 0.297 *(0.297)* | **0.157** *(0.174)* | lower |
| Bundle size (tokens) | Usability — token budget | 10,295 *(12,770)* | 9,220 *(9,220)* | **4,812** *(2,736)* | lower |
| Findings returned | Functionality — coverage | **15** | 9.8 | 13 *(5)* | higher |
| Latency (ms) | Cost | 1.70 *(2.94)* | **0.80** *(1.13)* | 1.14 *(1.07)* | lower |

C's coverage rose from 5 to 13 — the layer reclassification removed the defect that
was starving it, exactly as the caveat below predicted. Its token bundle grew with
it and is still less than half of A's.

### Robustness — `implications_planning.md` §1, "handles empty / blank / missing input"

No option raised an exception. They differ in whether they return *anything useful*:

| Case | A flat | B regime | **C layered** |
|---|---|---|---|
| Empty knowledge base | 0 rows | 0 rows | 0 rows |
| Blank keyword list | 15 | 15 | 5 |
| Regime not seen before | 15 | **0 rows** | 5 |
| Regime is `None` | 15 | 15 | 5 |
| Keyword matches nothing | **0 rows** | **0 rows** | 5 |

---

## Which option won, and why

**Option C (layered memory) — confirmed.** It wins on three of the five measured
implications and never fails to return context.

The decisive margin is **token budget**: C sends 2,736 tokens where A sends 12,770 — a
**4.7× reduction** in the context bill on every single Loop 1 retry, with the strategy
agent already running an 8,000-token thinking budget. That is the largest, most consistent
effect in the whole trial; C is the tightest bundle in all 6 scenarios.

Second, **signal vs noise**: C's retrieved findings repeat each other far less (0.174 mean
pairwise similarity vs A's 0.277), so more of those tokens carry a distinct lesson.

Third, **graceful degradation**: C is the only option that returns context when the keyword
search misses or the regime has never been seen. A and B both return **zero rows** on a
keyword miss — the agent retries with no memory at all, silently. This is the practical
failure that matters most, because a keyword miss is invisible: nothing errors, the agent
just gets nothing.

C's cost is **coverage** — 5 findings against A's 15. This is deliberate selectivity, not a
defect, but it is a genuine trade-off and it is currently worse than designed (see below).

---

## Implications: before vs after the trial

| Implication | Before (asserted) | After (measured) | Verdict |
|---|---|---|---|
| **Functionality — relevance** | C ✅ "ranks by meaning + importance, not just word overlap" | **Still unmeasured.** Needs hand-labelled gold; formula variants were also found to be unidentifiable on this corpus. | ⏳ open |
| **Functionality — staleness** | C ✅ "layer decay + purge demote stale findings automatically" | **Refuted.** All three return context of the same age (111.2 / 111.6 / 111.5 days). Decay does nothing here. | ❌ overturned |
| **Functionality — signal vs noise** | A ❌ "can flood the prompt with near-duplicates" | **Half-refuted.** True near-duplicates do not exist (max corpus-wide Jaccard 0.714, so nothing clears a 0.8 bar). On the continuous measure C is genuinely 37% less redundant than A. | ⚠️ restated |
| **Functionality — robustness** | not assessed | **New, and decisive.** A and B return zero rows on a keyword miss; C always returns context. | ✅ new for C |
| **Usability — token budget** | C ✅ "saves tokens" | **Confirmed, and larger than expected.** 4.7× fewer tokens than A. | ✅ confirmed |
| **Cost / complexity** | C ❌ "most code — justified by the gains above" | **Confirmed**, and C is also the *fastest* (1.07 ms vs 2.94 ms) — the extra code costs no runtime. | ✅ confirmed |

Two claims did not survive contact with data:

1. **The staleness argument for C was wrong — and survived a re-test.** The corpus is one
   age cluster: essentially every finding was written inside a four-week window over 100
   days ago, so there is nothing fresher for decay to prefer. The 2026-08-09 re-run, with
   layers correct and FinMem scoring live, still shows no separation (112.2 / 113.4 / 112.9).
   The mechanism may well work; this corpus cannot show it, and only calendar time will fix
   that.

   > An intermediate version of the backfill stamped recovered findings with *today's*
   > date instead of the source evidence's date, which produced an apparent C win on
   > staleness (41.7 vs 113.4 days). That was an artifact of the bug, not a result. The
   > backfill now carries the source timestamp and the false win disappeared — recorded
   > here because it is exactly the kind of self-inflicted positive a trial should catch.
2. **Regime-awareness (B) is not the unqualified improvement the matrix implied.** B is
   *more* redundant than A (0.297 vs 0.277) — narrowing to one regime leaves findings that
   are more similar to each other — and its strict filter is brittle, returning nothing at
   all for an unseen regime. B's value survives only because C absorbs it as a soft ×1.5
   boost rather than a hard filter. **That is the strongest vindication of C's design in
   the whole trial, and the original matrix missed it.**

---

## Caveat that limits this trial

C's coverage number is depressed by a live defect, not by its design. All 74 rows carry
`layer='shallow'` from the migration default (gap **G5** in [[_trials]]), so C draws its
top-K from one layer and returns 5 instead of the ~9 correct classification would give
(70 deep / 1 intermediate / 3 shallow). Fixing G5 would improve C's coverage without
touching its token or redundancy advantage — so the winner does not change, but C's margin
here is understated.

The same defect makes `purge_kb()` delete 72 of 74 entries on the next Loop 1 run.
Snapshot: `backups/trading_system.20260807-182019.db`.

## Reproduce

```bash
python -m scripts.trials.kb_structure_trial run
python -m scripts.trials.kb_structure_trial run --with-semantic   # adds C2, costs API credit
```
