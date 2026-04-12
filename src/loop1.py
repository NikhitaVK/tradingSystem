"""
loop1.py — Full Loop 1 orchestration: strategy discovery and validation.

Flow:
  1. screen_pair_universe()           → top 5 liquid pairs by RSI signal density
  2. kb.query_relevant([...])         → prior findings as context
  3. strategy_agent.generate(...)     → strategy_spec + backtest_results
  4. analyst_agent.evaluate(...)      → {pass, diagnosis, challenges}
     FAIL → kb.write_finding(...)     → retry with diagnosis, attempt += 1
             if attempt == max_attempts → raise MaxAttemptsExceeded
     PASS →
  5. run_backtest(strategy_spec)      → final calibration (IS+OOS)
  6. save_validated_strategy(...)     → strategies table
  7. return strategy dict

Each attempt passes only the MOST RECENT failure diagnosis to the strategy agent.
All diagnoses accumulate in the KB — the agent can query them via query_knowledge_base.
"""
import json
import logging
import time
from typing import Optional

import ccxt

from config.settings import (
    LOOP1_MAX_ATTEMPTS,
    UNIVERSE_MIN_VOLUME_USD,
    UNIVERSE_MAX_CANDIDATES,
    UNIVERSE_TOP_N,
    ANTHROPIC_API_KEY,
)
from src.data.knowledge_base import write_finding, query_relevant
from src.backtest.engine import run_backtest
from src.agents.claude_client import ClaudeClient
from src.agents.mcp_client import MCPClient
from src.agents import strategy_agent, analyst_agent
from src.agents.tools import handle_save_validated_strategy

logger = logging.getLogger(__name__)


class MaxAttemptsExceeded(Exception):
    pass


def run_loop1(db_path: str, max_attempts: int = LOOP1_MAX_ATTEMPTS) -> dict:
    """
    Full Loop 1 orchestration. Returns the validated strategy dict.

    Args:
        db_path:      Path to SQLite DB.
        max_attempts: Max strategy discovery iterations before giving up.

    Returns:
        Strategy dict (from strategies table row) including spec, calibration, id.

    Raises:
        MaxAttemptsExceeded: If no valid strategy found after max_attempts.
    """
    client = ClaudeClient(db_path=db_path, api_key=ANTHROPIC_API_KEY)
    mcp = _init_mcp()

    # Step 1: screen pair universe
    logger.info("Loop 1: screening pair universe...")
    candidates = screen_pair_universe(db_path)
    logger.info("Loop 1: %d candidate pairs selected", len(candidates))

    # Step 2: load KB context
    kb_context = query_relevant(
        ["failure", "regime", "overfitting", "mechanism", "RSI", "EMA", "MACD"],
        db_path,
        limit=10,
    )

    last_diagnosis: Optional[str] = None

    for attempt in range(1, max_attempts + 1):
        logger.info("Loop 1: attempt %d/%d", attempt, max_attempts)

        # Step 3: generate strategy
        try:
            spec, backtest_results = strategy_agent.generate_strategy(
                pair_candidates=candidates,
                kb_context=kb_context,
                client=client,
                db_path=db_path,
                mcp_client=mcp,
                previous_diagnosis=last_diagnosis,
            )
        except ValueError as e:
            logger.warning("Strategy agent failed on attempt %d: %s", attempt, e)
            last_diagnosis = str(e)
            continue

        if spec is None:
            logger.warning("Strategy agent returned no spec on attempt %d", attempt)
            continue

        # Step 4: analyst evaluation (Debate CP1)
        eval_result = analyst_agent.evaluate(spec, backtest_results, client)

        if not eval_result["pass"]:
            diagnosis = eval_result["diagnosis"]
            logger.info("Analyst rejected strategy (attempt %d): %s", attempt, diagnosis[:100])

            write_finding(
                category="failure_diagnosis",
                content=(
                    f"Attempt {attempt} failure.\n"
                    f"Strategy: {spec.get('name', 'unnamed')}\n"
                    f"Diagnosis: {diagnosis}\n"
                    f"Challenges: {json.dumps(eval_result['challenges'])}"
                ),
                db_path=db_path,
            )
            last_diagnosis = diagnosis

            if attempt == max_attempts:
                raise MaxAttemptsExceeded(
                    f"Loop 1 exhausted {max_attempts} attempts. Last diagnosis: {diagnosis}"
                )
            continue

        # PASS — strategy survived adversarial evaluation
        logger.info("Analyst approved strategy: %s", spec.get("name", "unnamed"))

        # Step 5: final calibration backtest
        try:
            final_results = run_backtest(spec, db_path)
        except Exception as e:
            logger.warning("Final backtest failed: %s. Using strategy_agent backtest results.", e)
            final_results = backtest_results

        # Step 6: save to DB
        strategy_id = handle_save_validated_strategy(spec, final_results, db_path)
        logger.info("Strategy saved with id=%d", strategy_id)

        # Step 7: return strategy dict
        return {
            "id": strategy_id,
            "name": spec.get("name"),
            "symbol": spec.get("symbol"),
            "timeframe": spec.get("timeframe"),
            "spec": spec,
            "performance": final_results.get("aggregate", {}),
            "calibration": final_results.get("calibration", {}),
            "viable": final_results.get("viable", True),
        }

    raise MaxAttemptsExceeded(f"Loop 1 exhausted {max_attempts} attempts without a viable strategy.")


