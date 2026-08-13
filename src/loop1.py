"""
loop1.py — Full Loop 1 orchestration: strategy discovery and validation.

Flow:
  1. screen_pair_universe()           → top 5 liquid pairs by RSI signal density
  2. detect_current_regime()          → current market regime (HMM-based)
  3. get_working_memory()             → FinMem-style layered KB context
  4. strategy_agent.generate(...)     → strategy_spec + backtest_results
  5. analyst_agent.evaluate(...)      → {pass, diagnosis, challenges}
     FAIL → kb.write_finding(...)     → retry with diagnosis, attempt += 1
             track spec evolution in strategy_evolutions table
             if attempt == max_attempts → raise MaxAttemptsExceeded
     PASS →
  6. run_backtest(strategy_spec)      → final calibration (IS+OOS)
  7. save_validated_strategy(...)     → strategies table
  8. backfill strategy_id into evolution records
  9. run memory maintenance (purge + promote/demote)
  10. return strategy dict

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
    COGNITIVE_SPAN_K,
)
from src.data.knowledge_base import (
    write_finding,
    get_working_memory,
    detect_current_regime,
    purge_kb,
    promote_memories,
)
from src.data.schema import get_connection
from src.backtest.engine import run_backtest
from src.agents.claude_client import ClaudeClient
from src.agents.mcp_client import MCPClient
from src.agents import strategy_agent, analyst_agent
from src.agents.tools import handle_save_validated_strategy
from src.monitor.status_bus import stage, emit, DONE, SKIPPED

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
    with stage("loop1", "screen_pairs", "Screening the pair universe") as st:
        candidates = screen_pair_universe(db_path)
        st.result(f"{len(candidates)} pairs")
        st.detail(", ".join(c.get("symbol", "?") for c in candidates[:5]))
    logger.info("Loop 1: %d candidate pairs selected", len(candidates))

    # Step 2: detect current regime
    with stage("loop1", "detect_regime", "Classifying the current market regime") as st:
        current_regime = detect_current_regime(db_path)
        st.result(current_regime)
    logger.info("Loop 1: current regime detected as '%s'", current_regime)

    # Step 3: load KB context via FinMem working memory
    with stage("loop1", "memory_retrieval",
               "Loading layered knowledge-base context") as st:
        memory = get_working_memory(
            db_path,
            current_regime=current_regime,
            mechanism=None,
            k_per_layer=COGNITIVE_SPAN_K,
        )
        kb_context = _flatten_working_memory(memory)
        kb_ids_used = _working_memory_ids(memory)
        st.result(f"{memory['total']} entries")
        st.detail(f"{memory['total']} entries, "
                  f"{memory['regime_matches']} matching '{current_regime}'")
    logger.info(
        "Loop 1: working memory loaded — %d entries (%d regime matches)",
        memory["total"], memory["regime_matches"],
    )

    last_diagnosis: Optional[str] = None
    prev_spec: Optional[dict] = None
    prev_results: Optional[dict] = None
    rejected_names: list[str] = []

    for attempt in range(1, max_attempts + 1):
        logger.info("Loop 1: attempt %d/%d", attempt, max_attempts)
        emit("loop1", "attempt", DONE, f"Attempt {attempt} of {max_attempts}",
             attempt=attempt, max_attempts=max_attempts)

        # Step 4: generate strategy
        # candidate_generation and empirical_search are emitted from inside
        # strategy_agent.generate_strategy — they happen there, not here.
        try:
            with stage("loop1", "strategy_selection", "LLM selects the best survivor",
                       attempt=attempt, max_attempts=max_attempts) as st:
                spec, backtest_results = strategy_agent.generate_strategy(
                    pair_candidates=candidates,
                    kb_context=kb_context,
                    client=client,
                    db_path=db_path,
                    mcp_client=mcp,
                    previous_diagnosis=last_diagnosis,
                    current_regime=current_regime,
                    rejected_names=rejected_names or None,
                )
                if spec is not None:
                    st.result(spec.get("name", "unnamed"))
        except ValueError as e:
            logger.warning("Strategy agent failed on attempt %d: %s", attempt, e)
            last_diagnosis = str(e)
            continue

        if spec is None:
            logger.warning("Strategy agent returned no spec on attempt %d", attempt)
            emit("loop1", "strategy_selection", SKIPPED, "No spec returned",
                 attempt=attempt, max_attempts=max_attempts)
            continue

        # Track evolution between attempts (Phase 4 RL layer)
        if prev_spec is not None and attempt > 1:
            _write_evolution(
                attempt_a=attempt - 1,
                attempt_b=attempt,
                prev_spec=prev_spec,
                current_spec=spec,
                prev_results=prev_results,
                current_results=backtest_results,
                diagnosis=last_diagnosis,
                db_path=db_path,
                kb_entries_used=kb_ids_used,
            )

        # Step 5: analyst evaluation (Debate CP1)
        with stage("loop1", "analyst_review", "Adversarial review (debate CP1)",
                   attempt=attempt, max_attempts=max_attempts) as st:
            eval_result = analyst_agent.evaluate(spec, backtest_results, client, db_path)
            st.result(str(eval_result.get("verdict", "")).upper() or
                      ("PASS" if eval_result["pass"] else "FAIL"))
            st.detail(f"{spec.get('name', 'unnamed')} — "
                      f"score {eval_result.get('score', 0):.2f}")

        if not eval_result["pass"]:
            diagnosis = eval_result["diagnosis"]
            regime_robustness = eval_result.get("regime_robustness", {})
            dominant_regime = regime_robustness.get("dominant_regime") or current_regime
            logger.info("Analyst rejected strategy (attempt %d): %s", attempt, diagnosis[:100])

            write_finding(
                category="failure_diagnosis",
                content=(
                    f"Attempt {attempt} failure.\n"
                    f"Strategy: {spec.get('name', 'unnamed')}\n"
                    f"Diagnosis: {diagnosis}\n"
                    f"Challenges: {json.dumps(eval_result['challenges'])}\n"
                    f"Regime robustness: {json.dumps(regime_robustness)}"
                ),
                db_path=db_path,
                regime=dominant_regime,
                mechanism=_infer_mechanism(spec),
            )
            last_diagnosis = diagnosis
            rejected_name = spec.get("name")
            if rejected_name and rejected_name not in rejected_names:
                rejected_names.append(rejected_name)

            if attempt == max_attempts:
                raise MaxAttemptsExceeded(
                    f"Loop 1 exhausted {max_attempts} attempts. Last diagnosis: {diagnosis}"
                )
            prev_spec = spec
            prev_results = backtest_results
            continue

        # PASS — strategy survived adversarial evaluation
        logger.info("Analyst approved strategy: %s", spec.get("name", "unnamed"))

        # Step 6: final calibration backtest
        with stage("loop1", "final_backtest", "Walk-forward calibration run",
                   attempt=attempt, max_attempts=max_attempts) as st:
            try:
                final_results = run_backtest(spec, db_path)
                agg = final_results.get("aggregate", {})
                st.result(f"WR {agg.get('win_rate_mean', 0):.2f}")
                st.detail(f"{agg.get('total_trades', 0)} trades, "
                          f"win rate {agg.get('win_rate_mean', 0):.2f}")
            except Exception as e:
                logger.warning(
                    "Final backtest failed: %s. Using strategy_agent backtest results.", e)
                final_results = backtest_results
                # Not a stage failure: the run continues on the earlier results.
                st.result("fell back")
                st.detail(f"final backtest failed ({type(e).__name__}) — "
                          f"using search results")

        # Step 7: save to DB
        verdict = eval_result.get("verdict", "pass")
        with stage("loop1", "save_strategy", "Persisting the validated strategy",
                   attempt=attempt, max_attempts=max_attempts) as st:
            strategy_id = handle_save_validated_strategy(
                spec, final_results, db_path, verdict=verdict
            )
            st.result(f"id={strategy_id}")
            st.detail(f"{spec.get('name', 'unnamed')} saved as id={strategy_id} "
                      f"({verdict})")
        status = "probation" if verdict == "probation" else "active"
        logger.info("Strategy saved with id=%d status=%s verdict=%s",
                    strategy_id, status, verdict)

        # Step 8: backfill strategy_id into orphaned evolution records
        try:
            conn = get_connection(db_path)
            conn.execute(
                "UPDATE strategy_evolutions SET strategy_id = ? WHERE strategy_id IS NULL",
                (strategy_id,),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug("Evolution backfill failed (table may not exist yet): %s", e)

        # Step 9: memory maintenance
        try:
            purged = purge_kb(db_path)
            changed = promote_memories(db_path)
            if purged or changed:
                logger.info("Memory maintenance: %d purged, %d layer-changed", purged, changed)
        except Exception as e:
            logger.warning("Memory maintenance failed: %s", e)

        # Step 10: return strategy dict
        return {
            "id": strategy_id,
            "name": spec.get("name"),
            "symbol": spec.get("symbol"),
            "timeframe": spec.get("timeframe"),
            "spec": spec,
            "performance": final_results.get("aggregate", {}),
            "calibration": final_results.get("calibration", {}),
            "viable": final_results.get("viable", True),
            "status": status,
            "verdict": verdict,
        }

    raise MaxAttemptsExceeded(f"Loop 1 exhausted {max_attempts} attempts without a viable strategy.")


def screen_pair_universe(db_path: str) -> list:
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

    candidates = []
    for symbol, ticker in tickers.items():
        if not symbol.endswith("/USDT"):
            continue
        quote_vol = ticker.get("quoteVolume") or 0
        if quote_vol < UNIVERSE_MIN_VOLUME_USD:
            continue
        candidates.append({"symbol": symbol, "volume_usdt": quote_vol})

    candidates.sort(key=lambda x: x["volume_usdt"], reverse=True)
    candidates = candidates[:UNIVERSE_MAX_CANDIDATES]

    if not candidates:
        return _fallback_candidates(db_path)

    scored = _score_candidates(candidates, db_path)
    return scored[:UNIVERSE_TOP_N]


def _score_candidates(candidates: list, db_path: str) -> list:
    """
    Score each candidate by RSI(14) signal density on recent 90-day data.
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
                "ORDER BY timestamp DESC LIMIT 2160",
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
            c["sharpe"] = 0.0
            scored.append(c)

        except Exception as e:
            logger.debug("Failed to score %s: %s", symbol, e)
            c["signal_count"] = 0
            c["sharpe"] = 0.0
            scored.append(c)

    scored.sort(key=lambda x: x["signal_count"], reverse=True)
    return scored


