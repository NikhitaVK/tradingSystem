# Decision: FinMem-style layered KB memory with RL importance feedback

**Date**: 2026-04-15

## Decision
Implement `src/data/memory_layers.py` (pure functions implementing FinMem importance scoring and layer transitions: working / warm / deep) and `src/data/memory_feedback.py` (RL-style importance boost when a KB entry contributed to a winning trade, hooked into `execution_agent._close_trade`).

## Reason
A flat KB grows unboundedly and noisy entries crowd out useful ones. FinMem layered memory mirrors human trader cognition (recent / consolidated / long-term) and gives a principled basis for purging and promotion. Importance feedback from realized trade outcomes creates a reinforcing loop: entries that demonstrably contributed to wins persist and rise in retrieval ranking. Losses incur no penalty — natural decay handles that.

## Alternatives Considered
- **Flat KB with recency-only ranking** — rejected: entries that are old but valuable get squeezed out
- **LLM-graded importance** — rejected: expensive and circular (LLM judging the input it later reads)
- **Loss penalty as well as win boost** — rejected: would punish entries that informed contextually-correct decisions that happened to lose


## Related

- MOC: [[agents]]
- [[phase_06_layered_memory_architecture]]
- [[2026-04-15-hmm-regime-detection]]
