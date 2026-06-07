# Architecture Overview

## Project Purpose

Autonomous cryptocurrency trading system that removes human emotional involvement from both strategy discovery and execution. Two-loop architecture: Loop 1 discovers strategies, Loop 2 executes them live and monitors for degradation.

**Core insight**: 84% of traders fail due to neurological emotional responses. This system mimics disciplined successful traders — universe filtering, hypothesis generation, adversarial review, calibrated risk — without the psychology.

## Tech Stack

| Component | Technology |
|---|---|
| Runtime | Python 3.11+ |
| Strategy Agent | Claude Sonnet 4.6 + extended thinking |
| Analyst Agent | Claude Sonnet 4.6 (separate instances for eval vs reflect) |
| Market Data | CCXT (Binance Testnet sandbox) |
| Indicators | TradingView MCP server + TA-Lib fallback |
| Persistence | SQLite (WAL mode, foreign keys ON) |
| Config | `config/settings.py` — single source of truth for all parameters |

## Repository Layout

```
tradingSystemv0.01/
├── src/
│   ├── data/              # Module 1: ingestion, CCXT feed, KB CRUD, schema
│   ├── backtest/          # Module 2: engine, indicators, strategy runner
│   ├── agents/            # Module 3: Claude client, strategy/analyst agents, tools, MCP
│   ├── monitor/           # Module 4: degradation monitor (empty — not built yet)
│   ├── loop1.py           # Loop 1 orchestrator
│   ├── loop2.py           # Loop 2 orchestrator (not built)
│   └── main.py            # Outer loop (not built)
├── tests/
│   ├── test_data_pipeline.py  # Module 1 — PASSING
│   ├── test_backtest.py        # Module 2 — not passing
│   ├── test_loop1.py           # Module 3 — not passing
│   └── test_loop2.py           # Module 4 — not built
├── config/settings.py     # All tuneable parameters (import from here, never hardcode)
├── prompts/               # Versioned agent prompts (v1, v2...)
├── .claude/               # Original spec docs (do not edit or delete)
│   ├── PROJECT_CONTEXT.md
│   ├── PLANNED_IMPROVEMENTS.md
│   └── rules/modules/    # Detailed per-module specs
└── claude_docs/          # New modular documentation
```

## System Architecture

### Two-Loop Design

```
┌─────────────────────────────────────────────────────────┐
│  LOOP 1 — Strategy Discovery                            │
│  ┌──────────┐   ┌──────────┐   ┌───────────────────┐   │
│  │ Universe │ → │  Pair    │ → │ Strategy Agent    │   │
│  │ Scan     │   │ Screener │   │ (Claude, 8k tok)  │   │
│  └──────────┘   └──────────┘   └─────────┬─────────┘   │
│                                          ↓               │
│  ┌──────────┐   ┌──────────┐   ┌───────────────────┐   │
│  │ Backtest │ ← │ Analyst  │ ← │ Debate CP1        │   │
│  │ Engine   │   │ Eval     │   │ (Claude, 5k tok)  │   │
│  └────┬─────┘   └────┬─────┘   └───────────────────┘   │
│       ↓              ↓                                  │
│  ┌──────────┐   ┌──────────┐                          │
│  │ Save     │ ← │ Calibrate│                          │
│  │ Strategy │   │ Thresholds                           │
│  └────┬─────┘   └──────────┘                          │
│       ↓                                                  │
│  ═══════════════ TRIGGER LOOP 2 ════════════════════════│
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│  LOOP 2 — Live Execution                               │
│  ┌──────────┐   ┌──────────┐   ┌───────────────────┐  │
│  │  CCXT    │ → │ Signal   │ → │ Risk Agent        │  │
│  │  Poll    │   │ Detection│   │ (arithmetic only)  │  │
│  └──────────┘   └──────────┘   └─────────┬─────────┘  │
│                                             ↓           │
│  ┌──────────┐   ┌──────────┐   ┌───────────────────┐  │
│  │ Execution│ ← │ Analyst  │ ← │ Debate CP2        │  │
│  │ Agent    │   │ Brief    │   │ (Claude, 2k tok)   │  │
│  │ (BN Testnet)    │          │   └───────────────────┘  │
│  └──────────┘   └──────────┘                            │
│                     ↑                                    │
│  ═══ Degradation Monitor (background thread) ═══════════│
│       If triggered → Analyst Reflect → KB write          │
│       → StrategyDegradedException → Loop 1 restarts      │
└─────────────────────────────────────────────────────────┘
```

## Module Status

| # | Module | Directory | Isolation Test | Status |
|---|---|---|---|---|
| 1 | Data Pipeline | `src/data/` | `tests/test_data_pipeline.py` | **Complete** |
| 2 | Backtest Engine | `src/backtest/` | `tests/test_backtest.py` | Not started |
| 3 | Strategy Agents | `src/agents/`, `src/loop1.py` | `tests/test_loop1.py` | Not started |
| 4 | Execution Loop | `src/monitor/`, `src/loop2.py` | `tests/test_loop2.py` | Not built |

Build order is strict: each module's isolation tests must pass before the next begins.

## High-Level Data Flow

1. **BlackBull CSV** → `ingestor.py` → `ohlcv_history` table
2. **CCXT Live Poll** → `ccxt_feed.py` → `live_candles` table
3. **Loop 1 Screener** → CCXT → top 5 pairs by RSI signal density → strategy agent
4. **Strategy Agent** → tools (`run_backtest`, `query_knowledge_base`) → strategy spec
5. **Analyst Eval** → approves/rejects strategy spec + backtest results
6. **Approved strategy** → `strategies` table (with calibration data)
7. **Loop 2** → reads active strategy spec → monitors live candles → fires signals
8. **Degradation Monitor** → watches `trades` table → triggers `analyst.reflect()` → KB write → restarts Loop 1

## Key Architectural Rules

1. **No circular imports**: data → backtest → agents → loops
2. **Strategy spec is the contract**: JSON dict shared by backtest engine and all agents. Never change its shape without updating both sides.
3. **Thinking blocks are immutable**: Claude extended thinking responses include cryptographically signed blocks. Pass them back to the API exactly as received — never summarise or strip.
4. **Prompts are versioned files**: `prompts/strategy_agent_v1.txt`, never inline in Python
5. **One `init_db()` call**: at startup in `main.py` only
6. **All agent calls logged**: full reasoning trace → `reasoning_logs` table
7. **No hardcoded credentials**: `.env` + `python-dotenv`

## Current System State

- **Module 1 (Data Pipeline)** — complete, all 6 isolation tests passing
- **Module 2 (Backtest Engine)** — complete, all 11 isolation tests passing
- **Module 3 (Strategy Agents + Loop 1)** — complete, all 7 isolation tests passing
- **Module 4 (Execution Loop)** — not built; `src/monitor/` is empty, `src/loop2.py` and `src/main.py` do not exist
- `trading_system.db` (5MB) already exists with historical data
- **Planned improvement**: HMM-based regime detection + layered KB memory system (see `.claude/PLANNED_IMPROVEMENTS.md`)


## Related

- MOC: [[_architecture]]
- [[2026-05-18-four-module-decomposition]]
- [[2026-04-10-two-loop-debate-checkpoints]]
- [[2026-04-16-llm-as-selector-empirical-search]]
- [[2026-04-13-structured-context-system]]
