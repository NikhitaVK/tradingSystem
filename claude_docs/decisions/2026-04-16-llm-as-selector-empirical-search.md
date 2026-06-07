# Decision: Replace LLM tool-use loop with constrained DSL + empirical search + LLM-as-filter

**Date**: 2026-04-16

## Decision
The LLM strategy agent is repositioned from generator to *selector*. A deterministic `candidate_generator.py` emits a mechanism-diverse pool of strategy specs, `empirical_search.py` backtests and ranks them via a composite score, and Claude makes a single call to pick the best survivor and articulate its mechanism story.

## Reason
LLM prompting is the wrong tool for formula search on a low-signal, non-stationary process. The LLM was being asked to do three different jobs (mechanism hypothesis, indicator composition, parameter selection) which have very different optimal solvers. Repositioning the LLM to only the task it excels at (pattern-matching/selection and narrative) while letting deterministic search handle the alpha discovery is a much better fit.

## Alternatives Considered
- **Genetic Programming alone** — rejected: no semantic grounding of mechanism; produces uninterpretable strategies
- **Pure LLM hypothesis + tool-use backtest loop (previous design)** — rejected: high cost, low quality, hallucinated strategies
- **Hybrid GP + LLM** — considered but the constrained-DSL + LLM-as-filter variant was simpler to implement and preserves the strategy spec contract


## Related

- MOC: [[agents]]
- [[2026-04-17-crossover-entries-pure-sltp-exits]]
- [[2026-04-20-rejected-names-blacklist]]
