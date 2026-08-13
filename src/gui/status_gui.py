"""
status_gui.py — live status dashboard for the autonomous trading system.

⚠ AI-WRITTEN. NOT SUBMITTED AS AS91906 EVIDENCE.
   The assessed "Interface & events" program is src/gui/kb_gui.py, which is
   student-authored and is NOT touched by this file. This dashboard is a
   separate operational tool.

Purpose
-------
The system is autonomous, so there is almost nothing for a user to *do*. The
design problem is therefore visibility, not control: the user must never be
left watching a spinner wondering whether anything is happening. This window
answers three questions continuously —

    what stage is running right now?
    what already happened, and what did it decide?
    is the system alive?

Architecture
------------
The engine runs as a SUBPROCESS, not in this process. tkinter here needs
Tk 8.6 (/usr/local/bin/python3), while the engine needs the venv's packages —
and src/backtest/hmm_regime.py imports hmmlearn at module level with no
fallback, which the Tk-8.6 interpreter does not have. Running the engine in a
child process under .venv/bin/python sidesteps the split entirely, and makes
STOP a clean SIGTERM that main.py's existing shutdown handler already knows how
to absorb.

    child stdout (JSON lines) → reader thread → queue.Queue
                              → root.after(120ms) drain → widgets

THREADING RULE: the reader thread only ever appends to the queue. Every widget
mutation happens on the main thread inside the drain. tkinter is not
thread-safe and violating this produces crashes that are almost impossible to
reproduce.

Usage:
    /usr/local/bin/python3 -m src.gui.status_gui            # live run
    /usr/local/bin/python3 -m src.gui.status_gui --replay   # scripted demo, no API cost
"""
import argparse
import atexit
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.monitor.status_bus import (  # noqa: E402
    DONE, FAILED, LOOP1_STAGES, LOOP2_STAGES, RUNNING, SKIPPED,
)

# ── Appearance ───────────────────────────────────────────────────────────────
BG = "#ffffff"
FG = "#1a1a1a"
MUTED = "#8a8a8a"
RULE = "#d8d8d8"
PANEL = "#fafafa"

GREEN = "#2f9e44"
RED = "#d6336c"
BLUE = "#1c7ed6"
AMBER = "#f08c00"
GREY = "#adb5bd"

MONO = ("SF Mono", 11) if sys.platform == "darwin" else ("Consolas", 10)
MONO_SMALL = (MONO[0], MONO[1] - 1)
MONO_BOLD = (MONO[0], MONO[1], "bold")
TITLE_FONT = (MONO[0], MONO[1] + 3, "bold")

# Marker glyphs for pipeline state, matching the terminal-style mock.
MARKERS = {
    DONE: ("✓", GREEN),
    RUNNING: ("●", BLUE),
    FAILED: ("✕", RED),
    SKIPPED: ("–", MUTED),
    "pending": ("○", GREY),
}

# Results that mean "this went badly", so the label is coloured as a warning
# rather than as a success. Getting this wrong would show a rejection in green.
NEGATIVE_RESULTS = {
    "FAIL", "FAILED", "REJECTED", "ERROR", "EXHAUSTED", "DEGRADED",
    "no signal", "no funds", "fell back",
}
CAUTION_RESULTS = {"PROBATION", "ADJUSTED", "SKIPPED", "STOPPED"}


