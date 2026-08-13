"""
status_bus.py — stage-level progress events for the status GUI.

Why this exists
---------------
Loop 1 and Loop 2 had no way to report progress. There was no callback, queue,
observer or event hook anywhere in the pipeline, and no log file — the only
signals available to a UI were stderr lines and polling SQLite.

Polling is not enough for the stage a status display most needs to show. The
risk agent makes no LLM call and writes no database row (it is deterministic
arithmetic, by design — see src/agents/risk_agent.py), so "RISK AGENT: RUNNING"
is invisible to any database-polling observer. Only an explicit event can show
it.

Design constraints
------------------
1. **Silent by default.** The default sink is a no-op, so importing or running
   anything behaves exactly as it did before. Events only go anywhere once a
   caller opts in via `set_sink()`. This is what lets the pipeline be
   instrumented without changing test behaviour or CLI output.
2. **No dependencies.** stdlib only. This module is imported deep inside the
   pipeline and must never be the reason something fails to import.
3. **Never raises.** A monitoring channel that can crash the thing it monitors
   is worse than no monitoring. Every emit is wrapped.

Transport is one JSON object per line, which survives a pipe between two
different Python interpreters — necessary here because the GUI needs Tk 8.6
(/usr/local/bin/python3) while the engine needs the venv's packages.

Usage:
    from src.monitor.status_bus import stage, emit_event, set_sink

    with stage("loop1", "analyst_review", "Adversarial review (CP1)") as st:
        verdict = analyst.evaluate(...)
        st.result(verdict["verdict"].upper())
"""
import json
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field

# Event states. A stage is one of these at any moment.
RUNNING = "running"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"

_lock = threading.Lock()
_sink = None            # None == no-op. Set by main.py --emit-events.


@dataclass
class StageEvent:
    """One stage boundary in the pipeline."""
    loop: str                       # "loop1" | "loop2" | "system"
    stage: str                      # stable key, e.g. "analyst_review"
    state: str                      # running | done | failed | skipped
    detail: str = ""                # human-readable line for the activity feed
    result: str = ""                # verdict-ish label, e.g. "PASS", "REJECTED"
    attempt: int = 0                # Loop 1 retry number; 0 when not applicable
    max_attempts: int = 0
    ts: float = field(default_factory=time.time)

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @staticmethod
    def from_json(line: str) -> "StageEvent":
        return StageEvent(**json.loads(line))


def set_sink(sink) -> None:
    """
    Route events somewhere. `sink` is any callable taking one StageEvent, or a
    writable file object (a JSON line per event is written and flushed).
    Pass None to go silent again.
    """
    global _sink
    with _lock:
        if sink is None or callable(sink):
            _sink = sink
        else:
            _sink = _make_stream_sink(sink)


def _make_stream_sink(stream):
    def _write(event: StageEvent) -> None:
        stream.write(event.to_json() + "\n")
        stream.flush()      # unbuffered, or the GUI sees nothing until exit
    return _write


def use_stdout() -> None:
    """Emit to stdout as JSON lines — the transport the GUI subprocess reads."""
    set_sink(_make_stream_sink(sys.stdout))


def emit_event(event: StageEvent) -> None:
    """
    Publish one event. Never raises: a monitoring failure must not take down
    the trading pipeline it is monitoring.
    """
    sink = _sink
    if sink is None:
        return
    try:
        sink(event)
    except Exception:       # noqa: BLE001 - deliberately swallowing everything
        pass


def emit(loop: str, stage_key: str, state: str, detail: str = "",
         result: str = "", attempt: int = 0, max_attempts: int = 0) -> None:
    """Convenience wrapper for a one-off event with no enclosing block."""
    emit_event(StageEvent(loop=loop, stage=stage_key, state=state, detail=detail,
                          result=result, attempt=attempt,
                          max_attempts=max_attempts))


class _StageHandle:
    """Handed to the `with` body so it can attach a result or extra detail."""

    def __init__(self) -> None:
        self.result_label = ""
        self.detail_text = ""

    def result(self, label: str) -> None:
        """Record a verdict for this stage, e.g. 'PASS' or 'REJECTED'."""
        self.result_label = str(label)

    def detail(self, text: str) -> None:
        self.detail_text = str(text)


@contextmanager
def stage(loop: str, stage_key: str, detail: str = "", attempt: int = 0,
          max_attempts: int = 0):
    """
    Emit `running` on entry and `done` on clean exit, or `failed` with the
    exception text if the body raises. The exception is always re-raised —
    this observes, it never swallows pipeline errors.
    """
    handle = _StageHandle()
    emit(loop, stage_key, RUNNING, detail, attempt=attempt,
         max_attempts=max_attempts)
    try:
        yield handle
    except Exception as exc:
        emit(loop, stage_key, FAILED, f"{type(exc).__name__}: {exc}"[:300],
             attempt=attempt, max_attempts=max_attempts)
        raise
    else:
        emit(loop, stage_key, DONE, handle.detail_text or detail,
             result=handle.result_label, attempt=attempt,
             max_attempts=max_attempts)


# ── Stage catalogue ──────────────────────────────────────────────────────────
# The display order and labels for each loop. The GUI imports these so the
# pipeline list and the engine cannot drift apart.
#
# NOTE: these are the REAL stages of this system. A status display that showed
# an idealised pipeline instead would be misreporting what is running.
LOOP1_STAGES = [
    ("screen_pairs", "Market Data", "Screening the pair universe"),
    ("detect_regime", "Regime Detection", "Classifying the current market regime"),
    ("memory_retrieval", "Memory Retrieval", "Loading layered knowledge-base context"),
    ("candidate_generation", "Candidate Generation", "Building mechanism-diverse specs"),
    ("empirical_search", "Empirical Search", "Backtesting and ranking candidates"),
    ("strategy_selection", "Strategy Selection", "LLM selects the best survivor"),
    ("analyst_review", "Analyst Review", "Adversarial review (debate CP1)"),
    ("final_backtest", "Final Backtest", "Walk-forward calibration run"),
    ("save_strategy", "Save Strategy", "Persisting the validated strategy"),
]

LOOP2_STAGES = [
    ("fetch_candles", "Market Data", "Polling live candles"),
    ("signal_detection", "Signal Detection", "Evaluating entry conditions"),
    ("position_sizing", "Position Sizing", "ATR-based size calculation"),
    ("risk_review", "Risk Agent", "Evaluating position sizing and risk limits"),
    ("analyst_brief", "Analyst Brief", "Second opinion (debate CP2)"),
    ("place_trade", "Execution", "Placing the paper trade"),
    ("waiting", "Waiting", "Sleeping until the next candle close"),
]

STAGE_LABELS = {k: label for k, label, _ in LOOP1_STAGES + LOOP2_STAGES}
STAGE_DETAILS = {k: detail for k, _, detail in LOOP1_STAGES + LOOP2_STAGES}
