---
tags: [trial, knowledge-base, finmem, retrieval]
related: ["[[_trials]]", "[[2026-04-15-finmem-layered-memory]]", "[[agents]]", "[[data_pipeline]]"]
status: superseded
created: 2026-08-07
---

# Trial — KB retrieval ranking

**Up**: [[_trials]] · **Method mirrors**: `.claude/rules/testing/calibration_tests.md`

Harness: [scripts/trials/kb_retrieval_trial.py](../../scripts/trials/kb_retrieval_trial.py)

---

## The question

Does the layered-memory retrieval actually surface the *right* prior finding when Loop 1
asks for context — and which part of the scoring formula is doing the work?

## Why not "improvement in trade success"

That was the intuitive framing, and it is the right long-run metric. It has **no signal
today**:

| Evidence | Count |
|---|---|
| Trades in DB | 2 |
| Trades with a resolved win/loss outcome | **0** (1 `open`, 1 `timeout`) |
| Trades needed for a meaningful win-rate comparison | 50 (`calibration_tests.md` §4) |

A win-rate dot plot would be plotting an undefined quantity. Worse, KB → trade success is
a very long causal chain (KB → agent prompt → spec selection → analyst verdict → live
fills), so even at 50 trades the KB's contribution would be badly confounded by the
strategy search and market conditions.

**Retrieval rank is the KB's own output.** It is measurable today on 74 real entries, it
isolates the component under test, and it is the thing the formula variants actually
change. Revisit trade-linked evaluation when Loop 2 has ≥50 resolved trades — tracked as
the trigger in §Reopen conditions.

## What is measured

For each labelled query scenario, where does the first genuinely-relevant KB entry land in
the ranking?

- **rank of first gold** (1-indexed, lower is better) — the dot plot axis
- **Recall@k** with `k = COGNITIVE_SPAN_K = 5` — did it reach the prompt at all
- **MRR** across scenarios — single summary number

The `k=5` band is shaded on the chart: outside it, the finding was retrieved in principle
but never reached the agent.

---

## P0 — blocker that must be fixed before any run

**Every one of the 74 KB rows carries `layer='shallow'`, and none of them should.**

The `layer` column was added by migration (`schema.py:190`) with
`DEFAULT 'shallow'`, and every existing row was written before that migration.
`write_finding()` classifies correctly today — `failure_diagnosis → deep` — but no finding
has been written since the feature shipped. So the stored layers are backfill artefacts,
not classifications. Correct effective distribution:

| Layer | Stored | Should be |
|---|---|---|
| shallow | 74 | 3 |
| intermediate | 0 | 1 |
| deep | 0 | **70** |

This is not cosmetic. Layer selects the decay constants, and `shallow` has `Q=14` days
against `deep`'s `Q=365`:

```
failure_diagnosis entry, age 118.9 days
  as stored   (shallow, Q=14):  recency = 0.000205
  as designed (deep,    Q=365): recency = 0.721979     # ~3,500x higher
```

**Consequence: `purge_kb()` would delete 72 of 74 entries** — `should_purge` fires when
recency < 0.05, and 72 rows are far below it. Loop 1 calls `purge_kb()` at step 9 of every
successful run. *The next successful Loop 1 run wipes the knowledge base.*

Actions, in order:

1. **Snapshot the DB** — done: `backups/trading_system.20260807-182019.db` (gitignored).
2. **Reclassify** the backfilled rows to their category-derived layer before any further
   Loop 1 run. Until then treat `purge_kb()` as unsafe.
3. Only then run this trial against stored layers.

The harness defaults to the **category-derived** layer so the trial is not poisoned by the
mislabel. `--stored-layer` reproduces the broken state for comparison.

---

## P1 — finding that reshaped this trial

The backlog ranks **T1** (ours vs FinMem canonical) and **T2** (alpha-vs-Q redundancy) as
High priority. Both were run against the real corpus during design. Result:

> **T1, T2 and the baseline produce byte-identical rankings on the current corpus.**

```
A_baseline       vs B_finmem         identical_ranking = True
A_baseline       vs C_double_decay   identical_ranking = True
B_finmem         vs C_double_decay   identical_ranking = True
A_baseline       vs D_no_boosts      identical_ranking = False
```

Cause — the corpus cannot separate them:

- 70 of 74 entries share one effective layer (`deep`), so they share `Q` and `alpha`.
- Importance takes only **3 distinct values** (40, 50, 60) — it is sampled from
  `_LAYER_BASE_VALUES`, not earned, because nothing has ever applied feedback.
- Within a single layer, `importance x recency` and `recency + importance·α^t/100` are
  monotone in the same two variables, so they induce the same order.