class StatusGUI:
    """The dashboard window. One instance per process."""

    def __init__(self, root: tk.Tk, replay: bool = False, speed: float = 1.0):
        self.root = root
        self.replay = replay
        self.speed = speed

        self.events: "queue.Queue[dict]" = queue.Queue()
        self.proc: subprocess.Popen | None = None
        self.reader: threading.Thread | None = None

        self.activity: list[str] = []
        self.stage_state: dict[str, dict] = {}
        self.active_loop = "loop1"
        self.attempt = 0
        self.max_attempts = 0
        self.running = False

        self.root.title("Trading System — Status")
        self.root.configure(bg=BG)
        self.root.geometry("720x860")
        self.root.minsize(620, 700)

        self._build()
        self._reset_pipeline("loop1")
        self._load_db_snapshot()
        self.root.after(120, self._drain)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Closing the window is not the only way this process can end. If the
        # GUI is killed or crashes, the engine child would otherwise keep
        # running unsupervised — placing real testnet trades with nothing
        # watching. These make the child's lifetime match the GUI's.
        atexit.register(self._kill_child)
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                pass          # not on the main thread; atexit still covers us

    # ── Layout ───────────────────────────────────────────────────────────
    def _build(self) -> None:
        outer = tk.Frame(self.root, bg=BG, padx=18, pady=14)
        outer.pack(fill="both", expand=True)

        # Header ----------------------------------------------------------
        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x")
        tk.Label(header, text="TRADING SYSTEM", font=TITLE_FONT, bg=BG,
                 fg=FG).pack(side="left")
        self.status_dot = tk.Label(header, text="●  IDLE", font=MONO_BOLD,
                                   bg=BG, fg=GREY)
        self.status_dot.pack(side="right")
        if self.replay:
            tk.Label(header, text="  REPLAY  ", font=MONO_SMALL, bg=AMBER,
                     fg="#ffffff").pack(side="right", padx=(0, 10))
        self._rule(outer, pady=(10, 12))

        # Current stage ---------------------------------------------------
        tk.Label(outer, text="CURRENT STAGE", font=MONO_SMALL, bg=BG,
                 fg=MUTED).pack(anchor="w")
        box = tk.Frame(outer, bg=PANEL, highlightbackground=RULE,
                       highlightthickness=1, padx=14, pady=10)
        box.pack(fill="x", pady=(6, 14))
        row = tk.Frame(box, bg=PANEL)
        row.pack(fill="x")
        self.cur_name = tk.Label(row, text="—", font=MONO_BOLD, bg=PANEL, fg=FG)
        self.cur_name.pack(side="left")
        self.cur_state = tk.Label(row, text="IDLE", font=MONO_SMALL, bg=PANEL,
                                  fg=GREY)
        self.cur_state.pack(side="right")
        self.cur_detail = tk.Label(box, text="Press START to begin.",
                                   font=MONO_SMALL, bg=PANEL, fg=MUTED,
                                   anchor="w", justify="left", wraplength=600)
        self.cur_detail.pack(fill="x", pady=(4, 0))

        # Pipeline --------------------------------------------------------
        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x")
        self.loop_label = tk.Label(head, text="LOOP 1", font=MONO_SMALL, bg=BG,
                                   fg=MUTED)
        self.loop_label.pack(side="left")
        self.attempt_label = tk.Label(head, text="", font=MONO_SMALL, bg=BG,
                                      fg=AMBER)
        self.attempt_label.pack(side="right")

        self.pipeline_frame = tk.Frame(outer, bg=BG)
        self.pipeline_frame.pack(fill="x", pady=(8, 14))
        self.rows: dict[str, tuple] = {}

        self._rule(outer, pady=(0, 12))

        # Activity --------------------------------------------------------
        act_head = tk.Frame(outer, bg=BG)
        act_head.pack(fill="x")
        tk.Label(act_head, text="RECENT ACTIVITY", font=MONO_SMALL, bg=BG,
                 fg=MUTED).pack(side="left")
        tk.Button(act_head, text="View all", font=MONO_SMALL,
                  command=self._show_all_activity, relief="flat",
                  highlightthickness=0, bg=BG, fg=BLUE,
                  activebackground=BG, cursor="hand2").pack(side="right")

        act_wrap = tk.Frame(outer, bg=BG)
        act_wrap.pack(fill="both", expand=True, pady=(6, 12))
        scroll = tk.Scrollbar(act_wrap)
        scroll.pack(side="right", fill="y")
        self.activity_box = tk.Text(act_wrap, height=8, font=MONO_SMALL,
                                    bg=PANEL, fg=FG, relief="flat",
                                    highlightbackground=RULE,
                                    highlightthickness=1, padx=10, pady=8,
                                    yscrollcommand=scroll.set, wrap="word")
        self.activity_box.pack(side="left", fill="both", expand=True)
        self.activity_box.configure(state="disabled")
        scroll.config(command=self.activity_box.yview)

        # Footer ----------------------------------------------------------
        footer = tk.Frame(outer, bg=BG)
        footer.pack(fill="x")
        self.hint = tk.Label(footer, text="", font=MONO_SMALL, bg=BG, fg=MUTED)
        self.hint.pack(side="left")
        self.stop_btn = tk.Button(footer, text="STOP", font=MONO_BOLD,
                                  command=self.stop, state="disabled",
                                  cursor="hand2")
        self.stop_btn.pack(side="right", padx=(8, 0))
        self.start_btn = tk.Button(footer, text="START", font=MONO_BOLD,
                                   command=self.start, cursor="hand2")
        self.start_btn.pack(side="right")

    def _rule(self, parent, pady=(8, 8)) -> None:
        tk.Frame(parent, bg=RULE, height=1).pack(fill="x", pady=pady)

    def _reset_pipeline(self, loop: str) -> None:
        """Rebuild the stage list for whichever loop is active."""
        for child in self.pipeline_frame.winfo_children():
            child.destroy()
        self.rows.clear()
        self.stage_state.clear()
        self.active_loop = loop
        self.loop_label.config(text="LOOP 1" if loop == "loop1" else "LOOP 2")

        stages = LOOP1_STAGES if loop == "loop1" else LOOP2_STAGES
        for key, label, _detail in stages:
            row = tk.Frame(self.pipeline_frame, bg=BG)
            row.pack(fill="x", pady=1)
            marker = tk.Label(row, text="○", font=MONO, bg=BG, fg=GREY, width=2)
            marker.pack(side="left")
            name = tk.Label(row, text=label, font=MONO, bg=BG, fg=MUTED)
            name.pack(side="left")
            result = tk.Label(row, text="", font=MONO_SMALL, bg=BG, fg=MUTED)
            result.pack(side="right")
            self.rows[key] = (marker, name, result)
            self.stage_state[key] = {"state": "pending"}

    # ── Engine process ───────────────────────────────────────────────────
    def start(self) -> None:
        if self.running:
            return
        cmd = self._build_command()
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1,
                start_new_session=True,      # so STOP kills the whole group
            )
        except OSError as exc:
            self._set_status("ERROR", RED)
            self._log(f"could not start engine: {exc}")
            return

        self.running = True
        self._reset_pipeline("loop1")
        self._set_status("RUNNING", GREEN)
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.hint.config(text="replay — no API calls" if self.replay
                         else "live run — Binance Testnet")
        self._log("system started")

        self.reader = threading.Thread(target=self._read_stream, daemon=True)
        self.reader.start()

    def _build_command(self) -> list:
        if self.replay:
            return [sys.executable, "-u", "-m", "scripts.demo_status_events",
                    "--speed", str(self.speed)]
        # The engine needs the venv interpreter; this GUI is running under a
        # different one (Tk 8.6), so name it explicitly rather than reusing
        # sys.executable.
        venv_py = ROOT / ".venv" / "bin" / "python"
        engine_py = str(venv_py) if venv_py.exists() else sys.executable
        return [engine_py, "-u", "-m", "src.main", "--emit-events"]

    def _read_stream(self) -> None:
        """
        Reader thread. Appends to the queue and NEVER touches a widget.
        """
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    self.events.put(json.loads(line))
                except json.JSONDecodeError:
                    continue          # not an event line; ignore quietly
        except Exception:             # noqa: BLE001 - pipe closed mid-read
            pass
        finally:
            self.events.put({"__eof__": True})

    def stop(self) -> None:
        """SIGTERM the engine — main.py already handles it gracefully."""
        if not self.proc or self.proc.poll() is not None:
            self._finish("STOPPED", GREY)
            return
        self._log("stop requested — shutting down cleanly")
        self.stop_btn.config(state="disabled")
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            self.proc.terminate()

    # ── Event drain (main thread only) ───────────────────────────────────
    def _drain(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event.get("__eof__"):
                    self._on_eof()
                else:
                    self._apply(event)
        except queue.Empty:
            pass
        self.root.after(120, self._drain)

    def _apply(self, e: dict) -> None:
        loop = e.get("loop", "")
        key = e.get("stage", "")
        state = e.get("state", "")
        detail = e.get("detail", "")
        result = e.get("result", "")

        # Switch the visible pipeline when the other loop takes over.
        if loop in ("loop1", "loop2") and loop != self.active_loop:
            if key in dict((k, 1) for k, _, _ in
                           (LOOP1_STAGES if loop == "loop1" else LOOP2_STAGES)):
                self._reset_pipeline(loop)

        if e.get("attempt"):
            self.attempt = e["attempt"]
            self.max_attempts = e.get("max_attempts", 0)
            self.attempt_label.config(
                text=f"Attempt {self.attempt}/{self.max_attempts}"
                if self.max_attempts else f"Attempt {self.attempt}")
            # A retry restarts the per-attempt stages, so clear their marks.
            if key == "attempt":
                for k in ("candidate_generation", "empirical_search",
                          "strategy_selection", "analyst_review"):
                    self._mark(k, "pending", "")

        if key in self.rows:
            self._mark(key, state, result)

        if state == RUNNING:
            self._set_current(key, detail, "RUNNING", BLUE)
        elif state == FAILED:
            self._set_current(key, detail, "FAILED", RED)
        elif state == DONE and result:
            self._set_current(key, detail, result, self._result_colour(result))

        if detail and state != RUNNING:
            self._log(detail if not result else f"{detail}  [{result}]")
        elif state == RUNNING and detail:
            self._log(detail)

        if key == "stopped":
            self._finish("STOPPED", GREY)

    def _mark(self, key: str, state: str, result: str) -> None:
        if key not in self.rows:
            return
        marker, name, res = self.rows[key]
        glyph, colour = MARKERS.get(state, MARKERS["pending"])
        marker.config(text=glyph, fg=colour)
        name.config(fg=FG if state in (RUNNING, DONE, FAILED) else MUTED)
        if result:
            res.config(text=result, fg=self._result_colour(result))
        elif state == "pending":
            res.config(text="")
        self.stage_state[key] = {"state": state}

    @staticmethod
    def _result_colour(result: str) -> str:
        if result in NEGATIVE_RESULTS:
            return RED
        if result in CAUTION_RESULTS:
            return AMBER
        return GREEN

    def _set_current(self, key: str, detail: str, state_text: str,
                     colour: str) -> None:
        _m, name, _r = self.rows.get(key, (None, None, None))
        label = name.cget("text") if name else key.replace("_", " ").title()
        self.cur_name.config(text=label.upper())
        self.cur_state.config(text=state_text, fg=colour)
        self.cur_detail.config(text=detail or "—")

    def _set_status(self, text: str, colour: str) -> None:
        self.status_dot.config(text=f"●  {text}", fg=colour)

    def _log(self, message: str) -> None:
        line = f"{time.strftime('%H:%M:%S')}  {message}"
        self.activity.append(line)
        self.activity_box.configure(state="normal")
        self.activity_box.insert("end", line + "\n")
        # Cap the widget, not the history — "View all" still shows everything.
        if len(self.activity) > 200:
            self.activity_box.delete("1.0", "2.0")
        self.activity_box.see("end")
        self.activity_box.configure(state="disabled")

    def _on_eof(self) -> None:
        """Child's stdout closed. Distinguish a clean stop from a crash."""
        code = self.proc.poll() if self.proc else None
        if code in (0, -signal.SIGTERM, signal.SIGTERM):
            self._finish("STOPPED", GREY)
        else:
            self._log(f"engine exited unexpectedly (code {code})")
            self._finish("ERROR", RED)

    def _finish(self, text: str, colour: str) -> None:
        if not self.running:
            return
        self.running = False
        self._set_status(text, colour)
        self.cur_state.config(text=text, fg=colour)
        self.start_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.hint.config(text="")

    def _kill_child(self) -> None:
        """Terminate the engine subprocess if it is still alive. Idempotent."""
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            self.proc.wait(timeout=5)
        except Exception:          # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:      # noqa: BLE001
                pass

    def _on_signal(self, _signum, _frame) -> None:
        self._kill_child()
        sys.exit(0)

    def _on_close(self) -> None:
        """Never leave an orphaned engine behind."""
        self._kill_child()
        self.root.destroy()

    # ── Startup snapshot ─────────────────────────────────────────────────
    def _load_db_snapshot(self) -> None:
        """
        Show the last known state before anything runs, so the window is never
        blank on launch. Read-only and best-effort: a missing DB is normal on a
        first run and must not stop the GUI opening.
        """
        try:
            import sqlite3
            from config.settings import DB_PATH

            if not Path(DB_PATH).exists():
                self._log("no database yet — press START to begin")
                return
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, name, status FROM strategies ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                self._log(f"last strategy: {row['name']} "
                          f"(id={row['id']}, {row['status']})")
            counts = conn.execute(
                "SELECT outcome, COUNT(*) n FROM trades GROUP BY outcome"
            ).fetchall()
            if counts:
                summary = ", ".join(f"{r['n']} {r['outcome']}" for r in counts)
                self._log(f"trades on record: {summary}")
            conn.close()
        except Exception as exc:      # noqa: BLE001
            self._log(f"could not read database snapshot ({type(exc).__name__})")

    def _show_all_activity(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Activity log")
        win.geometry("760x560")
        win.configure(bg=BG)
        scroll = tk.Scrollbar(win)
        scroll.pack(side="right", fill="y")
        text = tk.Text(win, font=MONO_SMALL, bg=PANEL, fg=FG, relief="flat",
                       padx=12, pady=10, yscrollcommand=scroll.set, wrap="word")
        text.pack(side="left", fill="both", expand=True)
        text.insert("1.0", "\n".join(self.activity) or "(nothing yet)")
        text.configure(state="disabled")
        scroll.config(command=text.yview)


def main() -> None:
    ap = argparse.ArgumentParser(description="Trading system status dashboard")
    ap.add_argument("--replay", action="store_true",
                    help="run the scripted demo instead of the real engine "
                         "(no API cost, no exchange calls)")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="replay playback multiplier")
    ap.add_argument("--autostart", action="store_true",
                    help="begin immediately instead of waiting for START")
    args = ap.parse_args()

    root = tk.Tk()
    gui = StatusGUI(root, replay=args.replay, speed=args.speed)
    if args.autostart:
        root.after(400, gui.start)
    root.mainloop()


if __name__ == "__main__":
    main()
