# Decision: Use Claude Console (anthropic.com) only for prompt design, not as the agent runtime

**Date**: 2026-06-04

## Decision

Keep `strategy_agent.py` and `analyst_agent.py` as the production agent layer in Python; use Claude Console only for prompt iteration, structured-output prototyping, and extended-thinking testing.

## Reason

Console runs in Anthropic's cloud and cannot reach the local SQLite DB, local backtest engine, MCP subprocess, or TA-Lib fallback. The tool handlers must live next to the data.

## Alternatives Considered

- **Move agents into Console agent builder** — rejected: tools like `run_backtest`, `query_knowledge_base`, `get_indicator_data` execute against local Python/SQLite and have no way to be exposed to a cloud-hosted Console agent.


## Related

- MOC: [[agents]]
- [[2026-04-11-prompt-files-load-at-import]]