def screen_pair_universe(db_path: str) -> list[dict]:
    """
    Lightweight pair screening using CCXT.

    1. Fetch USDT spot pairs with 24h volume > UNIVERSE_MIN_VOLUME_USD
    2. Cap at UNIVERSE_MAX_CANDIDATES
    3. Rank by RSI(14) signal density (single-slice, not full walk-forward)
    4. Return top UNIVERSE_TOP_N

    Falls back to BTC/USDT + ETH/USDT if CCXT is unavailable.
    """
    try:
        exchange = ccxt.binance({"enableRateLimit": True})
        tickers = exchange.fetch_tickers()
    except Exception as e:
        logger.warning("CCXT ticker fetch failed: %s. Using fallback pairs.", e)
        return _fallback_candidates(db_path)

    # Filter: USDT quote, volume > threshold
    candidates = []
    for symbol, ticker in tickers.items():
        if not symbol.endswith("/USDT"):
            continue
        quote_vol = ticker.get("quoteVolume") or 0
        if quote_vol < UNIVERSE_MIN_VOLUME_USD:
            continue
        candidates.append({"symbol": symbol, "volume_usdt": quote_vol})

    # Sort by volume desc, cap candidates
    candidates.sort(key=lambda x: x["volume_usdt"], reverse=True)
    candidates = candidates[:UNIVERSE_MAX_CANDIDATES]

    if not candidates:
        return _fallback_candidates(db_path)

    # Score by RSI signal density from historical data
    scored = _score_candidates(candidates, db_path)

    # Return top N with metrics
    return scored[:UNIVERSE_TOP_N]


def _score_candidates(candidates: list[dict], db_path: str) -> list[dict]:
    """
    Score each candidate by RSI(14) signal density on recent 90-day data.
    Higher signal count = more active pair = better for strategy testing.
    Does NOT call run_backtest() — lightweight single-pass scoring only.
    """
    import sqlite3
    import pandas as pd
    from src.backtest.indicators import compute_rsi

    scored = []
    for c in candidates:
        symbol = c["symbol"]
        try:
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT close FROM ohlcv_history WHERE symbol=? AND timeframe='1h' "
                "ORDER BY timestamp DESC LIMIT 2160",  # ~90 days of 1h bars
                (symbol,),
            ).fetchall()
            conn.close()

            if len(rows) < 100:
                c["signal_count"] = 0
                c["sharpe"] = 0.0
                scored.append(c)
                continue

            closes = pd.Series([r[0] for r in reversed(rows)])
            rsi = compute_rsi(closes, 14)
            signal_count = int((rsi < 30).sum() + (rsi > 70).sum())
            c["signal_count"] = signal_count
            c["sharpe"] = 0.0  # placeholder — full Sharpe computed in run_backtest
            scored.append(c)

        except Exception as e:
            logger.debug("Failed to score %s: %s", symbol, e)
            c["signal_count"] = 0
            c["sharpe"] = 0.0
            scored.append(c)

    scored.sort(key=lambda x: x["signal_count"], reverse=True)
    return scored


def _fallback_candidates(db_path: str) -> list[dict]:
    """Return BTC/USDT + ETH/USDT as fallback when CCXT is unavailable."""
    return [
        {"symbol": "BTC/USDT", "volume_usdt": 0, "signal_count": 0, "sharpe": 0.0},
        {"symbol": "ETH/USDT", "volume_usdt": 0, "signal_count": 0, "sharpe": 0.0},
    ]


def _init_mcp() -> Optional[MCPClient]:
    try:
        return MCPClient()
    except Exception as e:
        logger.warning("MCPClient init failed: %s. Indicator data unavailable.", e)
        return None
