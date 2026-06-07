# Task: Build Module 4 — Execution Loop (Loop 2)

## Goal

Implement `src/monitor/degradation_monitor.py`, `src/loop2.py`, and `src/main.py`. Get all 14 isolation tests in `tests/test_loop2.py` passing using mocks.

## Relevant Context

- Modules 1, 2, 3 must all be complete (all isolation tests passing) before starting this
- `src/monitor/` is currently empty
- `src/loop2.py` and `src/main.py` do not exist yet
- 6 test groups (14 tests total) defined in `claude_docs/modules/execution.md`

## Requirements

### Degradation Monitor (`src/monitor/degradation_monitor.py`)
- `DegradationMonitor` class with background thread
- Reads `trades` table directly, computes rolling win rate over `window` trades
- Threshold from strategy calibration (mean - std of slice win rates, floor 0.30)
- `flag.is_set()` returns True when rolling win rate drops below threshold
- Time-based fallback: trigger if no trades for `STALE_STRATEGY_HOURS` (48h from settings)

### Risk Agent (`src/agents/risk_agent.py`)
- Pure arithmetic class — no Claude, no I/O
- Hard limits: MAX_POSITION_PCT=5%, MAX_CONCURRENT=3, MAX_DAILY_LOSS=3%
- Adjust oversized positions down (don't reject outright), unless daily loss limit hit

### Execution Agent (`src/agents/execution_agent.py`)
- `place_trade()` with Binance Testnet market order
- Order amount in **base currency** (BTC for BTC/USDT), not USDT
- OCO for stop/TP; fallback polling on rejection
- Log to `trades` table

### Loop 2 (`src/loop2.py`)
- `run_loop2(strategy, db_path)` raises `StrategyDegradedException` on degradation
- Correct order: monitor check → signal detection → risk review → CP2 analyst → execution
- Sleep between iterations

### Main (`src/main.py`)
- Outer loop: `run_loop1()` → `run_loop2()` → on `StrategyDegradedException` restart Loop 1
- Call `init_db()` once at startup
- Graceful SIGTERM/SIGINT: mark open trades as 'interrupted'

## Files Involved

- `src/monitor/degradation_monitor.py`
- `src/agents/risk_agent.py`
- `src/agents/execution_agent.py`
- `src/loop2.py`
- `src/main.py`
- `tests/test_loop2.py`

## Done When

- `pytest tests/test_loop2.py -v` shows 14/14 passing
- Group A: signal detection fires correctly on synthetic data (tests 1-2)
- Group B: risk agent correctly rejects/adjusts (tests 3-5)
- Group C: CP2 analyst confirmation gates execution (tests 6-7)
- Group D: trade placed and logged (tests 8-9)
- Group E: degradation monitor triggers correctly (tests 10-11)
- Group F: full integration with correct order and exception handling (tests 12-14)


## Related

- MOC: [[_tasks]]
- [[execution]]
