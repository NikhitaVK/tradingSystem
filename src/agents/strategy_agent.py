"""
strategy_agent.py — Empirical search + LLM selector for strategy discovery.

Replaces the old narrative tool-use loop with:
  1. candidate_generator  — emit 12 mechanism-diverse strategy specs
  2. empirical_search     — backtest all, rank by composite score, return top-K
  3. Single Claude call    — LLM selects the best survivor and articulates mechanism

The LLM no longer writes formulas or calls run_backtest. It acts as a
mechanism-reasoning filter on empirically-validated candidates.

Returns: (strategy_spec dict, backtest_results dict)
"""
import json
import logging
from pathlib import Path

from config.settings import CLAUDE_THINKING_BUDGET_STRATEGY
from src.agents.candidate_generator import generate_candidate_pool
from src.agents.empirical_search import run_search
from src.monitor.status_bus import stage

logger = logging.getLogger(__name__)

_PROMPT_PATH = Path("prompts/strategy_agent_v2.txt")
_STRATEGY_PROMPT = _PROMPT_PATH.read_text()


def generate_strategy(
    pair_candidates: list[dict],
    kb_context: list[dict],
    client,
    db_path: str,
    mcp_client=None,
    previous_diagnosis: str = None,
    current_regime: str = None,
    rejected_names: list = None,
) -> tuple[dict, dict]:
    """
    Generate a strategy spec and backtest results via empirical search + LLM selection.

    Args:
        pair_candidates:    Top pairs from screener with metrics.
        kb_context:         Recent KB findings (pre-fetched by loop1.py).
        client:             ClaudeClient instance.
        db_path:            Path to SQLite DB.
        mcp_client:         Unused (kept for API compatibility).
        previous_diagnosis: Most recent failure diagnosis (used for KB blacklist in future).
        current_regime:     Current market regime label from HMM detection.

    Returns:
        (strategy_spec, backtest_results)

    Raises:
        ValueError: If no candidate clears the viability floor.
    """
    regime = current_regime or "unknown"
    best_pair = pair_candidates[0] if pair_candidates else {"symbol": "BTC/USDT"}
    if "timeframe" not in best_pair:
        best_pair["timeframe"] = "1h"

    # Step 1: Generate mechanism-diverse candidate pool.
    # rejected_names excludes specs rejected by the analyst on prior attempts —
    # this makes the retry loop actually explore new specs rather than
    # re-ranking the identical deterministic pool.
    with stage("loop1", "candidate_generation",
               "Building mechanism-diverse specs") as st:
        candidates = generate_candidate_pool(
            regime, best_pair, kb_blacklist=rejected_names,
        )
        st.result(f"{len(candidates)} specs")
        st.detail(f"{len(candidates)} candidates for {best_pair['symbol']} "
                  f"in {regime} regime")
    logger.info(
        "Strategy agent: generated %d candidates for %s in %s regime",
        len(candidates), best_pair["symbol"], regime,
    )

    # Step 2: Empirical search — backtest all candidates, rank by score
    with stage("loop1", "empirical_search",
               "Backtesting and ranking candidates") as st:
        ranked = run_search(candidates, db_path)
        st.result(f"{len(ranked)} viable")
        st.detail(f"{len(ranked)} of {len(candidates)} candidates cleared "
                  f"the viability floor")

    if not ranked:
        raise ValueError(
            f"Empirical search found no viable candidates for {best_pair['symbol']} "
            f"in {regime} regime. All {len(candidates)} candidates failed backtest."
        )

    # Log top candidate metrics — the analyst agent is the downstream guard,
    # so we pass even marginal candidates through for evaluation.
    top_spec, top_results, top_score = ranked[0]
    top_pf = top_results.get("aggregate", {}).get("profit_factor_mean", 0)
    top_trades = top_results.get("aggregate", {}).get("total_trades", 0)
    logger.info(
        "Top candidate: %s pf=%.2f trades=%d viable=%s",
        top_spec.get("name"), top_pf, top_trades, top_results.get("viable"),
    )

    # Step 3: LLM selects from survivors
    system_prompt = _build_system_prompt(kb_context, ranked, regime)
    messages = [
        {
            "role": "user",
            "content": (
                "Review the empirically-tested candidates below and select the best one. "
                "Respond with the JSON selection object only."
            ),
        }
    ]

    response_text, tool_calls, messages = client.chat(
        messages=messages,
        tools=[],
        system_prompt=system_prompt,
        thinking_budget=CLAUDE_THINKING_BUDGET_STRATEGY,
        agent_name="strategy_agent",
    )

    # Parse LLM selection
    chosen_idx = _parse_selection(response_text, len(ranked))
    chosen_spec, chosen_results, chosen_score = ranked[chosen_idx]

    logger.info(
        "Strategy agent selected: %s (index=%d, score=%.4f, pf=%.2f)",
        chosen_spec.get("name"), chosen_idx, chosen_score,
        chosen_results.get("aggregate", {}).get("profit_factor_mean", 0),
    )

    return chosen_spec, chosen_results


