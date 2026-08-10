---
tags: [trial, knowledge-base, finmem, memory, spec]
related: ["[[_trials]]", "[[2026-04-15-finmem-layered-memory]]", "[[2026-08-09-finmem-faithful-compound-score]]", "[[2026-08-09-kb-backfill-from-search-logs]]", "[[2026-08-07-kb-structure-measured]]"]
status: narrowed — one open question, partially runnable
created: 2026-08-08
updated: 2026-08-09
---

# Trial spec — Memory relevancy, judged by system performance

**Up**: [[_trials]] · **Harnesses**: `scripts/trials/kb_structure_trial.py`,
`scripts/trials/kb_retrieval_trial.py`

> **This spec was narrowed on 2026-08-09.** It previously proposed three competing
> memory designs. Two of the three became decisions rather than experiments once the
> corpus grew and the deviations were traced to the paper — see
> [[2026-08-09-finmem-faithful-compound-score]]. What remains is one genuinely open
> question. The original three-option framing is preserved in §Superseded.

---

## The one open question

**Does the relevancy term need embeddings, or do our structured tags carry it?**

FinMem's third score term is `cosine(embed(memory), embed(query))`. We now ship a
structured stand-in — regime match 0.6 + mechanism match 0.4 — because Anthropic
offers no first-party embeddings endpoint and the tags were already there. Whether
that is a reasonable substitute or a material loss is untested.

| Arm | `S_relevancy` | Cost |
|---|---|---|
| **S — structured** (shipped default) | regime 0.6 + mechanism 0.4 from stored tags | zero, deterministic |
| **E — embeddings** | cosine similarity via `voyage-finance-2` | new provider, API key, per-query cost |
| **N — none** (control) | constant 0 | zero — isolates whether relevancy earns its place at all |

The **N** arm matters as much as **E**: if relevancy contributes nothing measurable,
the honest answer is to drop the term, not to buy a better one.

## Settled — no longer trialled

| Was | Now |
|---|---|
| Ours vs FinMem canonical scoring (T1) | **Decided.** FinMem's summed, scaled form adopted — it is the published baseline and the product form is what caused the purge bug |
| Keep or collapse the double age-decay (T2) | **Decided with it.** FinMem applies both `α` on importance and `Q` on recency; we now do too |
| Whether importance should be earned (O) | **Prerequisite, not an option.** Requires `kb_entries_used` to be populated (gap G6) before it can exist at all |

## What the backfill changed

The blocking objection to this trial was that the corpus could not distinguish any
design from any other. That is resolved:

| | Before | After |
|---|---|---|
| KB entries | 74 | 126 |
| Populated layers | 1 | 3 (3 / 53 / 70) |
| Distinct importance values | 3 | 4 |
| Mechanism classes | ~0 usable | 5 |
| Scoring variants producing identical rankings | **all pairs** | **no pairs** |

Age spread is still one cluster (April 2026) — the backfill fixes **relevancy**
identifiability, not **recency**. Any finding about the decay horizons remains
weakly supported until the system has run across more calendar time.

---

## Method — outcome-grounded, not hand-labelled

**Hand-labelling was dropped on 2026-08-09.** The earlier design asked a human to
mark which findings were "relevant" and scored each arm on how well it recovered
those marks. That measures *agreement with one person's guess about relevance*,
which is a proxy. The thing we actually care about is whether better retrieval makes
the system perform better — and if it does, that must be visible downstream. Scoring
against the outcome is strictly better evidence, and it removes the human-labelling
dependency entirely.

The obvious outcome is **trade success**, and it is still unavailable: 2 trades, 0
resolved. But retrieval quality surfaces earlier than that, in what Loop 1 *produces*:

| Metric | Why it is the real thing, not a proxy | Cost |
|---|---|---|
| **Repeat-failure rate** (primary) | Does Loop 1 re-propose a mechanism the KB already recorded failing in this regime? Preventing that is what a knowledge base is *for*. Binary per attempt, computable from `strategy_evolutions` | free |
| **OOS composite score** of the accepted strategy | `profit_factor × WFE × (1−regime_concentration)` — real quality of what the system ships | free (already computed) |
| **Attempts-to-acceptance** | Better memory should waste fewer retries | free |
| **Analyst verdict score** | Independent quality judgement | free |
| Trade success | The correct long-run measure — revisit at ≥50 resolved trades | months |

Repeat-failure rate is the one to lead on: it is outcome-grounded, needs no labels,
needs no live trades, and maps directly onto the mechanism under test.

**Design — paired replay.** To claim bundle X produced a better outcome than bundle Y
you need the same decision made twice with different memory. That is achievable here
because `candidate_generator` is deterministic and backtests are deterministic given
data: freeze the pool, pair and seed, and vary **only** the memory bundle. Same
scenario under S / E / N gives paired samples → **Wilcoxon signed-rank**, the test
FinMem uses.

**Honest caveat.** Outcome labels are noisier than relevance labels. The chain from
bundle → selected strategy → OOS score passes through an LLM sampling step, so a
single run proves nothing; this needs repeats, which is where the API cost sits.
Stage it: pilot at 4 scenarios × 3 arms × 1 repeat, expand only if the effect
direction is usable.

**Label-free structural arm (runnable now, no API spend).** `kb_structure_trial.py`
measures staleness, redundancy, token budget, coverage and robustness per arm. It
does not measure whether retrieval helped, only what it cost — run it as the cheap
screen before spending on paired replay.

## Decision rule

Adopt the arm that wins on repeat-failure rate without losing on OOS composite score,
at *p* < 0.05. If **S** and **E** do not separate, keep **S** — no dependency. If
**N** matches both, remove the relevancy term entirely rather than buying a better one.

## Remaining blockers

| # | Blocker | Status |
|---|---|---|
| B1 | G5 layer mislabel / purge bug | ✅ **Cleared** — `reclassify_layers()`, exposure 72→1 |
| B3 | Corpus cannot discriminate | ✅ **Cleared** — backfill; all variants now rank differently |
| B2 | `kb_entries_used` never written | ✅ **Cleared** — Loop 1 records the retrieved bundle; feedback credits those entries |
| B5 | Gold labels for the ranking arm | ✅ **Dissolved** — outcome metrics replace hand-labelling |
| B4 | 0 resolved trades | ❌ Open — blocks *trade-linked* metrics only; repeat-failure rate does not need them |
| B6 | API budget for paired replay | ❌ Open — needs sign-off before the pilot spends |

## Record result here

*Label-free arm: see [[2026-08-07-kb-structure-measured]], re-run on the 126-entry corpus.*
*Ranking arm: pending gold labels.*

| Arm | Repeat-failure rate | OOS composite | Recall@5 | Verdict |
|---|---|---|---|---|
| S — structured | | | | |
| E — embeddings | | | | |
| N — none | | | | |

---

## Superseded

The original three options (Faithful FinMem / Structured Relevance / Outcome-Weighted)
are retained for provenance. They collapsed because "Faithful FinMem" and "Structured
Relevance" differed only in how `S_relevancy` is computed — which is the single question
above — and "Outcome-Weighted" turned out to be blocked on plumbing (G6) rather than
being a design alternative.
