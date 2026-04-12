"""
analyst_agent.py — Adversarial strategy evaluation and degradation reflection.

Two modes:
  evaluate() — Debate Checkpoint 1 (Loop 1). Full adversarial evaluation with
               5000-token thinking budget. Challenges economic mechanism, WFE,
               regime robustness, cost survival, and statistical significance.
               Returns {'pass': bool, 'diagnosis': str, 'challenges': list[str]}.

  reflect()  — Degradation reflection (triggered by Loop 2 degradation monitor).
               Diagnoses root cause of strategy failure in live trading.
               Calls write_to_knowledge_base() tool to persist the diagnosis.
               Returns diagnosis string.
"""
import json
import logging
import re
from pathlib import Path

from config.settings import (
    CLAUDE_THINKING_BUDGET_ANALYST,
    CLAUDE_THINKING_BUDGET_ANALYST_BRIEF,
)
from src.agents.tools import ANALYST_REFLECT_TOOLS, handle_write_to_knowledge_base

logger = logging.getLogger(__name__)

# Load prompts at import time
_EVAL_PROMPT = Path("prompts/analyst_eval_v1.txt").read_text()
_REFLECT_PROMPT = Path("prompts/analyst_reflect_v1.txt").read_text()


def evaluate(
    strategy_spec: dict,
    backtest_results: dict,
    client,  # ClaudeClient instance
) -> dict:
    """
    Adversarial evaluation of a strategy (Debate CP1).

    Args:
        strategy_spec:     Strategy spec dict.
        backtest_results:  Full backtest results dict from run_backtest().
        client:            ClaudeClient instance.

    Returns:
        {'pass': bool, 'diagnosis': str, 'challenges': list[str]}
    """
    system_prompt = _build_eval_prompt(strategy_spec, backtest_results)

    messages = [
        {
            "role": "user",
            "content": (
                "Evaluate this strategy adversarially. Challenge each of the five criteria. "
                "Return your verdict as a JSON object with 'pass', 'diagnosis', and 'challenges' fields."
            ),
        }
    ]

    response_text, tool_calls, _ = client.chat(
        messages=messages,
        tools=[],  # analyst evaluation uses no tools
        system_prompt=system_prompt,
        thinking_budget=CLAUDE_THINKING_BUDGET_ANALYST,
        agent_name="analyst_eval",
    )

    return _parse_eval_response(response_text)


def evaluate_brief(
    strategy_spec: dict,
    recent_candles: list[dict],
    proposed_trade: dict,
    client,
) -> dict:
    """
    Brief evaluation at execution time (Debate CP2, Loop 2).
    Lower thinking budget (2000 tokens) for speed.

    Returns: {'confirm': bool, 'note': str}
    """
    system = (
        "You are a quick trade reviewer. Given the recent market context and a proposed trade, "
        "decide whether to confirm or reject it. Be brief — respond in 2-3 sentences.\n\n"
        f"Strategy: {json.dumps(strategy_spec)}\n"
        f"Proposed trade: {json.dumps(proposed_trade)}\n"
        f"Recent candles (last 20): {json.dumps(recent_candles[-20:] if recent_candles else [])}"
    )

    messages = [{"role": "user", "content": "Should this trade be placed? Reply with JSON: {\"confirm\": bool, \"note\": str}"}]

    response_text, _, _ = client.chat(
        messages=messages,
        tools=[],
        system_prompt=system,
        thinking_budget=CLAUDE_THINKING_BUDGET_ANALYST_BRIEF,
        agent_name="analyst_brief",
    )

    try:
        data = _extract_json(response_text)
        return {"confirm": bool(data.get("confirm", False)), "note": data.get("note", "")}
    except Exception:
        # On parse failure, default to not confirming (safe)
        return {"confirm": False, "note": f"Parse error in brief eval response: {response_text[:200]}"}


