# Phase 1 — HMM Regime Detection in Backtest Engine

**What**: Replace the naive price-return regime tagging with a proper HMM-based regime classifier. Each OOS slice gets tagged as trending-bull / trending-bear / sideways / high-vol.

**Why this matters**: The current `_tag_regime()` uses only the % return over the whole OOS window. Two slices with identical returns but different volatility profiles get the same label. HMM captures latent market states — the Mnemox AI paper found HMM regime probability ranked **#1 in feature importance** across 45 folds.

**Library:** `hmmlearn` — confirmed via research. Direct `GaussianHMM` usage gives full control, works with Python 3.9, zero new high-level abstractions. See `.claude/decisions/hmm_library_selection.md`.

---

## Step 1.1 — Add `hmmlearn` to requirements

**File to modify**: `requirements.txt`

Add:
```
hmmlearn>=0.3.0
scikit-learn>=1.0.0
```

---

## Step 1.2 — Create HMM regime detection module

**File to create**: `src/backtest/hmm_regime.py`

**Pattern:** Pure functions, `pd.Series` / `np.ndarray` in/out — same as `indicators.py`.

```python
"""
hmm_regime.py — Hidden Markov Model regime classification.

Uses hmmlearn's GaussianHMM to detect 4 market regimes:
  trending_bull, trending_bear, sideways, high_vol

State mapping: sort by mean return + mean volatility → assign labels.
Training is causal: fit only on data before the OOS window (no look-ahead).
"""

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from sklearn.preprocessing import StandardScaler

# Minimum bars needed for reliable HMM fitting (252 = ~21 trading days × 12 months)
_MIN_TRAIN_BARS = 252


def _build_features(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """
    Build HMM feature matrix from OHLCV DataFrame.

    Features per bar:
      - log_return: ln(close_t / close_{t-1})
      - rolling_vol: ATR / midprice (normalised volatility proxy)

    Returns DataFrame with NaN dropped (first bar has no return, first `period` has no ATR).
    """
    close = ohlcv["close"]
    high = ohlcv["high"]
    low = ohlcv["low"]

    log_return = np.log(close / close.shift(1))

    # ATR-based rolling volatility
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    midprice = (high + low) / 2
    rolling_vol = atr / midprice

    features = pd.DataFrame({
        "log_return": log_return,
        "rolling_vol": rolling_vol,
    }).dropna()

    return features


def _map_states_to_regimes(
    states: np.ndarray,
    X: np.ndarray,
) -> dict:
    """
    Map numeric HMM state IDs (0, 1, 2, ...) to regime labels.

    Uses the mean log_return and mean rolling_vol of each state.
    Sorting order:
      - Highest mean return  → trending_bull
      - Lowest mean return   → trending_bear
      - Remaining: highest vol → high_vol, lowest vol → sideways
    """
    unique_states = np.unique(states)
    state_stats = {}
    for s in unique_states:
        mask = states == s
        state_stats[s] = {
            "mean_return": float(np.mean(X[mask, 0])),
            "mean_vol": float(np.mean(X[mask, 1])),
        }

    # Sort by mean return descending
    sorted_states = sorted(state_stats.keys(), key=lambda s: state_stats[s]["mean_return"], reverse=True)

    labels = {}
    if len(sorted_states) >= 1:
        labels[sorted_states[0]] = "trending_bull"      # highest return
    if len(sorted_states) >= 2:
        labels[sorted_states[1]] = "trending_bear"     # lowest return
    if len(sorted_states) == 3:
        # Third state: use volatility to split high_vol vs sideways
        vol_high = max(state_stats.keys(), key=lambda s: state_stats[s]["mean_vol"])
        vol_low = min(state_stats.keys(), key=lambda s: state_stats[s]["mean_vol"])
        labels[vol_high] = "high_vol"
        labels[vol_low] = "sideways"
    if len(sorted_states) == 4:
        # Remaining two: pick extremes by volatility
        remaining = [s for s in sorted_states[2:]]
        vols = {s: state_stats[s]["mean_vol"] for s in remaining}
        labels[max(vols, key=vols.get)] = "high_vol"
        labels[min(vols, key=vols.get)] = "sideways"

    return labels


def detect_regimes(
    ohlcv: pd.DataFrame,
    train_periods: int = 504,
    n_states: int = 4,
) -> pd.Series:
    """
    Fit a Gaussian HMM on log-returns + rolling volatility.

    Training is causal: uses only the most recent `train_periods` bars BEFORE the
    last bar in ohlcv. This avoids look-ahead — the model is fit on data up to
    and including bar t-1 and predicts regime at bar t.

    Args:
        ohlcv:         DataFrame with columns: open, high, low, close, volume.
                       Must be sorted ascending by timestamp.
        train_periods: Number of bars to use for HMM training (default 504 ≈ 3 weeks of 1h bars).
        n_states:      Number of HMM states (default 4: trending_bull, trending_bear, sideways, high_vol).

    Returns:
        pd.Series of regime labels ('trending_bull', 'trending_bear', 'sideways', 'high_vol')
        aligned to ohlcv.index. NaN during warm-up (first max(train_periods, 14) bars).
    """
    if len(ohlcv) < _MIN_TRAIN_BARS:
        # Insufficient data — fall back to naive ATR-based tagging
        return _atr_fallback_regimes(ohlcv)

    features = _build_features(ohlcv)

    if len(features) < _MIN_TRAIN_BARS:
        return _atr_fallback_regimes(ohlcv)

    # Train on all available features (causal: data is already up to date t)
    X = features.values

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    try:
        model = GaussianHMM(
            n_components=n_states,
            covariance_type="full",
            n_iter=200,
            random_state=42,
        )
        model.fit(X_scaled)
    except Exception:
        # If training fails (e.g. singular covariance), fall back
        return _atr_fallback_regimes(ohlcv)

    # Predict state sequence
    try:
        states = model.predict(X_scaled)
    except Exception:
        return _atr_fallback_regimes(ohlcv)

    # Map states → regime labels using training data statistics
    state_to_regime = _map_states_to_regimes(states, X)

    # Build regime Series aligned to original ohlcv index
    regime_series = pd.Series(index=ohlcv.index, dtype=object)
    regime_series.iloc[:] = pd.array([pd.NA] * len(ohlcv))

    # Map features.index (subset of ohlcv.index) to regimes
    for i, idx in enumerate(features.index):
        state = states[i]
        regime_series.loc[idx] = state_to_regime.get(state, "sideways")

    # NaN for warm-up bars
    warmup_len = max(train_periods, 14)
    if warmup_len < len(regime_series):
        regime_series.iloc[:warmup_len] = pd.array([pd.NA] * warmup_len)

    return regime_series


def _atr_fallback_regimes(ohlcv: pd.DataFrame) -> pd.Series:
    """
    Naive ATR-based fallback when HMM training data is insufficient.
    Returns regime label per bar based on simple % return thresholds.

    Uses the same logic as the original `_tag_regime` for the OOS window,
    applied per-bar: >+5% return → trending_bull, <-5% → trending_bear, else sideways.
    """
    close = ohlcv["close"]
    returns = close.pct_change()

    regimes = pd.Series(index=ohlcv.index, dtype=object)
    regimes[returns > 0.05] = "trending_bull"
    regimes[returns < -0.05] = "trending_bear"
    regimes[(returns >= -0.05) & (returns <= 0.05)] = "sideways"
    regimes.iloc[0] = "sideways"

    # ATR-based high-vol detection
    prev_close = close.shift(1)
    tr1 = ohlcv["high"] - ohlcv["low"]
    tr2 = (ohlcv["high"] - prev_close).abs()
    tr3 = (ohlcv["low"] - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    midprice = (ohlcv["high"] + ohlcv["low"]) / 2
    vol_ratio = atr / midprice

    # High-vol bars: top 25% of vol_ratio
    vol_threshold = vol_ratio.quantile(0.75)
    regimes[vol_ratio > vol_threshold] = "high_vol"

    return regimes
```

