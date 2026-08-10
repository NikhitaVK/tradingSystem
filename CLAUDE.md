# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This System Does

Autonomous cryptocurrency **paper-trading** research system. Two-loop architecture, driven by `src/main.py`:

- **Loop 1** (`src/loop1.py`): Screens pairs → detects market regime → loads layered KB memory → empirical candidate search → LLM selects a survivor → adversarial analyst debate → saves validated strategy to DB.
- **Loop 2** (`src/loop2.py`): Polls live candles → detects signals → risk agent → analyst brief (CP2) → places paper trades → background degradation monitor. Raises `StrategyDegradedException`, which `main.py` catches to restart Loop 1.

All four modules are built and their test suites pass. **121 passed, 1 skipped** (`SKIP_LIVE_TESTS=1`).

## Environment Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY and BINANCE_TESTNET_* keys
```

## Common Commands

```bash
# Run all tests (skip live Binance tests — no network/keys needed)
SKIP_LIVE_TESTS=1 pytest tests/ -v

# Run a single test file / single test
SKIP_LIVE_TESTS=1 pytest tests/test_loop2.py -v
SKIP_LIVE_TESTS=1 pytest tests/test_backtest.py::test_look_ahead_bias -v

# Run the full system (Loop 1 → Loop 2 → restart on degradation)
python src/main.py

# Ingest a BlackBull CSV into SQLite
python -m src.data.ingestor --csv data/BTCUSD_H1.csv --symbol BTC/USDT --timeframe 1h

# Loop 2 smoke test without running main.py
python -m scripts.run_loop2_smoke

# Knowledge base GUI (tkinter — needs a Python with Tcl/Tk 8.6, no venv required)
python3 -m src.gui.kb_gui        # module form; running the .py directly breaks imports

# Regenerate Obsidian code-graph notes
python -m scripts.sync_obsidian_graph
```

## Architecture

### Module Status

| Module | Directory | Test File | Tests | Status |
|---|---|---|---|---|
| 1 — Data Pipeline | `src/data/` | `test_data_pipeline.py`, `test_knowledge_base.py`, `test_memory_feedback.py` | 6 / 17 / 4 | Complete |
| 2 — Backtest Engine | `src/backtest/` | `test_backtest.py` | 26 | Complete |
| 3 — Strategy Agents | `src/agents/`, `src/loop1.py` | `test_loop1.py`, `test_candidate_generator.py`, `test_empirical_search.py` | 21 / 8 / 5 | Complete |
| 4 — Execution Loop | `src/exchange/`, `src/monitor/`, `src/loop2.py` | `test_loop2.py` | 28 | Complete |
| Live smoke | — | `test_binance_live.py` | 7 | Requires testnet keys |

Build order was strict — each module's tests had to pass before the next began. Keep it that way for new work.

### Import Dependency Order

```
src/data/ → src/backtest/ → src/agents/ → src/exchange/ → src/loop1.py → src/loop2.py → src/main.py
```

### Strategy Discovery Pipeline (Loop 1)

The LLM is a **selector, not a generator**. The old LLM tool-use loop was replaced on 2026-04-17.

```
screen_pair_universe()      → top 5 pairs by RSI signal density (single-pass, NOT run_backtest)
detect_current_regime()     → HMM-based regime label
get_working_memory()        → FinMem layered KB context, regime-weighted
candidate_generator.py      → 12 mechanism-diverse specs, deterministic, no LLM
empirical_search.py         → backtests all, ranks by composite score, returns top-K
strategy_agent.generate()   → ONE Claude call selects best survivor + articulates mechanism
analyst_agent.evaluate()    → Debate CP1 → verdict: pass | probation | fail
run_backtest()              → final calibration
handle_save_validated_strategy() → strategies table (status = active | probation)
purge_kb() + promote_memories()  → memory maintenance
```

Failures write a `failure_diagnosis` to the KB and record a row in `strategy_evolutions` (spec delta + performance delta + outcome class). Only the **most recent** diagnosis is passed forward on retry — the agent queries the KB for the rest.

Composite score in `empirical_search._compute_score()`: `profit_factor × WFE × (1 − regime_concentration)`.

### Execution Pipeline (Loop 2)

```
DegradationMonitor.start()   → daemon thread watching the trades table
loop:
  flag set? → analyst.reflect() → KB write → raise StrategyDegradedException
  candles → build_signals() → drop incomplete candle
  signal → compute_position_size() → RiskAgent.review() (arithmetic, no LLM)
         → analyst.evaluate_brief() (CP2, 2000 thinking tokens)
         → execution_agent.place_trade() → trades table
  sleep until next candle close
