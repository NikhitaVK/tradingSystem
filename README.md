# 91906/7 — Trading System

Program developed for my 13 Digital Technologies assessment.

**Author:** Nikhita Krisson
**Date:** August 2026

---

An autonomous cryptocurrency **paper-trading** research system. It discovers
trading strategies by empirical search, has an AI analyst adversarially review them,
backtests the survivors with realistic costs, and (on Binance Testnet — **no real money**)
executes and monitors the chosen strategy, restarting discovery if performance degrades.

> ⚠️ All trading is on Binance's *Testnet* sandbox.
> Nothing here places real orders or manages real funds.

---

## Purpose & target user

**Purpose.** Automate the loop a discretionary trader does by hand — hypothesise a
strategy, test it on history, sanity-check it, trade it, and notice when it stops
working — so the process is faster, repeatable, and free of "I really want this idea to
work" bias.

**Target user.** A technically literate hobbyist / student quant who can read Python and
understands basic trading concepts (RSI, moving averages, stop-loss), wants to *experiment*
with algorithmic strategies safely on a testnet, and values an explainable audit trail
(every AI decision is logged) over a black box.

**The need it meets.** Manual strategy research is slow and easy to fool yourself with
(overfitting, look-ahead bias, ignoring costs). This system bakes in walk-forward
validation, a cost model, an adversarial review step, and live degradation monitoring so
those traps are handled by the tooling rather than left to discipline.

---

## Why Python

Python was chosen because the techniques this project needs are all first-class in its
ecosystem:

| Need | What Python gives us |
|---|---|
| AI agents (strategy selection + analyst review) | Official Anthropic SDK (`anthropic`) |
| Live + historical market data | `ccxt` (unified exchange API, incl. Binance Testnet sandbox) |
| Backtesting on time-series + indicators | `pandas` / `numpy` for vectorised OHLCV work |
| A complex technique (market-regime detection) | `hmmlearn` / `scikit-learn` (Hidden Markov Model) |
| Persistence without a server | built-in `sqlite3` |
| A simple GUI with no extra install | built-in `tkinter` |

---

## Requirements

- **Python 3.9+**
- ~2 GB RAM (walk-forward backtests load multi-year OHLCV into memory)
- Internet access (Claude API + Binance Testnet)
- A Claude API key and Binance Testnet keys (see setup)

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in your keys (see below)
```

Fill these in `.env`:

| Variable | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | https://console.anthropic.com/ |
| `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_SECRET` | https://testnet.binance.vision/ |

All other settings have defaults in [config/settings.py](config/settings.py) — override
them via `.env` only if you need to.

## Running it

```bash
# Run the full autonomous system (Loop 1 discovery → Loop 2 execution)
python src/main.py

# Ingest a historical CSV (BlackBull MT4/MT5 export) into the database
python -m src.data.ingestor --csv data/BTCUSD_H1.csv --symbol BTC/USDT --timeframe 1h

# Database viewer GUI (browse knowledge base, strategies, summaries)
# Needs a Python with working Tcl/Tk 8.6 (see FAQ if you hit a Tk/macOS error)
# Must be the module form (-m) and run from the repo root — the DB path is relative
python3 -m src.gui.kb_gui
```

The GUI is a small single-window tkinter form for the knowledge base — pick a category,
type a finding, add it, and list existing findings. It contains no SQL itself: it calls
`write_finding()` and `get_all_findings()` in `src/data/knowledge_base.py`.

> The richer three-tab viewer (Knowledge Base / Strategies / Summary, backed by
> `KnowledgeBaseRepository` and `StrategyRepository`) is not built yet — the repository
> classes exist in `src/data/`, but `kb_gui.py` does not use them.

## Testing

```bash
# Run everything except the live Binance tests (no network/keys needed)
SKIP_LIVE_TESTS=1 pytest tests/ -v

# A single module's tests
SKIP_LIVE_TESTS=1 pytest tests/test_backtest.py -v
```

---

## Project layout

```
src/
├── data/        # CSV ingest, live CCXT feed, SQLite schema, knowledge base, memory layers
├── backtest/    # indicators, walk-forward engine, HMM regime detection
├── agents/      # Claude client, candidate generator, strategy + analyst + risk agents
├── exchange/    # paper-trading exchange + factory
├── monitor/     # background degradation monitor
├── gui/         # tkinter knowledge-base form (no SQL — calls the data layer)
├── loop1.py     # strategy discovery loop
├── loop2.py     # live execution loop
└── main.py      # entry point (init_db → Loop 1 → Loop 2 → restart on degradation)
config/settings.py   # single source of truth for all tunable parameters
prompts/             # versioned agent prompts (*_v1, *_v2)
tests/               # pytest suite (~113–122 tests)
claude_docs/         # architecture, module specs, decision log, issues
claude_docs/code/    # AUTO-GENERATED Obsidian code-graph notes (do not edit by hand)
```

For developer/architecture guidance see [CLAUDE.md](CLAUDE.md) and
[claude_docs/dashboard.md](claude_docs/dashboard.md).

---

## Obsidian docs graph

`claude_docs/` is an Obsidian vault (the vault root is the repo root). Open it in
Obsidian and use **Graph View** to navigate the docs and the code together.

Because Obsidian can't read wikilinks inside `.py` files, the code is mirrored
into the graph by an auto-generated companion note per source file under
`claude_docs/code/` — each note links to the real `.py`, to the notes of the
modules it imports (so the import graph shows up as edges), and up to its module
doc. **These notes are generated — never edit them by hand.**

```bash
# Regenerate the code-graph notes on demand
python -m scripts.sync_obsidian_graph

# Activate the git hook that regenerates them on every commit (run once per clone)
git config core.hooksPath scripts/hooks
```

With the hook active, adding, renaming, or deleting a `.py` file updates the
graph automatically on your next commit — it never goes stale.

---

## Help / FAQ

**The live Binance tests fail / hang.** They need real testnet keys and network. Skip them
with `SKIP_LIVE_TESTS=1 pytest tests/ -v`.

**"Database not initialised" error.** `init_db()` runs once at the top of `src/main.py`.
If you call a module directly, initialise the DB first (or just run via `main.py`).

**Where is the data stored?** A single SQLite file, `./trading_system.db` (override with
`DB_PATH`). It persists between runs.

**Does this trade real money?** No. `set_sandbox_mode(True)` forces Binance Testnet; all
trades are simulated/paper.

**Sharpe ratios look off.** Annualisation is timeframe-dependent — see the
`PERIODS_PER_YEAR` note in [CLAUDE.md](CLAUDE.md). Using the daily factor on hourly data
understates Sharpe ~8.8×.

**The GUI errors with a Tk / macOS version message (e.g. "macOS … or later required").**
Your Python is using Apple's old system Tcl/Tk 8.5, which recent macOS has removed. Run the
GUI under a Python that bundles **Tcl/Tk 8.6** instead (check with
`python3 -c "import tkinter; print(tkinter.TkVersion)"` — you want `8.6`). The database GUI
deliberately has no third-party dependencies, so it runs under a bare system/Homebrew
Python with no venv needed:
```bash
/usr/local/bin/python3 -m src.gui.kb_gui   # or any python3 reporting TkVersion 8.6
```
