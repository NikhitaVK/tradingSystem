"""
test_candidate_generator.py — Tests for the deterministic strategy spec emitter.

Tests:
  1. Mechanism diversity: no single class > 40% of candidates
  2. Every spec round-trips through _normalise_spec without error
  3. Every spec produces valid signals on fixture OHLCV via build_signals
  4. KB blacklist actually removes candidates
  5. Generator is deterministic for a given regime/pair
"""
import numpy as np
import pandas as pd
import pytest

from src.agents.candidate_generator import generate_candidate_pool, MECHANISM_CLASSES


def _make_fixture_ohlcv(n_bars: int = 500) -> pd.DataFrame:
    """Generate synthetic OHLCV data for signal validation."""
    np.random.seed(42)
    close = 50000 + np.cumsum(np.random.randn(n_bars) * 100)
    return pd.DataFrame({
        "timestamp": range(n_bars),
        "open": close - np.random.rand(n_bars) * 50,
        "high": close + np.random.rand(n_bars) * 100,
        "low": close - np.random.rand(n_bars) * 100,
        "close": close,
        "volume": np.random.rand(n_bars) * 1000 + 100,
    })


def test_mechanism_diversity():
    """No single mechanism class should exceed 40% of the candidate pool."""
    candidates = generate_candidate_pool("sideways", {"symbol": "BTC/USDT"})
    assert len(candidates) > 0

    from collections import Counter
    mechanisms = Counter(c.get("mechanism", "unknown") for c in candidates)

    for mechanism, count in mechanisms.items():
        pct = count / len(candidates)
        assert pct <= 0.40, (
            f"Mechanism '{mechanism}' is {pct:.0%} of pool ({count}/{len(candidates)}), "
            f"exceeds 40% diversity limit"
        )


def test_both_directions_and_mechanism_diversity():
    """Pool should contain both long and short candidates with diverse mechanisms."""
    candidates = generate_candidate_pool("sideways", {"symbol": "BTC/USDT"})
    directions = set(c.get("direction", "long") for c in candidates)
    mechanisms = set(c.get("mechanism") for c in candidates)

    assert "long" in directions, "Pool must contain long candidates"
    assert "short" in directions, "Pool must contain short candidates"
    assert len(mechanisms) >= 3, (
        f"Pool should have at least 3 distinct mechanism classes, got {len(mechanisms)}: {mechanisms}"
    )


def test_specs_normalise_cleanly():
    """Every emitted spec should pass through _normalise_spec without error."""
    from src.agents.tools import _normalise_spec

    candidates = generate_candidate_pool("trending_bull", {"symbol": "ETH/USDT"})
    for spec in candidates:
        normalised = _normalise_spec(spec)
        assert "name" in normalised
        assert "indicators" in normalised
        assert "entry" in normalised
        assert "exit" in normalised
        for ind in normalised["indicators"]:
            assert "type" in ind, f"Indicator missing 'type' key: {ind}"


def test_specs_produce_valid_signals():
    """Every spec should produce a signal Series on fixture OHLCV without KeyError."""
    from src.backtest.strategy_runner import build_signals

    ohlcv = _make_fixture_ohlcv()
    candidates = generate_candidate_pool("sideways", {"symbol": "BTC/USDT", "timeframe": "1h"})

    for spec in candidates:
        signals = build_signals(ohlcv, spec)
        assert len(signals) == len(ohlcv), f"Signal length mismatch for {spec['name']}"
        assert set(signals.unique()).issubset({-1, 0, 1}), f"Invalid signal values for {spec['name']}"


def test_kb_blacklist_removes_candidates():
    """Blacklisting mechanism classes should remove all candidates of those classes."""
    full = generate_candidate_pool("sideways", {"symbol": "BTC/USDT"})
    blacklist = ["mean_reversion", "mean_reversion_short"]
    filtered = generate_candidate_pool("sideways", {"symbol": "BTC/USDT"}, kb_blacklist=blacklist)

    full_mr = [c for c in full if c.get("mechanism") in blacklist]
    filt_mr = [c for c in filtered if c.get("mechanism") in blacklist]

    assert len(full_mr) > 0, "Full pool should contain mean_reversion candidates"
    assert len(filt_mr) == 0, "Blacklisted pool should contain no mean_reversion candidates"


def test_deterministic_output():
    """Same inputs should produce the same candidate pool."""
    pair = {"symbol": "BTC/USDT", "timeframe": "1h"}
    pool1 = generate_candidate_pool("sideways", pair)
    pool2 = generate_candidate_pool("sideways", pair)

    assert len(pool1) == len(pool2)
    for s1, s2 in zip(pool1, pool2):
        assert s1["name"] == s2["name"]
        assert s1["mechanism"] == s2["mechanism"]


def test_rr_contract_enforced():
    """Every spec must have take_profit >= 2x stop_loss (hard R:R rule)."""
    candidates = generate_candidate_pool("sideways", {"symbol": "BTC/USDT"})
    for spec in candidates:
        sl = tp = None
        for cond in spec["exit"]["conditions"]:
            if cond.get("type") == "stop_loss_pct":
                sl = cond["value"]
            if cond.get("type") == "take_profit_pct":
                tp = cond["value"]
        assert sl is not None, f"{spec['name']} missing stop_loss_pct"
        assert tp is not None, f"{spec['name']} missing take_profit_pct"
        assert tp >= 2 * sl, f"{spec['name']}: TP={tp} < 2×SL={sl}, violates R:R contract"
