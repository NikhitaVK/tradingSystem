#!/bin/bash
# Double-click this in Finder (or run `./run_gui.command`) to open the database GUI.
# It uses a Python with working Tcl/Tk 8.6 — NOT the project venv (whose Tk 8.5 is broken on
# recent macOS). The GUI has no third-party dependencies, so no venv/pip is needed.
cd "$(dirname "$0")" || exit 1

# Prefer a python that reports Tk 8.6; fall back to /usr/local/bin/python3.
for PY in /usr/local/bin/python3 python3.12 python3.13 python3; do
    if command -v "$PY" >/dev/null 2>&1 && \
       "$PY" -c "import tkinter,sys; sys.exit(0 if tkinter.TkVersion>=8.6 else 1)" 2>/dev/null; then
        echo "Launching GUI with $PY ..."
        exec "$PY" -m src.gui.app
    fi
done

echo "No Python with Tcl/Tk 8.6 found. Install one (e.g. from python.org or 'brew install python-tk'),"
echo "then run:  <that python> -m src.gui.app"
read -r -p "Press Return to close."
