---
tags: [adr, knowledge-base, finmem, retrieval]
related: ["[[_decisions]]", "[[2026-04-15-finmem-layered-memory]]", "[[2026-08-09-kb-backfill-from-search-logs]]", "[[2026-08-07-kb-structure-measured]]", "[[agents]]"]
---

# Decision: Retrieval uses FinMem's faithful compound score (summed, scaled), not a product

**Date**: 2026-08-09

## Decision

`get_working_memory()` now ranks findings with FinMem's published compound score —
three terms, each in [0,1], **summed**:

```
score = S_recency + S_relevancy + S_importance
```

Concretely it calls the already-existing `compute_compound_score()` (recency +
decayed_importance/100) and adds a new `compute_structural_relevancy()` term.
The previous `importance × recency × 1.5 × 1.3` product and its multiplicative
regime/mechanism boosts are removed.

`S_relevancy` is computed from **structured tag matching** (regime 0.6 +
mechanism 0.4) rather than FinMem's embedding cosine — see Alternatives.

## Reason

Two findings forced this.

**The product form is what nearly deleted the knowledge base.** Multiplying an
unscaled importance (0–100) by a decayed recency (0–1) lets a small recency
annihilate a high-importance memory: a 119-day finding scored `0.0002`, below the
`0.05` purge floor. Under FinMem's sum the same memory still scores ~1.0 on its
importance term and survives. Our worst defect was a direct consequence of
departing from the paper.

**The faithful function already existed and nothing called it.**
`compute_compound_score()` has been in `memory_layers.py` since phase 06,
implementing `S_recency + I_current/100` correctly, while the live retrieval path
used the unfaithful product. This was a wiring gap, not a design disagreement.

Separately, FinMem specifies **three** score terms; we shipped two. Retrieval
could answer "what is important and recent?" but never "what is this *about*?"
The relevancy term closes that gap.

The only live consumer of KB retrieval is `loop1.get_working_memory()` (the
`handle_query_knowledge_base` tool handler is dead code from the pre-selector
architecture), so the blast radius of this change is one call site.

## Alternatives Considered

- **Keep the product, raise the purge floor** — rejected: treats the symptom.
  The product would still make recency dominate importance arbitrarily.
- **Embedding cosine for `S_relevancy`, as FinMem specifies** — deferred, not
  rejected. Anthropic offers no first-party embeddings endpoint and points to
  Voyage AI, so this adds a provider, an API key, and per-query cost.
  `voyage-finance-2` is domain-tuned and worth trialling. Structured tags are the
  zero-dependency starting point and use domain structure FinMem did not have
  (every finding already carries `regime`, `mechanism`, `conditions`).
- **Keep the ×1.5 / ×1.3 multiplicative boosts alongside the sum** — rejected:
  they are an unscaled stand-in for exactly the relevancy term being added, and
  measurement showed regime filtering makes bundles *more* redundant, not less.
- **Trial the formulas before adopting** — rejected as sequencing. The variants
  were provably unidentifiable on the old corpus, and the purge bug was live.
  Adopting the published baseline is the defensible default; the open question
  (structured tags vs embeddings) remains trialled.

## Consequences

- Purge exposure fell from 72/126 rows to 1 (a genuinely stale April note).
- Four new tests in `tests/test_knowledge_base.py` cover the sum-not-product
  behaviour and the relevancy term.
- `RELEVANCY_REGIME_WEIGHT` / `RELEVANCY_MECHANISM_WEIGHT` (0.6 / 0.4) are
  unvalidated starting values, and are now a calibration candidate alongside
  `COGNITIVE_SPAN_K` and the layer `Q` horizons.

## Related

- MOC: [[_decisions]] · [[agents]]
- Supersedes the scoring half of [[2026-04-15-finmem-layered-memory]]
- Depends on [[2026-08-09-kb-backfill-from-search-logs]] for a corpus that can
  distinguish scoring variants at all
- Trial: [[2026-08-07-kb-structure-measured]]
- Source: FinMem, arXiv:2311.13743
