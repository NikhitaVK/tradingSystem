# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Docs

Detailed module specs, testing methodology, and architectural rules live in `.claude/`. Always read `.claude/PROJECT_CONTEXT.md` first — it governs the entire project and links to per-module context files. Load only the relevant module file when working on a specific area.

## Environment Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in API keys
```

## Common Commands

```bash
# Run all tests
pytest tests/ -v

# Run a single test file
pytest tests/test_data_pipeline.py -v

# Run a single test by name
pytest tests/test_data_pipeline.py::test_csv_round_trip -v

# Skip tests that require live Binance Testnet keys
SKIP_LIVE_TESTS=1 pytest tests/test_data_pipeline.py -v

# Ingest a BlackBull CSV into SQLite
python -m src.data.ingestor --csv data/BTCUSD_H1.csv --symbol BTC/USDT --timeframe 1h

# Run the full system (once all modules are built)
python src/main.py
```

## Architecture

Two-loop system: **Loop 1** discovers and validates trading strategies, **Loop 2** executes them live and monitors for degradation. When Loop 2 detects degradation, it triggers Loop 1 to restart discovery.

```
Loop 1: Universe Scan → Pair Screener → Strategy Agent (Claude) → Backtest
        → Analyst Debate (CP1) → Calibrate → Save → Trigger Loop 2

Loop 2: CCXT Poll → Signal Detection → Risk Agent (arithmetic) → Analyst (CP2)
        → Execution Agent (Binance Testnet) → Log
        Background: Degradation Monitor → if triggered → Analyst Reflect → Loop 1
```

Four modules built in strict order — each must pass its isolation tests before the next begins:

| Module | Directory | Isolation Test | Status |
|---|---|---|---|
| 1 — Data Pipeline | `src/data/` | `tests/test_data_pipeline.py` | **Complete** |
| 2 — Backtest Engine | `src/backtest/` | `tests/test_backtest.py` | Not started |
| 3 — Strategy Agents | `src/agents/`, `src/loop1.py` | `tests/test_loop1.py` | Not started |
| 4 — Execution Loop | `src/monitor/`, `src/loop2.py` | `tests/test_loop2.py` | Not started |

## Key Contracts

- **Strategy spec** is a JSON dict — the immutable contract between the backtest engine and all agents. Shape defined in `.claude/rules/modules/module2_backtest.md`.
- **Thinking blocks** from Claude extended thinking are cryptographically signed and must be passed back to the API exactly as received — never modify or summarise them.
- **All DB timestamps** are Unix milliseconds UTC.
- **Prompts** live in `prompts/` as versioned `.txt` files (e.g. `strategy_agent_v1.txt`) — never inline in Python.
- **`config/settings.py`** is the single source of truth for all tuneable parameters (slippage, thinking budgets, risk limits, etc.). Never hardcode these elsewhere.
