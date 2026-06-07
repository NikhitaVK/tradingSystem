# Module 2 — Backtesting Engine

**Status**: Complete  
**Isolation test**: `tests/test_backtest.py` — 11/11 passing  
**Depends on**: Module 1 (`ohlcv_history` table, schema)

## Purpose

Run walk-forward backtests on historical OHLCV data given a strategy spec. Produce a standardised results dict consumed by agents and Loop 1. Calibrate ATR-based position sizing and strategy-specific degradation thresholds. Zero look-ahead bias, realistic cost modelling.

## Strategy Spec Contract

JSON dict — the immutable contract between the backtest engine and all agents.

```json
{
  "name": "RSI Mean Reversion",
  "symbol": "BTC/USDT",
  "timeframe": "1h",
  "indicators": [
    {"type": "RSI", "period": 14},
    {"type": "EMA", "period": 50}
  ],
  "entry": {
    "logic": "AND",
    "conditions": [
      {"indicator": "RSI_14", "operator": "<", "value": 30},
      {"indicator": "price", "operator": ">", "value": "EMA_50"}
    ]
  },
  "exit": {
    "logic": "OR",
    "conditions": [
      {"indicator": "RSI_14", "operator": ">", "value": 70},
      {"type": "stop_loss_pct", "value": 2.0},
      {"type": "take_profit_pct", "value": 4.0}
    ]
  }
}
```

Supported indicators: `RSI`, `MACD`, `EMA`, `BB` (Bollinger Bands), `ATR`  
Supported operators: `<`, `>`, `<=`, `>=`, `==`, `crosses_above`, `crosses_below`  
Exit conditions: indicator-based, `stop_loss_pct`, `take_profit_pct`

## Walk-Forward Methodology

- **Slices**: 3 non-overlapping out-of-sample periods
- **Split**: 80% in-sample / 20% out-of-sample per slice
- **Metrics computed on out-of-sample only** — in-sample is indicator warm-up only
- **Aggregate**: mean across slices for win rate and Sharpe; worst-case slice for max drawdown
- **Minimum trades per slice**: 10 (flagged as degenerate if below)

```
Full history: |-------- slice 1 --------|-------- slice 2 --------|-------- slice 3 --------|
Each slice:   |-- in-sample (80%) --|-- out-of-sample (20%) --|
```

## Results Dict Format

```python
{
    "slices": [
        {
            "slice_id": 1, "start_date": "2024-01-01", "end_date": "2024-04-30",
            "win_rate": 0.54, "sharpe": 1.2, "max_drawdown": 0.08,
            "total_trades": 32, "pnl_pct": 0.14, "regime": "sideways",
            "degenerate": False,
        },
    ],
    "aggregate": {
        "win_rate_mean": 0.51, "sharpe_mean": 1.1,
        "max_drawdown_worst": 0.12, "total_trades": 95,
        "regime_breakdown": {"bull": [1, 2], "bear": [], "sideways": [3]},
    },
    "calibration": {
        "degradation_threshold": 0.41,   # mean - std of slice win rates, min 0.30
        "position_sizing": {
            "method": "atr", "atr_period": 14, "atr_multiplier": 1.5,
            "risk_per_trade_pct": 0.01,
        }
    },
    "viable": True   # False if degenerate, negative Sharpe, etc.
}
```

## Cost Model

| Parameter | Starting Value | Represents |
|---|---|---|
| Slippage | 0.1% per side | Assumed fill quality degradation |
| Exchange fee | 0.1% per side | Binance spot maker/taker |
| Total round-trip | 0.4% | Slippage + fees both ways |

Both applied to every simulated trade entry and exit before PnL calculation.

## Calibration Outputs

**Degradation threshold** (strategy-specific, statistically grounded):
```python
slice_win_rates = [s['win_rate'] for s in results['slices']]
degradation_threshold = max(mean(slice_win_rates) - std(slice_win_rates), 0.30)
```

**ATR position sizing**:
```python
atr = compute_atr(ohlcv_series, period=14)
stop_distance = atr.iloc[-1] * 1.5   # 1.5x ATR from entry
risk_amount = account_balance * 0.01  # 1% of account
position_size_usdt = risk_amount / (stop_distance / current_price)
```

## Key Files

### `src/backtest/indicators.py`
Pure functions — `pd.Series` in, `pd.Series` out. No side effects, no I/O.
- `compute_rsi(close, period=14) -> pd.Series`
- `compute_macd(close, fast=12, slow=26, signal=9) -> dict`
- `compute_bb(close, period=20, std=2.0) -> dict`
- `compute_ema(close, period) -> pd.Series`
- `compute_atr(high, low, close, period=14) -> pd.Series`

### `src/backtest/strategy_runner.py`
- `build_signals(ohlcv, strategy_spec) -> pd.Series` — 1 = enter long, -1 = exit, 0 = hold
- All causal (no look-ahead): `.shift(1)` applied before comparing signals to price

### `src/backtest/engine.py`
- `run_backtest(strategy_spec, db_path, n_slices=3) -> dict`

## Critical Implementation Notes

- **Sharpe annualisation is timeframe-dependent** — use `PERIODS_PER_YEAR` dict, not `sqrt(252)` on non-daily data
- **NaN warm-up masking** — zero out signals for the first `min_valid_bar` periods (RSI: 14, MACD: 26, BB: 20)
- **Data sufficiency check** — raise `ValueError` if bars < `n_slices * (1/(1-train_ratio)) * min_trades * 10`
- **HMM regime detection** is planned (Phase 1 of PLANNED_IMPROVEMENTS.md) — will replace naive `% return` regime tagging

## Isolation Test Criteria (13 tests)

1. Known strategy on synthetic data — RSI < 30 at known bars → trades fire exactly there
2. Look-ahead bias check — results must differ on shifted data
3. Cost deduction — 0.4% round-trip reduces PnL by ~0.4% per trade
4. Degenerate strategy detection — unreachable entry conditions → `viable: False`
5. Degradation threshold floor — always >= 0.30
6. ATR position size non-zero and reasonable for BTC/USDT on $10k account
7. Degenerate slice detection — < 10 trades flagged in results

## Known Issues

- None


## Related

- MOC: [[_modules]]
- [[2026-04-11-look-ahead-causal-truncation-test]]
- [[2026-04-11-pair-screener-single-slice]]
- [[2026-04-11-volume-stop-slippage-cost-model]]
- [[2026-04-15-hmm-regime-detection]]
- [[2026-04-15-multi-timeframe-confirmer]]
- [[2026-04-17-5-slices-5-attempts]]
- [[2026-04-17-crossover-entries-pure-sltp-exits]]
- [[2026-04-17-min-trades-per-slice-5]]
- [[2026-04-17-analyst-pf-threshold-recalibration]]