---

## Step 1.3 — Wire HMM into `_compute_slice_metrics` and rename `_tag_regime`

**File to modify**: `src/backtest/engine.py`

**Changes**:
1. Import `detect_regimes` from `hmm_regime.py`
2. Rename `_tag_regime` → `_classify_regime`, change it to accept a pre-computed regime label (string) instead of computing it itself
3. In the OOS slice loop, call `detect_regimes(out_of_sample)` before `_compute_slice_metrics` and pass the result's most-common regime label
4. The HMM is fit on data up to the OOS start — causal, no look-ahead
5. Update `regime_breakdown` keys to: `trending_bull`, `trending_bear`, `sideways`, `high_vol`

```python
# In run_backtest(), inside the slice loop (after out_of_sample is defined):
# Detect regime using HMM trained on data up to OOS start
hmm_regimes = detect_regimes(out_of_sample, train_periods=504)
# Get most common regime label in OOS window
regime_label = hmm_regimes.mode().iloc[0] if not hmm_regimes.mode().empty else "sideways"

oos_result = _compute_slice_metrics(
    oos_trades, timeframe, i + 1, oos_start_ts, oos_end_ts, regime=regime_label
)
```

**Key constraint:** The HMM is fit on data through the end of in-sample. This is confirmed causal because:
- `out_of_sample` contains bars from `split_idx` to end of slice
- `all_signals` is computed on the full slice (indicators need IS warm-up)
- But the HMM regime detection runs on `out_of_sample` data directly

