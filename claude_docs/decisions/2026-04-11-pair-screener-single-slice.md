# Decision: Pair screener uses lightweight single-slice ranking, not the full walk-forward engine

**Date**: 2026-04-11

## Decision
The Loop 1 pair screener does NOT call `engine.run_backtest()`. It runs an internal single-pass RSI(14) signal-count helper to rank candidate pairs and emits the top 5 to the strategy agent. Full walk-forward backtesting only runs after the strategy agent has selected a pair and produced a complete hypothesis.

## Reason
Walk-forward backtests are expensive (3-5 slices per spec). Running them across 20 candidate pairs just for ranking would be ~60-100x more compute than needed for a relative ordering. The screener only needs ordinal comparison, which a vanilla RSI signal-count benchmark provides at near-zero cost.

## Alternatives Considered
- **Full walk-forward per pair** — rejected: 100x compute cost for a ranking-only decision
- **Volume-only ranking** — rejected: insufficient signal; high-volume pairs aren't necessarily where a strategy would fire well


## Related

- MOC: [[backtesting]]
- [[agents]]
