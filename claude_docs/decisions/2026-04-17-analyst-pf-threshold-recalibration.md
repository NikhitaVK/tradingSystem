# Decision: Lower analyst profit-factor threshold from 1.5 to 1.3 and explicitly document the cost model

**Date**: 2026-04-17

## Decision
`analyst_eval` was updated to challenge profit factor against 1.3 (not 1.5) and to include explicit documentation that the reported PF is already post-cost (0.4% round-trip + stop-loss extra slippage + volume-proportional slippage). Statistical-significance minimum trades was also reduced from 30 to 20, with crossover sparsity called out as expected behaviour.

## Reason
The evaluator was double-penalising costs: the engine already deducts slippage and fees from PnL before computing PF, and then the prompt instructed the LLM to challenge cost realism on top. A post-cost PF of 1.3 already means gross profit exceeds gross loss by 30% after all modelled frictions. The 30-trade minimum was calibrated for state-based entry density; crossover strategies are trade-sparse by design.

## Alternatives Considered
- **Keep thresholds, remove cost-model wording from prompt** — rejected: cost realism is a legitimate concern, just shouldn't be applied twice
- **Remove statistical-significance criterion entirely** — rejected: still useful as one weighted criterion, just at a calibrated threshold


## Related

- MOC: [[backtesting]]
- [[agents]]
- [[2026-04-11-volume-stop-slippage-cost-model]]