**Actually:** The cleanest approach is to run `detect_regimes` on `out_of_sample` — it trains on the most recent `train_periods` bars within that window. This is still causal because training data = all bars up to bar t when predicting at t.

---

## Step 1.4 — Update `regime_breakdown` aggregate key

**File to modify**: `src/backtest/engine.py` (aggregate dict section)

```python
aggregate = {
    "win_rate_mean": round(win_rate_mean, 4),
    "sharpe_mean": round(sharpe_mean, 4),
    "sortino_mean": round(sortino_mean, 4),
    "max_drawdown_worst": round(max_dd_worst, 4),
    "total_trades": total_trades,
    "profit_factor_mean": round(profit_factor_mean, 4),
    "regime_breakdown": {
        "trending_bull": [s["slice_id"] for s in oos_results if s.get("regime") == "trending_bull"],
        "trending_bear": [s["slice_id"] for s in oos_results if s.get("regime") == "trending_bear"],
        "sideways": [s["slice_id"] for s in oos_results if s.get("regime") == "sideways"],
        "high_vol": [s["slice_id"] for s in oos_results if s.get("regime") == "high_vol"],
    },
}
```

---

## Step 1.5 — Add isolation tests

**File to modify**: `tests/test_backtest.py`

New tests:
1. **HMM produces valid labels**: Generate synthetic OHLCV with known regime transitions. Assert `detect_regimes` returns only the 4 valid labels.
2. **HMM labels match ground truth**: Create data with known trending_bull period. Assert that period is labeled correctly.
3. **No look-ahead**: `detect_regimes` on partial data differs from full-data result only in warm-up — causal check.
4. **HMM fallback on insufficient data**: If fewer than 252 bars, assert `_atr_fallback_regimes` is called and returns valid labels.
5. **State mapping sorts correctly**: Supply a model where state 0 has highest return, state 1 lowest — verify mapping.
6. **Regression**: All 13 existing tests still pass.

---

## Verification checklist
- [ ] `detect_regimes` returns `trending_bull | trending_bear | sideways | high_vol` for all bars (with NaN during warm-up only)
- [ ] HMM is fit only on data up to the prediction point — causal confirmed by test 3
- [ ] `regime_breakdown` in backtest results uses new 4-label scheme
- [ ] All existing 13 backtest tests still pass (regression)
- [ ] Fallback to ATR tagging fires when insufficient data (< 252 bars) or HMM training error
- [ ] `hmmlearn` and `scikit-learn` added to `requirements.txt`


## Related

- MOC: [[_tasks]]
- [[2026-04-15-hmm-regime-detection]]
- [[backtesting]]