def _fallback_candidates(_db_path: str) -> list:
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


def _flatten_working_memory(memory: dict) -> list:
    """Flatten working memory layers into a single list for prompt injection."""
    flat = []
    for layer_name, findings in memory["layers"].items():
        for f in findings:
            flat.append({
                "layer": layer_name,
                "regime": f.get("regime"),
                "mechanism": f.get("mechanism"),
                "content": f["content"],
                "importance": f.get("importance", 50),
            })
    return flat


def _working_memory_ids(memory: dict) -> list:
    """
    KB row ids that were actually in the retrieved bundle.

    Kept separate from _flatten_working_memory so the ids never enter the prompt
    payload. Recording them is what lets feedback credit the findings that were
    genuinely in front of the agent, rather than every row sharing a strategy_id.
    """
    ids = []
    for findings in memory.get("layers", {}).values():
        for f in findings:
            if f.get("id") is not None:
                ids.append(f["id"])
    return ids


def _infer_mechanism(spec: dict) -> str:
    """
    Infer a mechanism label from the strategy spec entry conditions.
    Used for tagging KB failure entries.
    """
    if not spec:
        return "unknown"
    conditions = spec.get("entry", {}).get("conditions", [])
    indicators_used = [c.get("indicator", "") for c in conditions if isinstance(c, dict)]
    indicators_used += [ind.get("type", "") for ind in spec.get("indicators", []) if isinstance(ind, dict)]
    indicator_str = " ".join(indicators_used).upper()

    if "RSI" in indicator_str and any(
        c.get("operator") in ("<", "<=")
        and isinstance(c.get("value"), (int, float))
        and c.get("value", 100) <= 35
        for c in conditions
        if isinstance(c, dict)
    ):
        return "mean_reversion"
    if "EMA" in indicator_str or "MACD" in indicator_str:
        return "momentum"
    if "BB" in indicator_str:
        return "mean_reversion"
    if "ATR" in indicator_str or "ADX" in indicator_str:
        return "volatility"
    return "unknown"


