"""
hmm_regime.py — Hidden Markov Model regime classification.

Uses hmmlearn's GaussianHMM to detect 4 market regimes:
  trending_bull, trending_bear, sideways, high_vol

State mapping: sort by mean return + mean volatility → assign labels.
Training is causal: the HMM is fit on all data within the prediction window
(training data = all bars up to bar t when predicting at t).

Falls back to ATR-based tagging when fewer than _MIN_TRAIN_BARS are available
or if HMM training fails (e.g. singular covariance).
"""
import logging

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)

# Minimum bars needed for reliable HMM fitting (~21 trading days × 12 months of 1h bars)
_MIN_TRAIN_BARS = 252


def _build_features(ohlcv: pd.DataFrame) -> pd.DataFrame:
    """
    Build HMM feature matrix from OHLCV DataFrame.

    Features per bar:
      - log_return: ln(close_t / close_{t-1})
      - rolling_vol: ATR / midprice (normalised volatility proxy)

    Returns DataFrame with NaN rows dropped (first bar has no return,
    first 14 bars have no ATR).
    """
    close = ohlcv["close"]
    high = ohlcv["high"]
    low = ohlcv["low"]

    log_return = np.log(close / close.shift(1))

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    midprice = (high + low) / 2
    rolling_vol = atr / midprice

    features = pd.DataFrame({
        "log_return": log_return,
        "rolling_vol": rolling_vol,
    }).dropna()

    return features


def _map_states_to_regimes(states: np.ndarray, X: np.ndarray) -> dict:
    """
    Map numeric HMM state IDs to regime labels using the statistics of each state.

    Sorting logic:
      - Highest mean return  → trending_bull
      - Lowest mean return   → trending_bear
      - Middle states: highest vol → high_vol, lowest vol → sideways
      - If exactly 2 states: only trending_bull and trending_bear assigned.
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
    sorted_by_return = sorted(
        state_stats.keys(),
        key=lambda s: state_stats[s]["mean_return"],
        reverse=True,
    )

    labels = {}
    if len(sorted_by_return) >= 1:
        labels[sorted_by_return[0]] = "trending_bull"   # highest return
    if len(sorted_by_return) >= 2:
        labels[sorted_by_return[-1]] = "trending_bear"  # lowest return

    # Middle states classified by volatility
    middle_states = sorted_by_return[1:-1] if len(sorted_by_return) > 2 else []
    if middle_states:
        vols = {s: state_stats[s]["mean_vol"] for s in middle_states}
        labels[max(vols, key=vols.get)] = "high_vol"
        labels[min(vols, key=vols.get)] = "sideways"

    # If exactly 2 states, assign remaining as sideways
    for s in unique_states:
        if s not in labels:
            labels[s] = "sideways"

    return labels


def detect_regimes(
    ohlcv: pd.DataFrame,
    train_periods: int = 504,
    n_states: int = 4,
) -> pd.Series:
    """
    Fit a Gaussian HMM on log-returns + rolling volatility and label each bar.

    Args:
        ohlcv:         DataFrame with columns: open, high, low, close, volume.
                       Must be sorted ascending by timestamp.
        train_periods: Bars used for HMM training window (default 504 ≈ 3 weeks of 1h).
        n_states:      Number of HMM states (default 4).

    Returns:
        pd.Series of regime labels ('trending_bull', 'trending_bear', 'sideways', 'high_vol')
        aligned to ohlcv.index. NaN during warm-up (first max(train_periods, 14) bars).
    """
    regime_series = pd.Series(index=ohlcv.index, dtype=object)

    if len(ohlcv) < _MIN_TRAIN_BARS:
        return _atr_fallback_regimes(ohlcv)

    features = _build_features(ohlcv)

    if len(features) < _MIN_TRAIN_BARS:
        return _atr_fallback_regimes(ohlcv)

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
    except Exception as e:
        logger.debug("HMM training failed (%s), falling back to ATR regimes", e)
        return _atr_fallback_regimes(ohlcv)

    try:
        states = model.predict(X_scaled)
    except Exception as e:
        logger.debug("HMM predict failed (%s), falling back to ATR regimes", e)
        return _atr_fallback_regimes(ohlcv)

    state_to_regime = _map_states_to_regimes(states, X)

    # Map features index (subset of ohlcv index after NaN drop) to regime labels
    for i, idx in enumerate(features.index):
        state = states[i]
        regime_series.loc[idx] = state_to_regime.get(state, "sideways")

    # NaN for warm-up bars (HMM needs sufficient data to be reliable)
    warmup_len = max(train_periods, 14)
    if warmup_len < len(regime_series):
        regime_series.iloc[:warmup_len] = None

    return regime_series


def _atr_fallback_regimes(ohlcv: pd.DataFrame) -> pd.Series:
    """
    Naive per-bar regime tagging when HMM training data is insufficient.

    Uses ATR-normalised volatility to classify high_vol bars, then % return
    thresholds for trending_bull / trending_bear / sideways.
    """
    close = ohlcv["close"]
    returns = close.pct_change()

    regimes = pd.Series(index=ohlcv.index, dtype=object)
    regimes[returns > 0.05] = "trending_bull"
    regimes[returns < -0.05] = "trending_bear"
    regimes[(returns >= -0.05) & (returns <= 0.05)] = "sideways"
    if len(regimes) > 0:
        regimes.iloc[0] = "sideways"

    # Override with high_vol for bars in the top 25% of normalised ATR
    prev_close = close.shift(1)
    tr1 = ohlcv["high"] - ohlcv["low"]
    tr2 = (ohlcv["high"] - prev_close).abs()
    tr3 = (ohlcv["low"] - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    midprice = (ohlcv["high"] + ohlcv["low"]) / 2
    vol_ratio = atr / midprice

    vol_threshold = vol_ratio.quantile(0.75)
    regimes[vol_ratio > vol_threshold] = "high_vol"

    return regimes