def reflect(
    strategy: dict,
    recent_trades: list[dict],
    performance_history: list[dict],
    client,
    db_path: str,
) -> str:
    """
    Post-degradation root cause analysis (triggered by DegradationMonitor).

    Args:
        strategy:            Full strategy dict from DB (includes spec and performance).
        recent_trades:       Recent trade records from trades table.
        performance_history: Rolling win rate snapshots from performance table.
        client:              ClaudeClient instance.
        db_path:             For write_to_knowledge_base tool handler.

    Returns:
        Diagnosis string (also written to KB via tool call).
    """
    strategy_spec = json.loads(strategy.get("spec", "{}")) if isinstance(strategy.get("spec"), str) else strategy.get("spec", {})
    backtest_results = json.loads(strategy.get("performance", "{}")) if isinstance(strategy.get("performance"), str) else strategy.get("performance", {})

    system_prompt = (
        _REFLECT_PROMPT
        .replace("{strategy_spec}", json.dumps(strategy_spec, indent=2))
        .replace("{backtest_results}", json.dumps(backtest_results, indent=2))
        .replace("{recent_trades}", json.dumps(recent_trades, indent=2))
        .replace("{performance_history}", json.dumps(performance_history, indent=2))
    )

    messages = [
        {
            "role": "user",
            "content": (
                "Diagnose the root cause of this strategy's degradation. "
                "Work through each hypothesis, identify the most likely cause, "
                "then call write_to_knowledge_base() with your diagnosis."
            ),
        }
    ]

    diagnosis = ""
    for _ in range(3):  # allow up to 2 tool call rounds
        response_text, tool_calls, messages = client.chat(
            messages=messages,
            tools=ANALYST_REFLECT_TOOLS,
            system_prompt=system_prompt,
            thinking_budget=CLAUDE_THINKING_BUDGET_ANALYST,
            agent_name="analyst_reflect",
            strategy_id=strategy.get("id"),
        )

        if not tool_calls:
            diagnosis = response_text
            break

        for tc in tool_calls:
            if tc["name"] == "write_to_knowledge_base":
                handle_write_to_knowledge_base(tc["input"], db_path)
                diagnosis = tc["input"].get("content", "")
            tool_id = tc["id"]
            messages = client.append_tool_result(messages, tool_id, json.dumps({"success": True}))

        if diagnosis:
            break

    return diagnosis


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_eval_prompt(strategy_spec: dict, backtest_results: dict) -> str:
    aggregate = backtest_results.get("aggregate", {})
    calibration = backtest_results.get("calibration", {})
    slices = backtest_results.get("slices", [])

    wfe = calibration.get("walk_forward_efficiency", "N/A")
    profit_factor = aggregate.get("profit_factor_mean", aggregate.get("profit_factor", "N/A"))
    regime_breakdown = aggregate.get("regime_breakdown", {})
    total_trades = aggregate.get("total_trades", 0)
    n_slices = len(slices)

    return (
        _EVAL_PROMPT
        .replace("{strategy_spec}", json.dumps(strategy_spec, indent=2))
        .replace("{backtest_results}", json.dumps(backtest_results, indent=2))
        .replace("{wfe}", str(wfe))
        .replace("{profit_factor}", str(profit_factor))
        .replace("{regime_breakdown}", json.dumps(regime_breakdown))
        .replace("{total_trades}", str(total_trades))
        .replace("{n_slices}", str(n_slices))
    )


def _parse_eval_response(response_text: str) -> dict:
    """Parse analyst evaluation response into structured dict."""
    try:
        data = _extract_json(response_text)
        return {
            "pass": bool(data.get("pass", False)),
            "diagnosis": str(data.get("diagnosis", "")),
            "challenges": list(data.get("challenges", [])),
        }
    except Exception as e:
        logger.warning("Failed to parse analyst eval response: %s. Raw: %s", e, response_text[:300])
        # Default to fail on parse error — safer than accidentally passing a bad strategy
        return {
            "pass": False,
            "diagnosis": f"Parse error in analyst response: {response_text[:200]}",
            "challenges": ["Could not parse analyst evaluation response"],
        }


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from a text response."""
    # Try markdown code blocks first
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    for block in blocks:
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue

    # Try bare JSON
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Find first { ... } in text
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())

    raise ValueError("No JSON found in response")
