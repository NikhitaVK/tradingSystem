# Task: Build Module 3 — Strategy Agents (Loop 1)

## Goal

Implement `src/agents/` (claude_client, strategy_agent, analyst_agent, tools, mcp_client) and `src/loop1.py` orchestration. Get all 7 isolation tests in `tests/test_loop1.py` passing using a mock Claude client.

## Relevant Context

- Module 2 must be complete (11/11 tests passing) before starting this
- `src/agents/` and `src/loop1.py` are fully implemented with all 7 tests passing
- Tool schemas, prompt files, thinking block handling detailed in `claude_docs/modules/agents.md`
- 7 isolation test criteria in `claude_docs/modules/agents.md`

## Requirements

- `ClaudeClient.chat()`: thinking block preservation (JSON serialise/restore), rate limit retry with exponential backoff, session call counter, logs to `reasoning_logs`
- `strategy_agent.generate_strategy()`: tool-use loop (KB query → backtest → refine), max 5 iterations, pass only most recent diagnosis
- `analyst_agent.evaluate()`: returns `{pass, diagnosis, challenges}`; `reflect()`: returns diagnosis string, writes to KB
- `tools.py`: all 5 tool schemas as Anthropic tool definitions + handler functions
- `mcp_client.py`: subprocess JSON-RPC wrapper for TradingView MCP, TA-Lib fallback on timeout/failure
- All prompts loaded at module import time (not at call time)
- Mock Claude client in tests returns predetermined sequences (no real API calls)
- All 7 isolation tests pass before moving to Module 4

## Files Involved

- `src/agents/claude_client.py`
- `src/agents/strategy_agent.py`
- `src/agents/analyst_agent.py`
- `src/agents/tools.py`
- `src/agents/mcp_client.py`
- `tests/test_loop1.py`
- `prompts/strategy_agent_v1.txt`
- `prompts/analyst_eval_v1.txt`

## Done When

- `pytest tests/test_loop1.py -v` shows 7/7 passing
- Mock client produces correct tool call order (test 1)
- Fail path correctly feeds diagnosis back to strategy agent (test 2)
- KB updated on failure (test 3)
- MaxAttemptsExceeded after exactly 10 fails (test 5)
- Thinking blocks preserved across turns (test 6)


## Related

- MOC: [[_tasks]]
- [[agents]]
