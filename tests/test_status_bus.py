"""
Isolation tests for src/monitor/status_bus.py.

Headless — imports no tkinter and starts no GUI.

The most important test here is `test_default_sink_is_silent`: the whole point
of the no-op default is that instrumenting the pipeline changes nothing for
every existing caller. If that regresses, `python src/main.py` starts printing
JSON at users and the other 131 tests are no longer testing the shipped path.
"""
import io
import json

import pytest

from src.monitor import status_bus
from src.monitor.status_bus import (
    DONE, FAILED, RUNNING, LOOP1_STAGES, LOOP2_STAGES, StageEvent, emit, stage,
)


@pytest.fixture(autouse=True)
def _reset_sink():
    """Never leak a sink between tests — it is module-global state."""
    status_bus.set_sink(None)
    yield
    status_bus.set_sink(None)


@pytest.fixture
def captured():
    """Collect emitted events in a list."""
    events = []
    status_bus.set_sink(events.append)
    return events


# ── The silent default ───────────────────────────────────────────────────────

def test_default_sink_is_silent(capsys):
    """With no sink configured, nothing is emitted and nothing is printed."""
    emit("loop1", "screen_pairs", RUNNING, "should go nowhere")
    with stage("loop1", "detect_regime"):
        pass
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == ""


def test_sink_can_be_turned_off_again(captured):
    emit("loop1", "screen_pairs", RUNNING)
    assert len(captured) == 1
    status_bus.set_sink(None)
    emit("loop1", "detect_regime", RUNNING)
    assert len(captured) == 1, "events kept flowing after the sink was removed"


# ── Serialisation ────────────────────────────────────────────────────────────

def test_jsonl_round_trip():
    original = StageEvent(loop="loop1", stage="analyst_review", state=DONE,
                          detail="verdict reached", result="PASS",
                          attempt=2, max_attempts=5)
    restored = StageEvent.from_json(original.to_json())
    assert restored == original


def test_stream_sink_writes_one_flushed_json_line_per_event():
    stream = io.StringIO()
    status_bus.set_sink(stream)
    emit("loop1", "screen_pairs", RUNNING, "a")
    emit("loop1", "screen_pairs", DONE, "b", result="5 pairs")

    lines = stream.getvalue().strip().split("\n")
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["state"] == RUNNING
    assert second["state"] == DONE
    assert second["result"] == "5 pairs"


def test_events_keep_their_order():
    seen = []
    status_bus.set_sink(lambda e: seen.append(e.stage))
    for key, _, _ in LOOP1_STAGES:
        emit("loop1", key, DONE)
    assert seen == [k for k, _, _ in LOOP1_STAGES]


# ── The stage context manager ────────────────────────────────────────────────

def test_stage_emits_running_then_done(captured):
    with stage("loop1", "empirical_search", "ranking candidates"):
        pass
    assert [e.state for e in captured] == [RUNNING, DONE]
    assert all(e.stage == "empirical_search" for e in captured)


def test_stage_records_a_result_label(captured):
    with stage("loop1", "analyst_review") as st:
        st.result("probation")
    assert captured[-1].result == "probation"


def test_stage_emits_failed_and_reraises(captured):
    with pytest.raises(ValueError, match="boom"):
        with stage("loop2", "place_trade"):
            raise ValueError("boom")

    assert [e.state for e in captured] == [RUNNING, FAILED]
    assert "ValueError: boom" in captured[-1].detail


def test_stage_carries_the_attempt_counter(captured):
    with stage("loop1", "strategy_selection", attempt=3, max_attempts=5):
        pass
    assert all(e.attempt == 3 and e.max_attempts == 5 for e in captured)


# ── Failure isolation ────────────────────────────────────────────────────────

def test_a_broken_sink_cannot_break_the_pipeline():
    """Monitoring must never take down the thing it monitors."""
    def exploding_sink(_event):
        raise RuntimeError("sink is broken")

    status_bus.set_sink(exploding_sink)
    emit("loop1", "screen_pairs", RUNNING)          # must not raise

    with stage("loop1", "detect_regime"):           # must not raise
        pass


def test_pipeline_exception_still_propagates_through_a_broken_sink():
    """The sink swallows its own errors, never the body's."""
    status_bus.set_sink(lambda _e: (_ for _ in ()).throw(RuntimeError("nope")))
    with pytest.raises(KeyError):
        with stage("loop1", "save_strategy"):
            raise KeyError("real pipeline error")


# ── Stage catalogue ──────────────────────────────────────────────────────────

def test_stage_catalogue_keys_are_unique_and_labelled():
    keys = [k for k, _, _ in LOOP1_STAGES + LOOP2_STAGES]
    assert len(keys) == len(set(keys)), "duplicate stage key would collide in the GUI"
    for key, label, detail in LOOP1_STAGES + LOOP2_STAGES:
        assert key and label and detail
