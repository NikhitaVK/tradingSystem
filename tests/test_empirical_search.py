"""
test_empirical_search.py — Tests for the empirical backtest search engine.

Tests:
  1. Ranking order from injected mock results
  2. Early-termination path skips degenerate specs
  3. One aggregate log row per search cycle
  4. Empty result when all candidates fail
"""
import json
import os
import sqlite3
import tempfile
from unittest.mock import patch

import pytest

from src.agents.empirical_search import run_search, _compute_score


def _make_db():
    """Create a temp SQLite DB with the full schema. Returns db_path."""
    from src.data.schema import init_db
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    init_db(db_path)
    return db_path


def _make_backtest_result(pf: float, sharpe: float, trades: int, wfe: float = 0.7, viable: bool = True) -> dict:
    """Create a backtest result dict with controlled metrics."""
    return {
        "viable": viable,
        "aggregate": {
            "win_rate_mean": 0.55,
            "sharpe_mean": sharpe,
            "sortino_mean": sharpe * 1.1,
            "max_drawdown_worst": 0.08,
            "total_trades": trades,
            "profit_factor_mean": pf,
            "regime_breakdown": {
                "trending_bull": [1],
                "trending_bear": [],
                "sideways": [2, 3],
                "high_vol": [],
            },
        },
        "slices": [
            {"slice_id": 1, "sharpe": sharpe, "win_rate": 0.55, "total_trades": trades // 3, "regime": "trending_bull"},
            {"slice_id": 2, "sharpe": sharpe, "win_rate": 0.55, "total_trades": trades // 3, "regime": "sideways"},
            {"slice_id": 3, "sharpe": sharpe, "win_rate": 0.55, "total_trades": trades // 3, "regime": "sideways"},
        ],
        "calibration": {
            "degradation_threshold": 0.45,
            "walk_forward_efficiency": wfe,
            "position_sizing": {"method": "atr", "atr_period": 14, "atr_multiplier": 1.5, "risk_per_trade_pct": 0.01},
        },
    }


_SPEC_A = {"name": "Strategy_A", "symbol": "BTC/USDT", "timeframe": "1h", "mechanism": "momentum",
           "indicators": [], "entry": {"logic": "AND", "conditions": []}, "exit": {"logic": "OR", "conditions": []}}
_SPEC_B = {"name": "Strategy_B", "symbol": "BTC/USDT", "timeframe": "1h", "mechanism": "breakout",
           "indicators": [], "entry": {"logic": "AND", "conditions": []}, "exit": {"logic": "OR", "conditions": []}}
_SPEC_C = {"name": "Strategy_C", "symbol": "BTC/USDT", "timeframe": "1h", "mechanism": "volatility",
           "indicators": [], "entry": {"logic": "AND", "conditions": []}, "exit": {"logic": "OR", "conditions": []}}


def test_ranking_order():
    """Top-K should be sorted by composite score, best first."""
    db_path = _make_db()

    result_a = _make_backtest_result(pf=1.5, sharpe=1.0, trades=30, wfe=0.8)
    result_b = _make_backtest_result(pf=2.0, sharpe=1.5, trades=45, wfe=0.9)
    result_c = _make_backtest_result(pf=1.0, sharpe=0.5, trades=20, wfe=0.5)

    candidates = [_SPEC_A, _SPEC_B, _SPEC_C]
    results_map = {0: result_a, 1: result_b, 2: result_c}

    call_idx = {"n": 0}
    def mock_backtest(spec, db):
        idx = call_idx["n"]
        call_idx["n"] += 1
        return results_map[idx]

    with patch("src.agents.empirical_search.run_backtest", side_effect=mock_backtest):
        ranked = run_search(candidates, db_path, top_k=3)

    assert len(ranked) == 3
    # B should rank first (highest pf × wfe)
    assert ranked[0][0]["name"] == "Strategy_B"
    # Scores should be descending
    assert ranked[0][2] >= ranked[1][2] >= ranked[2][2]

    os.unlink(db_path)


def test_early_termination():
    """Candidates with too few trades should be skipped."""
    db_path = _make_db()

    # A: degenerate (2 trades — below CANDIDATE_EARLY_TERM_MIN_TRADES)
    result_a = _make_backtest_result(pf=0.5, sharpe=-2.0, trades=2)
    # B: viable
    result_b = _make_backtest_result(pf=1.8, sharpe=1.2, trades=40)

    candidates = [_SPEC_A, _SPEC_B]
    results_iter = iter([result_a, result_b])

    with patch("src.agents.empirical_search.run_backtest", side_effect=lambda s, d: next(results_iter)):
        ranked = run_search(candidates, db_path, top_k=3)

    # Only B should survive (A had too few trades)
    assert len(ranked) == 1
    assert ranked[0][0]["name"] == "Strategy_B"

    os.unlink(db_path)


def test_aggregate_log_row():
    """run_search should write exactly one reasoning_logs row per cycle."""
    db_path = _make_db()

    result = _make_backtest_result(pf=1.5, sharpe=1.0, trades=30)
    candidates = [_SPEC_A, _SPEC_B]

    with patch("src.agents.empirical_search.run_backtest", return_value=result):
        run_search(candidates, db_path)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT agent, thinking FROM reasoning_logs WHERE agent='empirical_search'"
    ).fetchall()
    conn.close()

    assert len(rows) == 1, f"Expected 1 aggregate log row, got {len(rows)}"
    log_data = json.loads(rows[0][1])
    assert len(log_data) == 2, "Log should contain entries for both candidates"

    os.unlink(db_path)


def test_empty_result_on_all_failures():
    """If all candidates fail backtest, return empty list."""
    db_path = _make_db()

    candidates = [_SPEC_A, _SPEC_B]

    with patch("src.agents.empirical_search.run_backtest", side_effect=ValueError("No data")):
        ranked = run_search(candidates, db_path)

    assert ranked == []

    os.unlink(db_path)


def test_compute_score_math():
    """Verify the composite score formula is correct."""
    result = _make_backtest_result(pf=2.0, sharpe=1.0, trades=30, wfe=0.8)
    score = _compute_score(result)

    # regime_breakdown: trending_bull=[1], sideways=[2,3] → 3 total, max=2
    # concentration = 2/3 ≈ 0.667
    # score = 2.0 * 0.8 * (1 - 0.667 + 0.1) = 1.6 * 0.433 ≈ 0.693
    assert 0.6 < score < 0.8, f"Expected score ~0.69, got {score}"
