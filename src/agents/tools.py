"""
tools.py — Anthropic tool schemas and their Python handler functions.

Five tools used by the strategy agent and analyst:
  1. query_knowledge_base    — retrieve prior findings (semantic or keyword)
  2. run_backtest            — test a strategy spec via Module 2 engine
  3. get_indicator_data      — fetch indicator values (MCP → TA-Lib fallback)
  4. write_to_knowledge_base — persist degradation diagnoses (analyst, reflect mode)
  5. save_validated_strategy — write final strategy + calibration to DB

Schema format follows Anthropic's tool_use API.
Handlers are called by strategy_agent / loop1 when Claude invokes a tool.
"""
import json
import logging
import time

from src.data.knowledge_base import (
    write_finding,
    query_relevant,
    query_regime_failures,
    detect_current_regime,
)
from src.backtest.engine import run_backtest as _run_backtest
from src.data.schema import get_connection

logger = logging.getLogger(__name__)


# ── Tool schemas (passed to Claude) ──────────────────────────────────────────

# Strategy agent no longer uses tools — empirical search replaces the tool-use loop.
# Kept as empty list for API compatibility (ClaudeClient.chat accepts tools=[]).
STRATEGY_AGENT_TOOLS = []

ANALYST_REFLECT_TOOLS = [
    {
        "name": "write_to_knowledge_base",
        "description": (
            "Persist a degradation diagnosis to the knowledge base. "
            "Call this with your root cause analysis. "
            "Other agents will read this on the next strategy discovery cycle."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["failure_diagnosis", "market_regime", "parameter_insight", "general"],
                    "description": "Use 'failure_diagnosis' for degradation diagnoses.",
                },
                "content": {
                    "type": "string",
                    "description": (
                        "The diagnosis. Must include: root cause, conditions under which "
                        "the strategy should be retried vs avoided."
                    ),
                },
                "strategy_id": {
                    "type": "integer",
                    "description": "ID of the degraded strategy.",
                },
                "regime": {
                    "type": "string",
                    "description": "Market regime at time of degradation (e.g. 'trending_bull').",
                },
                "mechanism": {
                    "type": "string",
                    "description": "Strategy mechanism (e.g. 'mean_reversion', 'momentum').",
                },
            },
            "required": ["category", "content"],
        },
    },
]


# ── Tool handlers ─────────────────────────────────────────────────────────────

def handle_query_knowledge_base(args: dict, db_path: str) -> str:
    keywords = args.get("keywords", [])
    limit = args.get("limit", 10)
    query_context = args.get("query_context")
    findings = query_relevant(keywords, db_path, limit=limit, query_context=query_context)
    if not findings:
        return json.dumps({"findings": [], "message": "No relevant findings in knowledge base."})
    return json.dumps({"findings": findings})


def _normalise_spec(spec) -> dict:
    """
    Normalise a strategy spec that may have been double-encoded or have
    non-standard field names by Claude.

    Fixes:
      1. spec itself is a JSON string → parse it
      2. entry/exit fields are JSON strings → parse them
      3. indicators use 'name' instead of 'type' → rename
    """
    if isinstance(spec, str):
        spec = json.loads(spec)

    spec = dict(spec)

    for key in ("entry", "exit"):
        if isinstance(spec.get(key), str):
            spec[key] = json.loads(spec[key])
        # Also normalise individual conditions that Claude may have encoded as strings
        if isinstance(spec.get(key), dict):
            raw_conditions = spec[key].get("conditions", [])
            spec[key]["conditions"] = [
                json.loads(c) if isinstance(c, str) else c
                for c in raw_conditions
            ]

    indicators = spec.get("indicators", [])
    normalised = []
    for ind in indicators:
        if isinstance(ind, str):
            ind = json.loads(ind)
        ind = dict(ind)
        if "name" in ind and "type" not in ind:
            ind["type"] = ind.pop("name")
        normalised.append(ind)
    spec["indicators"] = normalised

    return spec


