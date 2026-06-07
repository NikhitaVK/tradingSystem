# Task: Build Module 2 — Backtest Engine

## Goal

Implement `src/backtest/` (indicators, engine, strategy_runner) and get all 7 isolation tests in `tests/test_backtest.py` passing.

## Relevant Context

- Module 1 is complete and all 6 tests pass
- `src/backtest/` is fully implemented with all 11 tests passing
- Strategy spec contract is defined in `claude_docs/modules/backtesting.md`
- Walk-forward methodology: 3 slices, 80/20 split, metrics on OOS only
- All 7 test criteria are in `claude_docs/modules/backtesting.md`

## Requirements

- Implement `src/backtest/indicators.py`: RSI, MACD, BB, EMA, ATR (pure functions, pd.Series in/out)
- Implement `src/backtest/strategy_runner.py`: `build_signals()` — causal, no look-ahead
- Implement `src/backtest/engine.py`: `run_backtest()` — walk-forward, cost model, calibration
- Timeframe-dependent Sharpe annualisation (use `PERIODS_PER_YEAR` dict, NOT `sqrt(252)`)
- NaN warm-up masking — zero signals during indicator warm-up period
- Data sufficiency check — raise `ValueError` if insufficient bars
- All 7 isolation tests pass before moving to Module 3

## Files Involved

- `src/backtest/indicators.py`
- `src/backtest/strategy_runner.py`
- `src/backtest/engine.py`
- `tests/test_backtest.py`
- `config/settings.py`

## Done When

- `pytest tests/test_backtest.py -v` shows 13/13 passing
- No look-ahead bias (test 2 passes with different results on shifted data)
- Degenerate strategy detection works (test 4)
- Degradation threshold floor enforced at 0.30 (test 5)


## Related

- MOC: [[_tasks]]
- [[backtesting]]
