# Decision: Multi-timeframe trend confirmer as a signal gate

**Date**: 2026-04-15

## Decision
Implement `src/backtest/mtf_confirmer.py` exposing `get_higher_timeframes()`, `build_mtf_trend_filter()`, and `apply_mtf_confirm()`. It is wired into `strategy_runner.build_signals()` and gates base-timeframe signals against the higher-timeframe trend state. Higher-TF rows are aligned to base-TF timestamps via `pd.merge_asof(direction="backward")`.

## Reason
Single-timeframe strategies often fire counter-trend. A higher-timeframe trend gate is the standard fix. `merge_asof` backward-fill is the canonical way to align coarse-to-fine timestamps without introducing look-ahead. If higher-TF data is unavailable the filter no-ops gracefully (returns unmodified signals) so the system degrades rather than fails.

## Alternatives Considered
- **Naive resample/reindex with ffill** — rejected: easy to introduce look-ahead at slice boundaries
- **Require MTF confirmation hard-coded inside each strategy spec** — rejected: couples MTF logic to every template, hard to evolve


## Related

- MOC: [[backtesting]]
- [[phase_09_multitimeframe_confirmation]]