def _infer_mechanism(spec: dict) -> str:
    """Infer mechanism label from strategy spec entry conditions."""
    if not spec:
        return "unknown"
    conditions = spec.get("entry", {}).get("conditions", [])
    # Guard against conditions that weren't normalised to dicts
    indicators_used = [c.get("indicator", "") for c in conditions if isinstance(c, dict)]
    indicators_used += [ind.get("type", "") for ind in spec.get("indicators", [])]
    indicator_str = " ".join(indicators_used).upper()

    if "RSI" in indicator_str and any(
        c.get("operator") in ("<", "<=")
        and isinstance(c.get("value"), (int, float))
        and c.get("value", 100) <= 35
        for c in conditions
    ):
        return "mean_reversion"
    if "EMA" in indicator_str or "MACD" in indicator_str:
        return "momentum"
    if "BB" in indicator_str:
        return "mean_reversion"
    if "ATR" in indicator_str or "ADX" in indicator_str:
        return "volatility"
    return "unknown"


def handle_run_backtest(args: dict, db_path: str) -> str:
    try:
        spec = _normalise_spec(args["strategy_spec"])
        mechanism = _infer_mechanism(spec)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        logger.warning("run_backtest: invalid strategy_spec from Claude: %s", e)
        return json.dumps({"error": f"Invalid strategy spec: {e}", "viable": False})

    # Pre-backtest regime check (Phase 3)
    try:
        current_regime = detect_current_regime(db_path)
        failures = query_regime_failures(current_regime, mechanism, db_path)
    except Exception:
        current_regime = "unknown"
        failures = []
    if failures:
        logger.warning(
            "Regime match warning: %d prior failures for mechanism=%s in regime=%s",
            len(failures), mechanism, current_regime,
        )

    try:
        result = _run_backtest(spec, db_path)
        return json.dumps({
            "viable": result.get("viable"),
            "aggregate": result.get("aggregate"),
            "slices": result.get("slices"),
            "calibration": result.get("calibration"),
            "regime_warning": len(failures) > 0,
            "regime_warning_count": len(failures),
            "prior_failures": [f["content"][:200] for f in failures],
        })
    except ValueError as e:
        return json.dumps({"error": str(e), "viable": False})
    except Exception as e:
        logger.error("run_backtest tool error: %s", e)
        return json.dumps({"error": f"Backtest failed: {e}", "viable": False})


def handle_get_indicator_data(args: dict, mcp_client) -> str:
    if mcp_client is None:
        return json.dumps({"error": "Indicator data unavailable (no MCP client)", "values": []})
    try:
        result = mcp_client.call_tool("get_indicator_data", args)
        return json.dumps(result)
    except Exception as e:
        logger.warning("get_indicator_data failed: %s", e)
        return json.dumps({"error": str(e), "values": []})


def handle_write_to_knowledge_base(args: dict, db_path: str) -> str:
    category = args.get("category", "general")
    content = args["content"]
    strategy_id = args.get("strategy_id")
    regime = args.get("regime")
    mechanism = args.get("mechanism")
    row_id = write_finding(
        category, content, db_path,
        strategy_id=strategy_id,
        regime=regime,
        mechanism=mechanism,
    )
    return json.dumps({"success": True, "id": row_id})


def handle_save_validated_strategy(
    strategy_spec: dict,
    backtest_results: dict,
    db_path: str,
    verdict: str = "pass",
) -> int:
    """
    Write a validated strategy + calibration to the strategies table.
    Returns the new strategy id.

    verdict: "pass" or "probation" (from analyst_agent.evaluate). "probation"
    sets status='probation' so Loop 2 applies size multiplier + tighter
    degradation threshold. Any other value defaults to 'active'.
    """
    calibration = backtest_results.get("calibration", {})
    status = "probation" if verdict == "probation" else "active"
    conn = get_connection(db_path)
    cur = conn.execute(
        "INSERT INTO strategies (name, spec, performance, degradation_threshold, "
        "position_sizing, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            strategy_spec.get("name", "Unnamed"),
            json.dumps(strategy_spec),
            json.dumps(backtest_results.get("aggregate", {})),
            calibration.get("degradation_threshold"),
            json.dumps(calibration.get("position_sizing", {})),
            status,
            int(time.time() * 1000),
        ),
    )
    conn.commit()
    row_id = cur.lastrowid
    conn.close()
    return row_id
