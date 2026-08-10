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
from datetime import datetime
from pathlib import Path

from config.settings import (
    CLAUDE_THINKING_BUDGET_ANALYST,
    CLAUDE_THINKING_BUDGET_ANALYST_BRIEF,
)
from src.agents.tools import ANALYST_REFLECT_TOOLS, handle_write_to_knowledge_base
from src.data.knowledge_base import (
    detect_current_regime,
    query_regime_failures,
    query_relevant,
)

logger = logging.getLogger(__name__)

# Load prompts at import time
_EVAL_PROMPT = Path("prompts/analyst_eval_v3.txt").read_text()
_REFLECT_PROMPT = Path("prompts/analyst_reflect_v2.txt").read_text()

# How many prior records to surface to the analyst. Deliberately small: the
# analyst's value at CP1 is independent judgement, so it gets a narrow targeted
# lookup rather than the broad working-memory bundle the strategy agent receives.
_ANALYST_KB_LIMIT = 5


def evaluate(
    strategy_spec: dict,
    backtest_results: dict,
    client,  # ClaudeClient instance
    db_path: str = None,
) -> dict:
    """
    Adversarial evaluation of a strategy (Debate CP1).

    Args:
        strategy_spec:     Strategy spec dict.
        backtest_results:  Full backtest results dict from run_backtest().
        client:            ClaudeClient instance.
        db_path:           Optional. When supplied, the analyst receives a targeted
                           lookup of prior failures for this mechanism in the current
                           regime. Omitted (None) keeps the pre-2026-08 behaviour of
                           judging on spec + backtest alone.

    Returns:
        {'verdict': 'pass'|'probation'|'fail', 'pass': bool, 'score': float,
         'subscores': dict, 'diagnosis': str, 'challenges': list[str],
         'regime_robustness': dict}
    """
    regime_failures = _fetch_regime_failures(strategy_spec, db_path)
    system_prompt = _build_eval_prompt(strategy_spec, backtest_results, regime_failures)

    messages = [
        {
            "role": "user",
            "content": (
                "Grade this strategy on the composite score and return the full "
                "JSON object specified in the response-format section."
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
        .replace("{prior_diagnoses}", _fetch_prior_diagnoses(db_path))
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

def _format_findings(findings: list, empty_note: str) -> str:
    """Render KB rows for prompt injection, newest first."""
    if not findings:
        return empty_note
    lines = []
    for f in findings[:_ANALYST_KB_LIMIT]:
        stamp = f.get("created_at")
        when = (
            datetime.utcfromtimestamp(stamp / 1000).strftime("%Y-%m-%d")
            if isinstance(stamp, (int, float)) else "unknown date"
        )
        content = " ".join(str(f.get("content", "")).split())
        lines.append(
            f"- [{when}] regime={f.get('regime', 'unknown')} "
            f"mechanism={f.get('mechanism', 'unknown')}: {content}"
        )
    return "\n".join(lines)


def _fetch_regime_failures(strategy_spec: dict, db_path: str) -> str:
    """
    Targeted CP1 lookup: prior failures for this mechanism under the current regime.

    Deliberately narrower than the strategy agent's working-memory bundle. Feeding
    both agents identical context would make CP1's verdict correlate with the
    selection it is supposed to challenge independently.
    """
    if not db_path:
        return "No knowledge base available for this evaluation."
    try:
        from src.agents.tools import _infer_mechanism

        mechanism = _infer_mechanism(strategy_spec)
        regime = detect_current_regime(db_path)
        failures = query_regime_failures(regime, mechanism, db_path)
        return _format_findings(
            failures,
            f"No prior recorded failures for mechanism '{mechanism}' in regime "
            f"'{regime}'. This is an absence of evidence, not evidence of safety.",
        )
    except Exception as e:
        logger.warning("CP1 regime-failure lookup failed: %s", e)
        return "Knowledge base lookup unavailable for this evaluation."


def _fetch_prior_diagnoses(db_path: str) -> str:
    """
    Prior root-cause diagnoses, so reflection can recognise a recurring cause.

    Without this, reflect() diagnoses degradation with no memory of any previous
    degradation — the system can rediscover the same root cause indefinitely and
    never notice it is a pattern.
    """
    if not db_path:
        return "No knowledge base available for this reflection."
    try:
        keywords = ["degradation", "regime", "overfit", "failed", "drawdown"]
        findings = query_relevant(
            keywords, db_path, limit=_ANALYST_KB_LIMIT, category="failure_diagnosis"
        )
        return _format_findings(
            findings, "No prior degradation diagnoses recorded — this is the first."
        )
    except Exception as e:
        logger.warning("Reflection KB lookup failed: %s", e)
        return "Knowledge base lookup unavailable for this reflection."


def _build_eval_prompt(strategy_spec: dict, backtest_results: dict,
                       regime_failures: str = "") -> str:
    aggregate = backtest_results.get("aggregate", {})
    calibration = backtest_results.get("calibration", {})
    slices = backtest_results.get("slices", [])

    wfe = calibration.get("walk_forward_efficiency", "N/A")
    profit_factor = aggregate.get("profit_factor_mean", aggregate.get("profit_factor", "N/A"))
    profit_factor_tw = aggregate.get("profit_factor_trade_weighted", profit_factor)
    regime_breakdown = aggregate.get("regime_breakdown", {})
    total_trades = aggregate.get("total_trades", 0)
    n_slices = len(slices)

    return (
        _EVAL_PROMPT
        .replace("{strategy_spec}", json.dumps(strategy_spec, indent=2))
        .replace("{backtest_results}", json.dumps(backtest_results, indent=2))
        .replace("{wfe}", str(wfe))
        .replace("{profit_factor_tw}", str(profit_factor_tw))
        .replace("{profit_factor}", str(profit_factor))
        .replace("{regime_breakdown}", json.dumps(regime_breakdown))
        .replace("{total_trades}", str(total_trades))
        .replace("{n_slices}", str(n_slices))
        .replace("{regime_failures}", regime_failures or "Knowledge base not consulted.")
    )


_VALID_VERDICTS = {"pass", "probation", "fail"}


def _parse_eval_response(response_text: str) -> dict:
    """Parse analyst evaluation response into structured dict.

    Accepts v2 schema (verdict/score/subscores) and falls back to v1 pass-only
    schema for backwards compatibility. Always emits ``pass`` (True for verdict
    in {'pass', 'probation'}) so Loop 1's existing callers keep working.
    """
    try:
        data = _extract_json(response_text)

        raw_robustness = data.get("regime_robustness", {})
        regime_robustness = {
            "dominant_regime": raw_robustness.get("dominant_regime"),
            "fragile_regimes": list(raw_robustness.get("fragile_regimes", [])),
            "failure_risk": str(raw_robustness.get("failure_risk", "")),
        } if raw_robustness else {}

        verdict = str(data.get("verdict", "")).lower().strip()
        if verdict not in _VALID_VERDICTS:
            # v1 fallback: map legacy pass:bool → pass/fail
            verdict = "pass" if bool(data.get("pass", False)) else "fail"

        raw_subscores = data.get("subscores", {}) or {}
        subscores = {
            "pf": float(raw_subscores.get("pf", 0.0)),
            "wfe": float(raw_subscores.get("wfe", 0.0)),
            "consistency": float(raw_subscores.get("consistency", 0.0)),
            "sample_size": float(raw_subscores.get("sample_size", 0.0)),
            "mechanism": float(raw_subscores.get("mechanism", 0.0)),
        }

        return {
            "verdict": verdict,
            "pass": verdict in ("pass", "probation"),
            "score": float(data.get("score", 0.0)),
            "subscores": subscores,
            "diagnosis": str(data.get("diagnosis", "")),
            "challenges": list(data.get("challenges", [])),
            "regime_robustness": regime_robustness,
        }
    except Exception as e:
        logger.warning("Failed to parse analyst eval response: %s. Raw: %s", e, response_text[:300])
        # Default to fail on parse error — safer than accidentally passing a bad strategy
        return {
            "verdict": "fail",
            "pass": False,
            "score": 0.0,
            "subscores": {"pf": 0.0, "wfe": 0.0, "consistency": 0.0, "sample_size": 0.0, "mechanism": 0.0},
            "diagnosis": f"Parse error in analyst response: {response_text[:200]}",
            "challenges": ["Could not parse analyst evaluation response"],
            "regime_robustness": {},
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