def _build_system_prompt(
    kb_context: list[dict],
    ranked: list,
    current_regime: str,
) -> str:
    """Build the selector prompt with candidate summaries and KB context."""
    # Format KB entries
    if kb_context:
        tagged_lines = []
        for entry in kb_context:
            layer = entry.get("layer", "shallow")
            tag = {"shallow": "[sha]", "intermediate": "[int]", "deep": "[dee]"}.get(layer, "[sha]")
            regime = entry.get("regime", "?")
            mechanism = entry.get("mechanism", "?")
            content = entry.get("content", "")[:300]
            tagged_lines.append(f"{tag} regime={regime} mechanism={mechanism}\n    {content}")
        kb_str = "\n".join(tagged_lines)
    else:
        kb_str = "No prior findings. This is the first strategy discovery cycle."

    # Format candidate summaries
    summaries = []
    for i, (spec, results, score) in enumerate(ranked):
        agg = results.get("aggregate", {})
        cal = results.get("calibration", {})
        regime_bd = agg.get("regime_breakdown", {})
        summaries.append(
            f"[{i}] {spec.get('name', 'unnamed')} (mechanism={spec.get('mechanism', '?')})\n"
            f"    Symbol: {spec.get('symbol')} | Timeframe: {spec.get('timeframe')}\n"
            f"    Win rate: {agg.get('win_rate_mean', 0):.2%} | "
            f"Sharpe: {agg.get('sharpe_mean', 0):.2f} | "
            f"Sortino: {agg.get('sortino_mean', 0):.2f}\n"
            f"    Profit factor: {agg.get('profit_factor_mean', 0):.2f} | "
            f"Total trades: {agg.get('total_trades', 0)}\n"
            f"    WFE: {cal.get('walk_forward_efficiency', 'N/A')} | "
            f"Score: {score:.4f}\n"
            f"    Regime breakdown: {json.dumps(regime_bd)}\n"
            f"    Viable: {results.get('viable', False)}"
        )

    return (
        _STRATEGY_PROMPT
        .replace("{candidate_count}", str(len(ranked)))
        .replace("{current_regime}", current_regime or "unknown")
        .replace("{candidate_summaries}", "\n\n".join(summaries))
        .replace("{kb_context}", kb_str)
    )


def _parse_selection(response_text: str, n_candidates: int) -> int:
    """Parse Claude's JSON selection response. Falls back to index 0."""
    import re

    # Try direct JSON parse
    for text in [response_text.strip()]:
        try:
            parsed = json.loads(text)
            idx = int(parsed.get("chosen_index", 0))
            if 0 <= idx < n_candidates:
                return idx
        except (json.JSONDecodeError, TypeError, ValueError):
            pass

    # Try extracting JSON from markdown fences
    json_blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    for block in json_blocks:
        try:
            parsed = json.loads(block)
            idx = int(parsed.get("chosen_index", 0))
            if 0 <= idx < n_candidates:
                return idx
        except (json.JSONDecodeError, TypeError, ValueError):
            continue

    # Try finding chosen_index anywhere in the text
    match = re.search(r'"chosen_index"\s*:\s*(\d+)', response_text)
    if match:
        idx = int(match.group(1))
        if 0 <= idx < n_candidates:
            return idx

    logger.warning("Could not parse LLM selection, defaulting to index 0")
    return 0
