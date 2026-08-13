#!/bin/bash
# Trading System — status dashboard.
# Double-click this file in Finder to open it.
#
# Needs a Python with Tcl/Tk 8.6. The project venv is deliberately NOT used:
# its Python 3.9 ships Tk 8.5, which is broken on recent macOS. The engine
# itself still runs under the venv — the GUI launches it as a subprocess.
#
# Pass --replay to demo the dashboard with a scripted run (no API cost).

cd "$(dirname "$0")" || exit 1

FOUND=""
for PY in /usr/local/bin/python3 python3.13 python3.12 python3; do
    if command -v "$PY" >/dev/null 2>&1; then
        if "$PY" -c "import tkinter, sys; sys.exit(0 if tkinter.TkVersion >= 8.6 else 1)" 2>/dev/null; then
            FOUND="$PY"
            break
        fi
    fi
done

if [ -z "$FOUND" ]; then
    echo "No Python with Tcl/Tk 8.6 found."
    echo
    echo "The macOS system Python ships Tk 8.5, which cannot run this window."
    echo "Install a newer Python from https://www.python.org/downloads/"
    echo "then double-click this file again."
    read -r -p "Press Return to close."
    exit 1
fi

echo "Using $FOUND ($("$FOUND" -c 'import tkinter;print("Tk",tkinter.TkVersion)'))"
exec "$FOUND" -m src.gui.status_gui "$@"