def _compute_spec_delta(spec_a: dict, spec_b: dict) -> dict:
    """Return dict of fields that changed between two strategy specs."""
    if not spec_a or not spec_b:
        return {}
    changes = {}
    for key in set(spec_a.keys()) | set(spec_b.keys()):
        if spec_a.get(key) != spec_b.get(key):
            changes[key] = {"from": spec_a.get(key), "to": spec_b.get(key)}
    return changes


def _classify_outcome(perf_delta: dict) -> str:
    win_rate_change = perf_delta.get("win_rate_change", 0)
    if win_rate_change > 0.05:
        return "improved"
    if win_rate_change < -0.05:
        return "degraded"
    return "unchanged"


def _write_evolution(
    attempt_a: int,
    attempt_b: int,
    prev_spec: dict,
    current_spec: dict,
    prev_results: Optional[dict],
    current_results: Optional[dict],
    diagnosis: Optional[str],
    db_path: str,
    kb_entries_used: Optional[list] = None,
) -> None:
    """
    Record spec delta and performance delta between two Loop 1 attempts.

    ``kb_entries_used`` records which knowledge-base rows were in the retrieved
    bundle for this attempt. It is the attribution link FinMem's access counter
    relies on: without it, feedback can only credit every finding sharing a
    strategy_id, rather than the findings that actually informed the decision.
    """
    spec_delta = _compute_spec_delta(prev_spec, current_spec)

    prev_agg = (prev_results or {}).get("aggregate", {})
    curr_agg = (current_results or {}).get("aggregate", {})
    perf_delta = {
        "win_rate_change": round(
            curr_agg.get("win_rate_mean", 0) - prev_agg.get("win_rate_mean", 0), 4
        ),
        "sharpe_change": round(
            curr_agg.get("sharpe_mean", 0) - prev_agg.get("sharpe_mean", 0), 4
        ),
    }
    outcome = _classify_outcome(perf_delta)

    try:
        conn = get_connection(db_path)
        conn.execute(
            """
            INSERT INTO strategy_evolutions
                (attempt_a, attempt_b, strategy_id, spec_delta, performance_delta,
                 outcome, diagnosis, kb_entries_used, created_at)
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt_a,
                attempt_b,
                json.dumps(spec_delta),
                json.dumps(perf_delta),
                outcome,
                diagnosis,
                json.dumps(kb_entries_used or []),
                int(time.time() * 1000),
            ),
        )
        conn.commit()
        conn.close()
        logger.debug("Evolution recorded: attempt %d→%d outcome=%s", attempt_a, attempt_b, outcome)
    except Exception as e:
        logger.warning("Failed to write evolution record: %s", e)


def get_recent_evolutions(db_path: str, limit: int = 5) -> list:
    """Return the most recent strategy evolution records for prompt injection."""
    try:
        conn = get_connection(db_path)
        rows = conn.execute(
            """
            SELECT attempt_a, attempt_b, spec_delta, performance_delta, outcome, diagnosis
            FROM strategy_evolutions
            ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception:
        return []
