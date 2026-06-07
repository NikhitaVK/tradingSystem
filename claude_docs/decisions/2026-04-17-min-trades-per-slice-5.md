# Decision: Lower BACKTEST_MIN_TRADES_PER_SLICE from 10 to 5

**Date**: 2026-04-17

## Decision
The minimum trades per walk-forward slice was halved from 10 to 5 alongside the move to crossover-based entries.

## Reason
Crossover entries fire only on the bar a crossover happens, producing far fewer total signals per slice than state-based entries that fire every bar the condition holds. Keeping the 10-trade minimum would have flagged well-designed crossover strategies as degenerate, biasing aggregation against the higher-quality event-driven candidates the pool was redesigned to favour.

## Alternatives Considered
- **Keep 10-trade minimum** — rejected: systematically excludes the better-designed crossover candidates
- **Drop trade minimum entirely** — rejected: lose protection against truly degenerate strategies with 0-1 trades


## Related

- MOC: [[backtesting]]
- [[2026-04-17-5-slices-5-attempts]]