**T1 and T2 are unfalsifiable on this data.** Running them would produce a confident-looking
null result that says nothing about the formulas — only about the corpus. They are
correctly *deprioritised* until the KB has entries spread across layers with earned (not
sampled) importance, which requires T5 attribution to exist first.

The one lever that does move the ranking is the **context boosts**. That is what this trial
tests.

---

## Design

**Control**: one frozen corpus snapshot, one pinned `now` timestamp shared by all variants.
This matters — the shipped scoring functions call `time.time()` internally, so unpinned
runs are not reproducible.

Each variant differs from baseline in **exactly one** way, per
`.claude/rules/ablation_methodology.md`.

| Variant | Change from baseline | Tests |
|---|---|---|
| **A_baseline** | none — current `get_working_memory()`: `importance x recency`, x1.5 regime, x1.3 mechanism | control |
| **B_finmem** | scoring formula → FinMem canonical `recency + decayed_importance/100` | T1 |
| **C_double_decay** | importance also decays by `alpha` (both age-decays active) | T2 |
| **D_no_boosts** | the x1.5 / x1.3 context boosts removed | **the live question** |

B and C are retained as registered null arms — recording that they tie is the evidence for
the P1 claim above.

### Ground truth

`gold_ids` must be labelled **by hand**. The scenarios are real Loop 1 decision points
pulled from `strategy_evolutions`; for each, a human decides which KB entries a competent
analyst would want in front of them. This cannot be automated with the ranker or an LLM
scorer without circularity — using a retrieval system to grade retrieval systems just
measures agreement with the grader.

Target **≥12 labelled scenarios**. Label before looking at any variant output.

### Procedure

```bash
# 1. Emit the labelling template from real decision points
python -m scripts.trials.kb_retrieval_trial template --out trials_out/scenarios.csv

# 2. Hand-label gold_ids (browse entries: python3 -m src.gui.kb_gui)

# 3. Run the bake-off
python -m scripts.trials.kb_retrieval_trial run --scenarios trials_out/scenarios.csv

# 4. Optional — quantify the P0 damage
python -m scripts.trials.kb_retrieval_trial run --scenarios trials_out/scenarios.csv \
    --out-dir trials_out/stored_layer --stored-layer
```

Outputs `kb_retrieval_results.csv`, `kb_retrieval_summary.json`, and
`kb_retrieval_dotplot.svg` (self-contained SVG, no matplotlib dependency).

### The chart

A **paired dot plot**: one row per scenario, x-axis = rank of the first gold finding, one
dot per variant, connected by a line so movement is legible. Left is better. The shaded
band is the top-`k` region that actually reaches the agent's prompt.

This is the right chart because the comparison is *paired* — the same scenario under
different formulas — and n is small enough (~12) that showing every point beats showing a
mean with error bars. A bar chart of average rank would hide that most scenarios are
unchanged while one or two move a lot, which is exactly the pattern a ranking change
produces.

---

## Decision rule

Adopt a variant only if it **wins or ties on Recall@5 and improves median rank**, over ≥12
labelled scenarios. A change that improves MRR while dropping Recall@5 is surfacing one
good entry at the cost of the bundle — reject it.

If **D_no_boosts** wins, the regime/mechanism boosts are noise and should be removed —
a real simplification. If **A** wins, the boosts are earning their place and the multipliers
themselves become the next calibration sweep.

---

## Record result here

*Superseded 2026-08-09 — not run, and will not be run as specified.*

This trial scored retrieval against **hand-labelled `gold_ids`**: a human marks
which KB entries are "relevant" to a scenario, and each scoring variant is graded
on how well it recovers those marks. That measures agreement with one person's
guess about relevance, not whether better retrieval makes the system perform
better.

The replacement measures the outcome directly — repeat-failure rate, OOS composite
score, attempts-to-acceptance — which needs no labels and no live trades. See
[[2026-08-08-memory-design-options]].

The harness `scripts/trials/kb_retrieval_trial.py` still runs and is still useful
as a ranking-diff tool; only the label-based scoring is abandoned.

Design-phase runs used randomised gold labels for a smoke test only; those numbers are
meaningless and are not recorded. The P1 identical-ranking finding above **is** a real
result — it depends only on the corpus, not on the labels.

| Variant | MRR | Recall@5 | Median rank | Verdict |
|---|---|---|---|---|
| A_baseline | | | | |
| B_finmem | | | | |
| C_double_decay | | | | |
| D_no_boosts | | | | |

---

## Reopen conditions

- **Trade-linked evaluation**: revisit when `trades` holds ≥50 resolved win/loss rows.
- **T1 / T2**: revisit once importance is *earned* rather than sampled — needs T5
  (per-retrieved-memory attribution). Note `strategy_evolutions.kb_entries_used` already
  exists in the schema but is **NULL in all 47 rows**; nothing writes it. Populating it is
  the cheapest unblock for T5, T6, and eventually T1/T2.
