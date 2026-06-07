# Module 3 — Strategy Discovery Agents (Loop 1)

**Status**: Complete  
**Isolation test**: `tests/test_loop1.py` — 7/7 passing  
**Depends on**: Module 1 (KB, schema), Module 2 (backtest engine)

## Purpose

Discover and validate trading strategies via a two-phase agent loop: strategy generation (Claude with extended thinking) followed by adversarial analyst evaluation. On failure, write diagnosis to KB and retry with context. On success, calibrate thresholds and trigger Loop 2.

## Loop 1 Flow

```
1. screen_pair_universe()           → top 5 candidates by RSI signal density
2. kb.query_relevant([...])         → prior findings as context
3. strategy_agent.generate(...)     → strategy_spec + backtest_results
4. analyst_agent.evaluate(...)      → {pass, diagnosis, challenges}
   - FAIL: kb.write_finding('failure_diagnosis', diagnosis)
           → retry with diagnosis as context, attempt += 1
           → if attempt == max_attempts: raise MaxAttemptsExceeded
   - PASS:
5. run_backtest(strategy_spec)      → final calibration (IS+OOS)
6. save_validated_strategy(...)     → strategies table
7. return strategy dict
```

## Claude Configuration

```python
MODEL = "claude-sonnet-4-6"   # from config.settings.CLAUDE_MODEL
THINKING_BUDGET_STRATEGY = 8000    # tokens — strategy generation
THINKING_BUDGET_ANALYST  = 5000    # tokens — analyst evaluation (CP1)
THINKING_BUDGET_ANALYST_BRIEF = 2000  # tokens — brief CP2 check in Loop 2
MAX_TOKENS = 16000           # must exceed largest thinking budget
```

**Thinking block rule**: Claude extended thinking responses include cryptographically signed blocks. These must be passed back to the API exactly as received in multi-turn conversations. Never summarise, truncate, or strip them.

## Pair Universe Screening

1. Fetch all USDT spot pairs from CCXT with 24h volume > $50M USD
2. Cap at 20 candidates, rank by volume
3. Score each by RSI(14) signal density on 90-day 1h data (lightweight pass — does NOT call `run_backtest`)
4. Return top 5 pairs (with signal count and volume as context)

Single-pass ranking only — the full walk-forward backtest runs only after the strategy agent selects a pair and produces a complete hypothesis.

## Tool Schemas

| Tool | Called By | Purpose |
|---|---|---|
| `query_knowledge_base` | Strategy agent | Retrieve prior findings before hypothesising |
| `run_backtest` | Strategy agent | Test the current hypothesis |
| `get_indicator_data` | Strategy agent | Fetch real indicator values for validation |
| `write_to_knowledge_base` | Analyst (reflection mode) | Persist degradation diagnoses |
| `save_validated_strategy` | Loop 1 orchestrator | Write final strategy + calibration to DB |

All defined as Anthropic tool schemas in `src/agents/tools.py`. Prompt strings live in `prompts/` as `.txt` files — never inline in Python.

## Prompt Versioning

```
prompts/
├── strategy_agent_v1.txt
├── analyst_eval_v1.txt
└── analyst_reflect_v1.txt
```

Version increment on change. Keep old versions as baseline. Test each version against the test cases in `.claude/rules/testing/calibration_tests.md` before deploying.

## Key Files

### `src/agents/claude_client.py`
```python
class ClaudeClient:
    def chat(messages, tools, system_prompt, thinking_budget)
        -> tuple[response_text, tool_calls, updated_messages]
```
- Handles thinking block preservation across turns (JSON serialise/restore)
- Logs full thinking + response to `reasoning_logs` table
- Rate limit retry with exponential backoff (2s, 4s, 8s — max 3 retries)
- Session call counter warns at 80% of `MAX_CALLS_PER_SESSION`, raises at 100%

### `src/agents/strategy_agent.py`
```python
def generate_strategy(pair_candidates, kb_context, client, db_path, mcp_client, previous_diagnosis)
    -> tuple[dict, dict]
```
- Tool-use loop internally: KB query → hypothesis → backtest → refine
- Max 5 internal iterations before returning to debate checkpoint
- Passes only the most recent failure diagnosis (not all history)

### `src/agents/analyst_agent.py`
```python
def evaluate(strategy_spec, backtest_results, client) -> dict:
    # Returns {'pass': bool, 'diagnosis': str, 'challenges': list[str]}
def reflect(strategy, recent_trades, performance_history, client) -> str:
    # Returns diagnosis string. Writes findings to KB internally.
```

### `src/loop1.py`
- `run_loop1(db_path, max_attempts=10) -> dict` — full orchestrator
- `screen_pair_universe(db_path) -> list[dict]` — CCXT-based screener with fallback
- `MaxAttemptsExceeded` raised after `max_attempts` failures

### `src/agents/mcp_client.py`
- Subprocess wrapper for TradingView MCP server (`json-rpc over stdin/stdout`)
- Falls back to TA-Lib automatically on timeout or process failure
- `get_indicator_data` tool returns same structure regardless of source

## Critical Implementation Notes

- **Prompts loaded at import time** — `STRATEGY_PROMPT = Path("prompts/strategy_agent_v1.txt").read_text()` at module level
- **Retry context**: pass only the **most recent** diagnosis to the strategy agent on retry
- **CLAUDE_MAX_TOKENS = 16000** — must comfortably exceed `THINKING_BUDGET_STRATEGY=8000`
- **Order amount in base currency, not USDT** — `amount = amount_usdt / ticker["last"]` for CCXT

## Isolation Test Criteria (7 tests)

1. Correct tool call order — mock returns `query_knowledge_base` → `run_backtest` → text; assert in that order
2. Fail path feeds diagnosis — analyst returns `{pass: False, diagnosis: "..."}`, assert diagnosis in next strategy agent call
3. KB updated on failure — after failed attempt, new `knowledge_base` row with category `failure_diagnosis`
4. Pass path saves strategy — analyst returns `{pass: True}`, assert `save_validated_strategy` tool called with valid spec
5. MaxAttemptsExceeded after 10 fails — mock analyst always fails, assert exception after exactly 10 attempts
6. Thinking block preservation — mock thinking blocks in turn 1 present in turn 2 unmodified
7. Pair screener narrows to 5 — mock CCXT returns 30 pairs, assert strategy agent receives exactly 5

## Known Issues

- None


## Related

- MOC: [[_modules]]
- [[2026-04-10-two-loop-debate-checkpoints]]
- [[2026-04-10-claude-max-tokens-raised-to-16000]]
- [[2026-04-11-thinking-block-preservation]]
- [[2026-04-11-prompt-files-load-at-import]]
- [[2026-04-11-mcp-subprocess-talib-fallback]]
- [[2026-04-15-finmem-layered-memory]]
- [[2026-04-16-llm-as-selector-empirical-search]]
- [[2026-04-20-three-way-verdict-composite-score]]
- [[2026-04-20-probationary-tier]]
- [[2026-04-20-rejected-names-blacklist]]
- [[2026-06-04-claude-console-prompt-design-only]]
