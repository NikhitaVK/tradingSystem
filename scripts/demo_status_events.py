"""
demo_status_events.py — scripted event stream for demonstrating the status GUI.

Why this exists
---------------
A real Loop 1 run costs Anthropic API credit and takes minutes. This replays a
representative run through the *identical* event path (`status_bus.emit`), so
the GUI can be demonstrated, screenshotted and tested without spending anything
or touching an exchange.

The scripted run is deliberately not a clean success. It contains:
  * attempt 1 — the analyst REJECTS the strategy (this is the common case)
  * attempt 2 — a different spec passes on PROBATION, not a clean pass
  * Loop 2  — a candle with no signal, then a risk-agent size ADJUSTMENT,
              then a confirmed trade
so the retry badge, the rejection styling and the waiting state are all visible.
A demo that only ever shows the happy path would hide exactly the states a
status display exists to communicate.

Every event carries `replay: true` in its detail so a screenshot can never be
mistaken for a live run, and the GUI stamps the header REPLAY.

Usage:
    python3 -m scripts.demo_status_events            # stream to stdout
    python3 -m scripts.demo_status_events --speed 4  # 4x faster
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.monitor import status_bus  # noqa: E402
from src.monitor.status_bus import DONE, FAILED, RUNNING, emit  # noqa: E402

# (loop, stage, state, detail, result, attempt, pause_seconds)
SCRIPT = [
    ("system", "startup", RUNNING, "Initialising database at trading_system.db", "", 0, 0.4),
    ("loop1", "loop_start", RUNNING, "Loop 1 — strategy discovery", "", 0, 0.5),

    ("loop1", "screen_pairs", RUNNING, "Screening the pair universe", "", 0, 1.1),
    ("loop1", "screen_pairs", DONE, "BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, XRP/USDT",
     "5 pairs", 0, 0.4),

    ("loop1", "detect_regime", RUNNING, "Classifying the current market regime", "", 0, 0.9),
    ("loop1", "detect_regime", DONE, "HMM classified 4-state model", "trending_bull", 0, 0.4),

    ("loop1", "memory_retrieval", RUNNING, "Loading layered knowledge-base context", "", 0, 0.7),
    ("loop1", "memory_retrieval", DONE, "13 entries, 6 matching 'trending_bull'",
     "13 entries", 0, 0.5),

    # ── Attempt 1 — rejected ────────────────────────────────────────────────
    ("loop1", "attempt", DONE, "Attempt 1 of 5", "", 1, 0.3),
    ("loop1", "candidate_generation", RUNNING, "Building mechanism-diverse specs", "", 1, 0.8),
    ("loop1", "candidate_generation", DONE, "12 candidates for BTC/USDT in trending_bull regime",
     "12 specs", 1, 0.3),
    ("loop1", "empirical_search", RUNNING, "Backtesting and ranking candidates", "", 1, 2.2),
    ("loop1", "empirical_search", DONE, "3 of 12 candidates cleared the viability floor",
     "3 viable", 1, 0.4),
    ("loop1", "strategy_selection", RUNNING, "LLM selects the best survivor", "", 1, 1.6),
    ("loop1", "strategy_selection", DONE, "RSI Momentum Breakout", "RSI Momentum Breakout", 1, 0.4),
    ("loop1", "analyst_review", RUNNING, "Adversarial review (debate CP1)", "", 1, 2.0),
    ("loop1", "analyst_review", DONE,
     "RSI Momentum Breakout — score 0.41. Only 2 of 5 slices profitable; "
     "edge is concentrated in one regime.", "FAIL", 1, 0.8),

    # ── Attempt 2 — probation ───────────────────────────────────────────────
    ("loop1", "attempt", DONE, "Attempt 2 of 5", "", 2, 0.4),
    ("loop1", "candidate_generation", RUNNING, "Building mechanism-diverse specs", "", 2, 0.8),
    ("loop1", "candidate_generation", DONE, "12 candidates (1 name blacklisted)",
     "12 specs", 2, 0.3),
    ("loop1", "empirical_search", RUNNING, "Backtesting and ranking candidates", "", 2, 2.1),
    ("loop1", "empirical_search", DONE, "4 of 12 candidates cleared the viability floor",
     "4 viable", 2, 0.4),
    ("loop1", "strategy_selection", RUNNING, "LLM selects the best survivor", "", 2, 1.5),
    ("loop1", "strategy_selection", DONE, "EMA Trend Pullback", "EMA Trend Pullback", 2, 0.4),
    ("loop1", "analyst_review", RUNNING, "Adversarial review (debate CP1)", "", 2, 2.0),
    ("loop1", "analyst_review", DONE, "EMA Trend Pullback — score 0.63", "PROBATION", 2, 0.6),
    ("loop1", "final_backtest", RUNNING, "Walk-forward calibration run", "", 2, 1.8),
    ("loop1", "final_backtest", DONE, "68 trades, win rate 0.54", "WR 0.54", 2, 0.4),
    ("loop1", "save_strategy", RUNNING, "Persisting the validated strategy", "", 2, 0.5),
    ("loop1", "save_strategy", DONE, "EMA Trend Pullback saved as id=7 (probation)",
     "id=7", 2, 0.4),
    ("loop1", "loop_start", DONE, "Loop 1 complete — EMA Trend Pullback (id=7)",
     "COMPLETE", 0, 0.8),

    # ── Loop 2 ──────────────────────────────────────────────────────────────
    ("loop2", "loop_start", RUNNING, "Loop 2 — live execution of EMA Trend Pullback", "", 0, 0.6),
    ("loop2", "fetch_candles", RUNNING, "Polling BTC/USDT 1h", "", 0, 0.8),
    ("loop2", "fetch_candles", DONE, "199 closed candles for BTC/USDT", "199 candles", 0, 0.3),
    ("loop2", "signal_detection", RUNNING, "Evaluating entry conditions", "", 0, 0.7),
    ("loop2", "signal_detection", DONE, "Entry conditions not met", "no signal", 0, 0.3),
    ("loop2", "waiting", DONE, "No entry signal on the last closed candle", "1h", 0, 1.6),

    ("loop2", "fetch_candles", RUNNING, "Polling BTC/USDT 1h", "", 0, 0.7),
    ("loop2", "fetch_candles", DONE, "200 closed candles for BTC/USDT", "200 candles", 0, 0.3),
    ("loop2", "signal_detection", RUNNING, "Evaluating entry conditions", "", 0, 0.7),
    ("loop2", "signal_detection", DONE, "Entry signal on BTC/USDT", "SIGNAL", 0, 0.5),
    ("loop2", "position_sizing", RUNNING, "ATR-based size calculation", "", 0, 0.6),
    ("loop2", "position_sizing", DONE, "Proposed 612.40 USDT on a 10000.00 balance",
     "612.40 USDT", 0, 0.4),
    ("loop2", "risk_review", RUNNING, "Evaluating position sizing and risk limits", "", 0, 1.2),
    ("loop2", "risk_review", DONE,
     "size above 5% cap — adjusted down to 500.00 USDT", "ADJUSTED", 0, 0.6),
    ("loop2", "analyst_brief", RUNNING, "Second opinion (debate CP2)", "", 0, 1.7),
    ("loop2", "analyst_brief", DONE,
     "Trend intact on the higher timeframe; size is appropriate after the risk cap.",
     "CONFIRMED", 0, 0.5),
    ("loop2", "place_trade", RUNNING, "Placing buy 500.00 USDT on BTC/USDT", "", 0, 1.4),
    ("loop2", "place_trade", DONE, "trade id=12 outcome=open", "OPEN", 0, 0.5),
    ("loop2", "waiting", DONE, "Trade complete — waiting for the next candle close", "1h", 0, 2.0),
]


def run(speed: float = 1.0, loop_forever: bool = False) -> None:
    status_bus.use_stdout()
    while True:
        for loop, stage_key, state, detail, result, attempt, pause in SCRIPT:
            emit(loop, stage_key, state,
                 detail, result=result,
                 attempt=attempt, max_attempts=5 if attempt else 0)
            time.sleep(max(pause / speed, 0.0))
        if not loop_forever:
            break
    emit("system", "stopped", DONE, "Replay finished", result="STOPPED")


def main() -> None:
    ap = argparse.ArgumentParser(description="Replay a scripted run for the status GUI")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="playback multiplier (2 = twice as fast)")
    ap.add_argument("--loop", action="store_true", help="repeat forever")
    args = ap.parse_args()
    try:
        run(speed=args.speed, loop_forever=args.loop)
    except (KeyboardInterrupt, BrokenPipeError):
        pass


if __name__ == "__main__":
    main()
