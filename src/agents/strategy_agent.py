"""
strategy_agent.py — Hypothesis-first strategy generation using Claude extended thinking.

The agent is prompted to articulate an economic mechanism BEFORE designing any
indicator rules. This prevents data mining: strategies must be grounded in a
real market inefficiency, not parameter search.

Internal tool-use loop (up to 5 iterations):
  1. query_knowledge_base  — learn what has failed and why
  2. (reasoning: mechanism hypothesis + regime requirement + indicator design)
  3. run_backtest          — validate the hypothesis against real price history
  4. (reasoning: does the result confirm or contradict the mechanism?)
  5. Optionally refine and re-run (max 4 backtests per generate() call)

Returns: (strategy_spec dict, backtest_results dict)
"""
import json
import logging
from pathlib import Path

from config.settings import CLAUDE_THINKING_BUDGET_STRATEGY
from src.agents.tools import (
    STRATEGY_AGENT_TOOLS,
    handle_query_knowledge_base,
    handle_run_backtest,
    handle_get_indicator_data,
)

logger = logging.getLogger(__name__)

# Load prompt at import time — fail immediately if file is missing
_PROMPT_PATH = Path("prompts/strategy_agent_v1.txt")
_STRATEGY_PROMPT = _PROMPT_PATH.read_text()

_MAX_TOOL_ITERATIONS = 5   # internal tool-use iterations per generate() call
_MAX_BACKTESTS = 4         # max run_backtest calls per generate() call


def generate_strategy(
    pair_candidates: list[dict],
    kb_context: list[dict],
    client,  # ClaudeClient instance
    db_path: str,
    mcp_client=None,
    previous_diagnosis: str = None,
) -> tuple[dict, dict]:
    """
    Generate a strategy spec and backtest results using hypothesis-first reasoning.

    Args:
        pair_candidates:    Top 5 pairs from screener with metrics.
        kb_context:         Recent KB findings (pre-fetched by loop1.py).
        client:             ClaudeClient instance.
        db_path:            Path to SQLite DB.
        mcp_client:         MCPClient instance (or None — TA-Lib fallback used).
        previous_diagnosis: Most recent failure diagnosis from KB (passed on retry).

    Returns:
        (strategy_spec, backtest_results)

    Raises:
        ValueError: If Claude does not produce a strategy spec after max iterations.
    """
    system_prompt = _build_system_prompt(kb_context, pair_candidates, previous_diagnosis)

    # Initial user message
    messages = [
        {
            "role": "user",
            "content": (
                "Please analyse the available pairs and knowledge base context, "
                "then develop a strategy hypothesis following the four reasoning steps. "
                "Remember: state the economic mechanism BEFORE calling run_backtest()."
            ),
        }
    ]

    strategy_spec = None
    backtest_results = None
    backtest_count = 0

    for iteration in range(_MAX_TOOL_ITERATIONS):
        response_text, tool_calls, messages = client.chat(
            messages=messages,
            tools=STRATEGY_AGENT_TOOLS,
            system_prompt=system_prompt,
            thinking_budget=CLAUDE_THINKING_BUDGET_STRATEGY,
            agent_name="strategy_agent",
        )

        if not tool_calls:
            # Claude finished — extract strategy spec from response text
            strategy_spec, backtest_results = _extract_final_strategy(
                response_text, backtest_results
            )
            break

        # Handle each tool call and append results
        for tc in tool_calls:
            name = tc["name"]
            args = tc["input"]
            tool_id = tc["id"]

            if name == "query_knowledge_base":
                result = handle_query_knowledge_base(args, db_path)

            elif name == "run_backtest":
                if backtest_count >= _MAX_BACKTESTS:
                    result = json.dumps({"error": f"Max backtests ({_MAX_BACKTESTS}) reached. Finalise your strategy."})
                else:
                    result = handle_run_backtest(args, db_path)
                    backtest_count += 1
                    # Track the most recent viable result
                    parsed = json.loads(result)
                    if parsed.get("viable") is not None:
                        backtest_results = parsed
                        strategy_spec = args.get("strategy_spec")

            elif name == "get_indicator_data":
                result = handle_get_indicator_data(args, mcp_client)

            else:
                result = json.dumps({"error": f"Unknown tool: {name}"})
                logger.warning("Unknown tool called by strategy_agent: %s", name)

            messages = client.append_tool_result(messages, tool_id, result)

    if strategy_spec is None:
        raise ValueError(
            f"Strategy agent did not produce a strategy spec after {_MAX_TOOL_ITERATIONS} iterations."
        )

    if backtest_results is None:
        backtest_results = {"viable": False, "aggregate": {}, "slices": [], "calibration": {}}

    return strategy_spec, backtest_results


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_system_prompt(
    kb_context: list[dict],
    pair_candidates: list[dict],
    previous_diagnosis: str,
) -> str:
    kb_str = (
        json.dumps(kb_context, indent=2)
        if kb_context
        else "No prior findings. This is the first strategy discovery cycle."
    )
    pairs_str = json.dumps(pair_candidates, indent=2) if pair_candidates else "[]"
    diag_str = (
        f"\nPREVIOUS ATTEMPT FAILURE DIAGNOSIS:\n{previous_diagnosis}\n"
        "Take this diagnosis seriously — do not repeat the same approach.\n"
        if previous_diagnosis
        else ""
    )

    return (
        _STRATEGY_PROMPT
        .replace("{kb_context}", kb_str)
        .replace("{pair_candidates}", pairs_str)
        .replace("{previous_diagnosis}", diag_str)
    )


def _extract_final_strategy(
    response_text: str,
    last_backtest_results: dict,
) -> tuple[dict, dict]:
    """
    When Claude finishes without a tool call, try to extract a JSON strategy spec
    from the response text. If not found, return whatever we have from the last backtest.
    """
    # Try to find a JSON block in the response
    import re
    json_blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)
    for block in json_blocks:
        try:
            spec = json.loads(block)
            if all(k in spec for k in ("name", "symbol", "timeframe", "indicators", "entry", "exit")):
                return spec, last_backtest_results or {}
        except json.JSONDecodeError:
            continue

    # Try to parse the entire response as JSON
    try:
        spec = json.loads(response_text.strip())
        if all(k in spec for k in ("name", "symbol", "timeframe", "indicators", "entry", "exit")):
            return spec, last_backtest_results or {}
    except (json.JSONDecodeError, AttributeError):
        pass

    # Return what we have from the last run_backtest call (strategy_spec was captured there)
    return None, last_backtest_results or {}
