# Status GUI — specifications

**Status**: Built
**Files**: `src/gui/status_gui.py`, `src/monitor/status_bus.py`, `scripts/demo_status_events.py`
**Test**: `tests/test_status_bus.py` (12 tests — the bus only; tkinter is not tested)
**Depends on**: Loop 1, Loop 2, `main.py` (all instrumented to emit events)

> **AI-written. Not AS91906 evidence.** The assessed *Interface & events*
> program is `src/gui/kb_gui.py`, which is student-authored and untouched by
> this work.

---

## Purpose

The system is autonomous, so there is nothing for a user to operate. The problem
this solves is **visibility of system status**: the user must never watch a
spinner wondering whether anything is happening.

The window answers three questions continuously:

1. What stage is running right now?
2. What already happened, and what did it decide?
3. Is the system alive?

---

## Requirements

| # | Requirement | How it is met |
|---|---|---|
| 1 | Show the stage currently running | CURRENT STAGE panel, updated on every event |
| 2 | Show the whole pipeline and progress through it | Stage list with ✓ done / ● running / ○ pending |
| 3 | Show what each stage *decided*, not just that it finished | Right-aligned result label (`PASS`, `REJECTED`, `PROBATION`) |
| 4 | Distinguish good outcomes from bad at a glance | Green = pass, amber = probation/adjusted, red = fail/rejected |
| 5 | Show history, not just the present | Timestamped activity feed + "View all" window |
| 6 | Never look frozen while idle | Waiting states say *why* they are waiting |
| 7 | Show retries | `Attempt 2/5` badge; per-attempt stages reset on retry |
| 8 | Let the user stop it | STOP → SIGTERM → existing graceful shutdown |
| 9 | Be demonstrable without spending money | `--replay` mode |

---

## What it displays

```
┌──────────────────────────────────────────────┐
│ TRADING SYSTEM                    ●  RUNNING │   status: IDLE/RUNNING/STOPPED/ERROR
├──────────────────────────────────────────────┤
│ CURRENT STAGE                                │
│  ┌────────────────────────────────────────┐  │
│  │ RISK AGENT                    RUNNING  │  │   stage name + state
│  │ Evaluating position sizing and risk... │  │   one-line detail
│  └────────────────────────────────────────┘  │
│                                              │
│ LOOP 1                          Attempt 2/5  │   which loop + retry badge
│  ✓ Market Data                     5 pairs   │
│  ✓ Regime Detection          trending_bull   │
│  ✓ Memory Retrieval             13 entries   │
│  ✓ Candidate Generation           12 specs   │
│  ✓ Empirical Search              3 viable    │
│  ● Strategy Selection                        │
│  ○ Analyst Review                            │
│  ○ Final Backtest                            │
│  ○ Save Strategy                             │
├──────────────────────────────────────────────┤
│ RECENT ACTIVITY                    [View all]│
│ 12:42:22  3 of 12 candidates cleared...      │
│ 12:42:30  EMA Trend Pullback  [PROBATION]    │
├──────────────────────────────────────────────┤
│                            [START]  [STOP]   │
└──────────────────────────────────────────────┘
```

Loop 2 replaces the stage list when it starts: Market Data → Signal Detection →
Position Sizing → Risk Agent → Analyst Brief → Execution → Waiting.

---

## How it works

```
GUI process (/usr/local/bin/python3, Tk 8.6)
   └─ subprocess: .venv/bin/python -m src.main --emit-events
          each stage boundary → one JSON line on stdout
   └─ reader thread → queue.Queue
   └─ root.after(120ms) → drain queue → update widgets
   └─ [STOP] → SIGTERM → main.py marks open trades 'interrupted'
```

**Two processes, on purpose.** The GUI needs Tk 8.6, which only
`/usr/local/bin/python3` has. The engine needs the venv's packages —
`hmm_regime.py` imports `hmmlearn` at module level with no fallback, and the
Tk 8.6 interpreter does not have it. Neither interpreter can do both jobs.

**Threading rule.** The reader thread only appends to the queue. All widget
updates happen on the main thread in the drain. tkinter is not thread-safe.

### Event format

One JSON object per line:

```json
{"loop": "loop1", "stage": "analyst_review", "state": "done",
 "detail": "EMA Trend Pullback — score 0.63", "result": "PROBATION",
 "attempt": 2, "max_attempts": 5, "ts": 1786609526.9}
```

`state` is one of `running` | `done` | `failed` | `skipped`.

### Why an event bus rather than polling the database

The risk agent is deterministic arithmetic. It makes no LLM call and writes no
row, so it is **invisible** to anything polling SQLite — yet it is the stage the
design most needs to show. Only an explicit event can surface it.

---

## Design rules

- **Silent by default.** `status_bus`'s default sink is a no-op, so the pipeline
  behaves identically unless `--emit-events` is passed. This is what allowed the
  loops to be instrumented without changing any existing behaviour or test.
- **Monitoring never breaks the thing it monitors.** Every emit is wrapped;
  a broken sink cannot raise into the trading pipeline. Pipeline exceptions
  still propagate normally.
- **The display must not lie.** Stages are the system's real stages. The
  original mock listed "Risk Review" under Loop 1, but it belongs to Loop 2;
  showing it otherwise would misreport the system.

---

## How to run

```bash
# Demo — scripted run, no API cost, no exchange calls
/usr/local/bin/python3 -m src.gui.status_gui --replay --autostart

# Live — spends API credit, places testnet trades
/usr/local/bin/python3 -m src.gui.status_gui

# Or double-click, which finds a Tk 8.6 interpreter itself
run_status_gui.command
```

Running it under the venv fails deliberately with an explanation: that Python is
3.9 with Tk 8.5, which calls `abort()` on macOS 16+.

---

## Replay mode

`scripts/demo_status_events.py` emits 51 scripted events through the same
`status_bus.emit` path the real engine uses, so the GUI cannot tell the
difference. The scripted run contains a **rejection**, a **retry**, a
**PROBATION** verdict and a **risk-agent size adjustment** — a happy-path demo
would hide exactly the states this display exists to communicate.

Replay stamps an amber `REPLAY` badge in the header so a screenshot can never be
mistaken for a live run.

---

## Limits

- **The live path has never been run end to end** — no API credit. The plumbing
  is verified (events serialise, the GUI applies them, STOP terminates the child
  cleanly) but no real Loop 1 has driven this window.
- **No automated GUI test.** `tests/test_status_bus.py` covers the event bus;
  the tkinter layer is verified by injecting events and reading widget state
  back manually.
- **Visual layout is unverified** — screen capture was unavailable in the
  environment where it was built.
- Status only. No charts, no history browsing, no configuration.
- One strategy at a time, matching the engine.
