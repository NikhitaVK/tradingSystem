"""
tools.py — Anthropic tool schemas and their Python handler functions.

Five tools used by the strategy agent and analyst:
  1. query_knowledge_base    — retrieve prior findings
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

from src.data.knowledge_base import write_finding, query_relevant
from src.backtest.engine import run_backtest as _run_backtest
from src.data.schema import get_connection

logger = logging.getLogger(__name__)


# ── Tool schemas (passed to Claude) ──────────────────────────────────────────

STRATEGY_AGENT_TOOLS = [
    {
        "name": "query_knowledge_base",
        "description": (
            "Retrieve prior findings from the knowledge base. "
            "Call this FIRST to learn what has already been tried and why it failed. "
            "Returns a list of findings sorted by recency."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keywords to search for (e.g. ['RSI', 'BTC', 'failure'])",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max findings to return (default 10)",
                    "default": 10,
                },
            },
            "required": ["keywords"],
        },
    },
    {
        "name": "run_backtest",
        "description": (
            "Run a walk-forward backtest on a strategy spec. "
            "Returns per-slice metrics, aggregate metrics, calibration data, and a viable flag. "
            "Only call this AFTER stating your economic mechanism hypothesis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "strategy_spec": {
                    "type": "object",
                    "description": (
                        "Strategy specification. Must include: name, symbol, timeframe, "
                        "indicators (list), entry (logic + conditions), exit (logic + conditions)."
                    ),
                    "properties": {
                        "name": {"type": "string"},
                        "symbol": {"type": "string", "description": "e.g. 'BTC/USDT'"},
                        "timeframe": {"type": "string", "description": "e.g. '1h'"},
                        "indicators": {
                            "type": "array",
                            "items": {"type": "object"},
                        },
                        "entry": {"type": "object"},
                        "exit": {"type": "object"},
                    },
                    "required": ["name", "symbol", "timeframe", "indicators", "entry", "exit"],
                },
            },
            "required": ["strategy_spec"],
        },
    },
    {
        "name": "get_indicator_data",
        "description": (
            "Fetch current indicator values for a symbol/timeframe. "
            "Use this to validate that your hypothesis aligns with current market conditions "
            "before committing to a backtest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "e.g. 'BTC/USDT'"},
                "timeframe": {"type": "string", "description": "e.g. '1h'"},
                "indicator": {
                    "type": "string",
                    "description": "Indicator name: RSI, EMA, MACD, BB, ATR",
                },
                "params": {
                    "type": "object",
                    "description": "Indicator parameters (e.g. {'period': 14})",
                },
            },
            "required": ["symbol", "timeframe", "indicator"],
        },
    },
]

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
            },
            "required": ["category", "content"],
        },
    },
]


# ── Tool handlers ─────────────────────────────────────────────────────────────

def handle_query_knowledge_base(args: dict, db_path: str) -> str:
    keywords = args.get("keywords", [])
    limit = args.get("limit", 10)
    findings = query_relevant(keywords, db_path, limit=limit)
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

    # Parse entry/exit if they arrived as strings
    for key in ("entry", "exit"):
        if isinstance(spec.get(key), str):
            spec[key] = json.loads(spec[key])

    # Normalise indicator field: 'name' → 'type'
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


def handle_run_backtest(args: dict, db_path: str) -> str:
    spec = _normalise_spec(args["strategy_spec"])
    try:
        result = _run_backtest(spec, db_path)
        # Return a compact version — full result can be very large
        return json.dumps({
            "viable": result.get("viable"),
            "aggregate": result.get("aggregate"),
            "slices": result.get("slices"),
            "calibration": result.get("calibration"),
        })
    except ValueError as e:
        return json.dumps({"error": str(e), "viable": False})
    except Exception as e:
        logger.error("run_backtest tool error: %s", e)
        return json.dumps({"error": f"Backtest failed: {e}", "viable": False})


def handle_get_indicator_data(args: dict, mcp_client) -> str:
    """
    mcp_client is an MCPClient instance (or None for TA-Lib-only path).
    Falls back to TA-Lib silently if MCP is unavailable.
    """
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
    row_id = write_finding(category, content, db_path, strategy_id=strategy_id)
    return json.dumps({"success": True, "id": row_id})


def handle_save_validated_strategy(
    strategy_spec: dict,
    backtest_results: dict,
    db_path: str,
) -> int:
    """
    Write a validated strategy + calibration to the strategies table.
    Returns the new strategy id.
    """
    import json as _json
    calibration = backtest_results.get("calibration", {})
    conn = get_connection(db_path)
    cur = conn.execute(
        "INSERT INTO strategies (name, spec, performance, degradation_threshold, position_sizing, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'active', ?)",
        (
            strategy_spec.get("name", "Unnamed"),
            _json.dumps(strategy_spec),
            _json.dumps(backtest_results.get("aggregate", {})),
            calibration.get("degradation_threshold"),
            _json.dumps(calibration.get("position_sizing", {})),
            int(time.time() * 1000),
        ),
    )
    conn.commit()
    return cur.lastrowid
