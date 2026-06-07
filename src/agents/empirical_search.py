"""
empirical_search.py — Run backtest on each candidate spec and rank by composite score.

Replaces the LLM tool-use loop with exhaustive empirical evaluation.
All candidates are tested against the same walk-forward engine used by the
old strategy agent — the only difference is WHO generates the specs.
"""
import json
import logging
import time

from config.settings import (
    EMPIRICAL_SEARCH_TOP_K,
    CANDIDATE_EARLY_TERM_MIN_TRADES,
)
from src.backtest.engine import run_backtest
from src.data.schema import get_connection

logger = logging.getLogger(__name__)


def run_search(
    candidates: list,
    db_path: str,
    top_k: int = EMPIRICAL_SEARCH_TOP_K,
) -> list:
    """
    Backtest every candidate and return the top-K ranked by composite score.

    Args:
        candidates: List of strategy spec dicts from candidate_generator.
        db_path:    Path to SQLite DB with ohlcv_history.
        top_k:      How many survivors to return.

    Returns:
        List of (spec, backtest_results, score) tuples, sorted best-first.
        Empty list if no candidate clears the viability floor.
    """
    scored = []
    search_log = []

    for i, spec in enumerate(candidates):
        name = spec.get("name", f"candidate_{i}")
        try:
            results = run_backtest(spec, db_path)
        except (ValueError, Exception) as e:
            logger.debug("Candidate %s skipped: %s", name, e)
            search_log.append({"name": name, "error": str(e)[:200]})
            continue

        agg = results.get("aggregate", {})
        total_trades = agg.get("total_trades", 0)
        sharpe = agg.get("sharpe_mean", 0)

        # Early-terminate only on degenerate trade count.
        # Don't filter on Sharpe here — annualised Sharpe on 1h data amplifies
        # small negatives to -20+, which would kill every candidate. Let the
        # scoring function and analyst agent handle quality filtering.
        if total_trades < CANDIDATE_EARLY_TERM_MIN_TRADES:
            logger.debug(
                "Candidate %s early-terminated: trades=%d",
                name, total_trades,
            )
            search_log.append({
                "name": name, "trades": total_trades,
                "sharpe": sharpe, "early_term": True,
            })
            continue

        score = _compute_score(results)
        scored.append((spec, results, score))
        search_log.append({
            "name": name,
            "trades": total_trades,
            "sharpe": round(sharpe, 4),
            "pf": round(agg.get("profit_factor_mean", 0), 4),
            "score": round(score, 4),
            "viable": results.get("viable", False),
        })

        logger.info(
            "Candidate %s: trades=%d sharpe=%.2f pf=%.2f score=%.4f viable=%s",
            name, total_trades, sharpe,
            agg.get("profit_factor_mean", 0), score,
            results.get("viable", False),
        )

    _log_search_aggregate(search_log, db_path)

    # Viable candidates first, then non-viable — both sorted by score.
    # This ensures the LLM selector always sees the best viable options.
    viable = [s for s in scored if s[1].get("viable", False)]
    non_viable = [s for s in scored if not s[1].get("viable", False)]
    viable.sort(key=lambda x: x[2], reverse=True)
    non_viable.sort(key=lambda x: x[2], reverse=True)
    ranked = viable + non_viable
    return ranked[:top_k]


def _compute_score(results: dict) -> float:
    """
    Composite score: profit_factor × WFE × (1 − regime_concentration).

    Components:
      - profit_factor_mean: raw edge strength
      - walk_forward_efficiency: penalises in-sample/OOS divergence (overfitting)
      - regime_diversity: penalises strategies that only work in one regime
    """
    agg = results.get("aggregate", {})
    cal = results.get("calibration", {})

    pf = max(agg.get("profit_factor_mean", 0), 0)
    wfe = cal.get("walk_forward_efficiency")
    if wfe is None or wfe <= 0:
        wfe = 0.1

    regime = agg.get("regime_breakdown", {})
    total_slices = sum(len(v) for v in regime.values())
    if total_slices > 0:
        max_in_one = max(len(v) for v in regime.values())
        regime_concentration = max_in_one / total_slices
    else:
        regime_concentration = 1.0

    return pf * wfe * (1 - regime_concentration + 0.1)


def _log_search_aggregate(search_log: list, db_path: str) -> None:
    """Write one aggregate row to reasoning_logs for the whole search cycle."""
    try:
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO reasoning_logs (agent, thinking, response, created_at) "
            "VALUES (?, ?, ?, ?)",
            (
                "empirical_search",
                json.dumps(search_log),
                f"Evaluated {len(search_log)} candidates",
                int(time.time() * 1000),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("Failed to log search aggregate: %s", e)
