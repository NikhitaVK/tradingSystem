# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This System Does

Autonomous cryptocurrency trading system. Two-loop architecture:
- **Loop 1** (`src/loop1.py`): Screens pairs, generates strategy candidates via empirical search, selects via LLM, runs adversarial analyst debate, saves validated strategy to DB.
- **Loop 2** (`src/loop2.py`, not built): Polls live CCXT data, detects signals, applies risk rules, places paper trades on Binance Testnet, monitors for degradation.

## Environment Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY and BINANCE_TESTNET_* keys
```

## Common Commands

```bash
# Run all tests (skip live Binance tests)
SKIP_LIVE_TESTS=1 pytest tests/ -v

# Run a single test file
SKIP_LIVE_TESTS=1 pytest tests/test_loop1.py -v
SKIP_LIVE_TESTS=1 pytest tests/test_backtest.py -v

# Ingest a BlackBull CSV into SQLite
python -m src.data.ingestor --csv data/BTCUSD_H1.csv --symbol BTC/USDT --timeframe 1h

# Run the full system (once Module 4 is built)
python src/main.py
```

## Architecture

### Strategy Discovery Pipeline (Module 3 — Empirical Search)

The old LLM tool-use loop was replaced on 2026-04-17. Strategy discovery now works as a pipeline:

```
1. candidate_generator.py  →  emits 12 mechanism-diverse strategy specs
2. empirical_search.py    →  backtests all candidates, ranks by composite score
3. Single LLM call        →  LLM selects best survivor and articulates mechanism
4. analyst_agent.evaluate →  adversarial debate checkpoint
```

The LLM acts as **selector**, not generator. The empirical search handles all backtesting.

### Module Status

| Module | Directory | Test File | Tests | Status |
|---|---|---|---|---|
| 1 — Data Pipeline | `src/data/` | `tests/test_data_pipeline.py` | 6 | **Complete** |
| 2 — Backtest Engine | `src/backtest/` | `tests/test_backtest.py` | 13 | **Complete** |
| 3 — Strategy Agents | `src/agents/`, `src/loop1.py` | `tests/test_loop1.py` | 7 | **Complete** |
| 4 — Execution Loop | `src/monitor/`, `src/loop2.py` | `tests/test_loop2.py` | 14 | **Not built** |

Build order is strict — each module's tests must pass before the next begins.

### Import Dependency Order

```
src/data/ → src/backtest/ → src/agents/ → src/loop1.py → src/main.py
```

### Data Flow

1. `ingestor.py` parses BlackBull CSV → `ohlcv_history` table (timestamps as Unix **milliseconds** UTC)
2. `ccxt_feed.py` polls Binance Testnet → `live_candles` table
3. Loop 1 screener → CCXT, top 5 pairs by RSI signal density → strategy agent
4. `candidate_generator.py` emits 12 mechanism-diverse specs → `empirical_search.py` backtests all, ranks by composite score → `strategy_agent.py` calls LLM once to select best survivor → strategy spec + backtest results
5. `analyst_agent.py` evaluates (Debate CP1) → approves or rejects with diagnosis
6. Approved strategy → `strategies` table with calibration data (degradation threshold, ATR position sizing)
7. Loop 2 (pending): reads active strategy → monitors live candles → risk agent → analyst brief (CP2) → paper trade
8. `DegradationMonitor` (background thread): watches `trades` table → triggers `analyst.reflect()` → KB write → raises `StrategyDegradedException` → Loop 1 restarts

### SQLite Tables

`ohlcv_history`, `live_candles`, `strategies`, `trades`, `performance`, `knowledge_base`, `reasoning_logs` — all defined in `src/data/schema.py`. `init_db()` is called **once** at startup in `main.py` only.

### Claude API Configuration

```python
# From config/settings.py
CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_THINKING_BUDGET_STRATEGY = 8000  # tokens for strategy selection
CLAUDE_THINKING_BUDGET_ANALYST  = 5000  # tokens for analyst evaluation
CLAUDE_THINKING_BUDGET_ANALYST_BRIEF = 2000  # tokens for CP2 quick checks
CLAUDE_MAX_TOKENS = 16000  # must exceed largest thinking budget
```

All Claude calls go through `ClaudeClient.chat()` which auto-logs to `reasoning_logs`.

### Prompt Versioning

Prompts are loaded at **module import time** from versioned files in `prompts/`:

```
prompts/
├── strategy_agent_v1.txt   # old tool-use loop (deprecated)
├── strategy_agent_v2.txt   # LLM-as-selector role
├── analyst_eval_v1.txt
└── analyst_reflect_v1.txt
```

## Key Implementation Rules

### Sharpe annualisation is timeframe-dependent

Using `sqrt(252)` on 1h data gives ~8.8x too low a Sharpe. Use:

```python
PERIODS_PER_YEAR = {
    "1m": 252*24*60, "5m": 252*24*12, "15m": 252*24*4,
    "1h": 252*24, "4h": 252*6, "1d": 252
}
```

### Look-ahead bias prevention

`.shift(1)` is applied to signal arrays so bar `t`'s signal fires at open of bar `t+1`. Zero out signals during indicator warm-up period. Reset mask at start of each walk-forward slice.

### CCXT order amounts are in base currency

`create_order` expects BTC quantity, not USDT. Always divide `amount_usdt / ticker["last"]` before placing.

### Strategy spec shape is the contract

Between `src/backtest/engine.py` and all agents. Never change it without updating both sides. See `tools.py:_normalise_spec()` which handles Claude's double-encoding patterns.

### Thinking blocks are cryptographically signed

Pass them back to the API exactly as received. Never summarise or strip. Serialise the full block including signature:

```python
thinking_json = json.dumps([b.model_dump() for b in response.content if b.type == "thinking"])
```

### Empirical Search Parameters

```python
CANDIDATE_POOL_SIZE = 12       # mechanism-diverse candidates per attempt
EMPIRICAL_SEARCH_TOP_K = 3    # top candidates passed to LLM selector
EMPIRICAL_SEARCH_MIN_VIABLE_PF = 1.2  # profit factor floor (delegated to analyst)
CANDIDATE_EARLY_TERM_MIN_TRADES = 5   # early termination if trades < 5
LOOP1_MAX_ATTEMPTS = 3        # reduced from 10 after refactor
```

## Documentation

| Working on | Read |
|---|---|
| Project architecture | `claude_docs/architecture/overview.md` |
| Module 1 | `claude_docs/modules/data_pipeline.md` |
| Module 2 | `claude_docs/modules/backtesting.md` |
| Module 3 | `claude_docs/modules/agents.md` |
| Module 4 | `claude_docs/modules/execution.md` |
| Testing methodology | `.claude/rules/testing/` |
| All docs index | `claude_docs/dashboard.md` |

## Pending Work

Module 4 task spec: `claude_docs/tasks/pending/module4_execution_loop.md`

Planned improvements (HMM regime detection, layered KB memory, semantic retrieval, multi-timeframe confirmation): `claude_docs/tasks/pending/phases/` — see `implementation_order.md` for sequencing.