```

`RiskAgent` is deterministic arithmetic — Claude is never called there, for latency reasons. The analyst is not called if risk already rejected.

### Exchange Abstraction

`src/exchange/factory.build_exchange()` returns either `PaperExchange` (simulated fills against real prices, tracks balance in the DB) or a real CCXT exchange, based on `EXECUTION_MODE` (`paper` | `live`). **`live` currently raises `NotImplementedError`.** The rest of the system is written against the CCXT-compatible interface and does not know which it got.

### Layered Memory (FinMem-style)

`src/data/memory_layers.py` assigns each KB entry a layer (`shallow` / `intermediate` / `deep`) and an importance score, with decay, promotion, demotion, and purge rules. `src/data/memory_feedback.py` boosts importance for entries that contributed to a good outcome. `knowledge_base.get_working_memory()` retrieves Top-K per layer (`COGNITIVE_SPAN_K`), weighting regime matches +50%.

`knowledge_base.query_relevant()` has two modes: keyword SQL, or — when `query_context` is passed — semantic reranking via **Claude Haiku** (`CLAUDE_HAIKU_MODEL`).

### SQLite Tables

`ohlcv_history`, `live_candles`, `strategies`, `trades`, `performance`, `knowledge_base`, `strategy_evolutions`, `reasoning_logs` — all in `src/data/schema.py`. `init_db()` is called **once**, at the top of `main()`, nowhere else. `knowledge_base` carries `regime`, `mechanism`, `conditions`, `layer`, `importance` columns beyond the base spec.

### Claude API Configuration

All Claude calls go through `ClaudeClient.chat()`, which auto-logs to `reasoning_logs` and handles rate-limit backoff. Everything tunable lives in `config/settings.py` — never hardcode elsewhere.

```python
CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_HAIKU_MODEL = "claude-haiku-4-5-20251001"  # semantic KB reranking only
CLAUDE_THINKING_BUDGET_STRATEGY      = 8000
CLAUDE_THINKING_BUDGET_ANALYST       = 5000
CLAUDE_THINKING_BUDGET_ANALYST_BRIEF = 2000   # CP2 quick checks
CLAUDE_MAX_TOKENS = 16000  # must comfortably exceed the largest thinking budget
```

### Prompt Versioning

Prompts load at **module import time** from `prompts/` — a missing file must fail at startup, not mid-run after expensive screening. Currently live: `strategy_agent_v2.txt` (LLM-as-selector) and `analyst_eval_v2.txt` (verdict/score schema). The `_v1` files are kept as ablation baselines — never overwrite a version, add the next one.

## Key Implementation Rules

### Current calibration values differ from the module specs

`.claude/rules/modules/*.md` records the original design values. `config/settings.py` is the source of truth and has moved on:

| Parameter | Spec doc says | Actual |
|---|---|---|
| `BACKTEST_N_SLICES` | 3 | **5** |
| `BACKTEST_MIN_TRADES_PER_SLICE` | 10 | **5** |
| `LOOP1_MAX_ATTEMPTS` | 10 | **5** |

### Cost model is richer than flat slippage

Beyond `SLIPPAGE_PER_SIDE` + `EXCHANGE_FEE_PER_SIDE` (0.1% each), the engine applies `STOP_LOSS_EXTRA_SLIPPAGE` (gap-through penalty on stop exits) and volume-proportional scaling — positions above `VOLUME_SLIPPAGE_THRESHOLD` of bar volume scale slippage up to `VOLUME_SLIPPAGE_MAX_MULTIPLIER`.

### Sharpe annualisation is timeframe-dependent

`sqrt(252)` on 1h data gives a Sharpe ~8.8× too low. Use:

```python
PERIODS_PER_YEAR = {"1m": 252*24*60, "5m": 252*24*12, "15m": 252*24*4,
                    "1h": 252*24, "4h": 252*6, "1d": 252}
```

### Look-ahead bias prevention

`.shift(1)` on signal arrays so bar `t`'s signal fires at the open of bar `t+1`. Zero out signals during indicator warm-up. Reset the mask at the start of each walk-forward slice. Loop 2 additionally calls `_drop_incomplete_candle()` — never trade on a candle that hasn't closed.

### CCXT order amounts are in base currency

`create_order` expects BTC quantity, not USDT. Always `amount_usdt / ticker["last"]` first.

### Strategy spec shape is the contract

Between `src/backtest/engine.py`, `strategy_runner.build_signals()`, and every agent. Never change one side alone. `tools.py:_normalise_spec()` absorbs Claude's double-encoding patterns. Indicators supported by the DSL: RSI, EMA, MACD, BB, ATR, ADX.

### Thinking blocks are cryptographically signed

Pass them back exactly as received — never summarise or strip. Serialise the whole block including its signature:

```python
thinking_json = json.dumps([b.model_dump() for b in response.content if b.type == "thinking"])
```

### Probationary tier

An analyst verdict of `probation` (score 0.50–0.70) still deploys, but at `PROBATION_SIZE_MULTIPLIER` size, with the degradation threshold raised by `PROBATION_THRESHOLD_BUMP` and the stale-check window halved. Auto-demotes after `PROBATION_DEMOTE_LOSSES` consecutive losses, promotes after `PROBATION_PROMOTE_WINS`.

### Empirical Search Parameters

```python
CANDIDATE_POOL_SIZE = 12              # mechanism-diverse candidates per attempt
EMPIRICAL_SEARCH_TOP_K = 3            # top candidates passed to the LLM selector
EMPIRICAL_SEARCH_MIN_VIABLE_PF = 1.2  # profit factor floor
CANDIDATE_EARLY_TERM_MIN_TRADES = 5   # early termination
```

## Documentation

| Working on | Read |
|---|---|
| Project architecture | `claude_docs/architecture/overview.md` |
| Modules 1–4 | `claude_docs/modules/{data_pipeline,backtesting,agents,execution}.md` |
| Design decisions (ADRs) | `claude_docs/decisions/` |
| Testing methodology | `.claude/rules/testing/` |
| All docs index | `claude_docs/dashboard.md` |

### Obsidian code graph

`claude_docs/code/` holds one AUTO-GENERATED companion note per `.py` file so the code appears in the Obsidian graph with its import edges (Obsidian can't read wikilinks inside `.py`). **Never hand-edit these notes.** They are regenerated by `scripts/sync_obsidian_graph.py`, run on every commit via `scripts/hooks/pre-commit` (activate once per clone with `git config core.hooksPath scripts/hooks`). The vault also contains `code-map.base` / `decisions.base` dashboards and `system-decomposition.canvas`.

## Pending Work

All four modules and all ten planned improvement phases (HMM regime detection, regime-aware KB, strategy evolution tracking, semantic retrieval, layered memory, template library, MTF confirmation, adversarial regime robustness) are in `claude_docs/tasks/complete/`. What remains:

- **Calibration items 3 and 4** in `.claude/rules/testing/calibration_tests.md` are still unrecorded: the degradation window/threshold false-positive simulation, and the 30/50/100-trade statistical significance comparison (the DB holds only a couple of completed trades).
- **Live execution mode** — `exchange/factory.py` raises `NotImplementedError` for `EXECUTION_MODE=live`.
- Known open issues: `claude_docs/issues/`.
- Stale artefacts worth tidying: `claude_docs/tasks/pending/module4_execution_loop.md` describes completed work, and `claude_docs/tasks/pending/phases/` is now empty. `README.md` tells users to run `python -m src.gui.app`, but the module is `src.gui.kb_gui`.
