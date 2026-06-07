# Decision: Enhanced cost model with stop-loss extra slippage and volume-proportional scaling

**Date**: 2026-04-11

## Decision
The backtest cost model was extended beyond a flat 0.4% round-trip: stop-loss exits incur extra slippage to model gap-through behaviour, and slippage scales proportionally with order size relative to bar volume. WFE (Walk-Forward Efficiency), Sortino, profit factor, expectancy, recovery factor, and per-regime breakdowns were added to the results dict in the same pass.

## Reason
Flat-rate slippage understates the cost of stop-outs (which often fill worse than expected due to fast moves) and ignores market impact on larger orders. Volume-proportional slippage is the standard non-linear model from the literature; quadratic in size beyond a volume fraction is most accurate. WFE was added because it directly measures backtest-to-live fit quality. Sortino is more appropriate than Sharpe for asymmetric crypto returns.

## Alternatives Considered
- **Keep flat 0.4% round-trip** — rejected: systematically over-rewards stop-heavy strategies
- **Full order-book simulation** — rejected: requires tick data the project doesn't have (MT4 tick volume only)
- **Funding-rate model** — rejected: out of scope until futures execution is wired


## Related

- MOC: [[backtesting]]
- [[2026-04-17-analyst-pf-threshold-recalibration]]
