#!/bin/bash
# TRIAL 1 — API cost ablation ladder.
#
# Double-click this in Finder (or run ./run_trial1_api_cost.command) to run
# the trial and regenerate its dot plot.
#
# Harness: scripts/trials/api_cost_ladder.py
# Outputs: trials_out/api_cost_ladder_summary.json
#          trials_out/api_cost_ladder_dotplot.svg (+ .png)
#
# Read-only on the database. No API calls, no cost. Safe to re-run.
cd "$(dirname "$0")" || exit 1

echo "Trial 1 — API cost ablation ladder"
echo

python3 -m scripts.trials.api_cost_ladder || {
    echo
    echo "Trial failed. Check that trading_system.db exists in this folder."
    read -r -p "Press Return to close."
    exit 1
}

# Rasterise the SVG to PNG (macOS built-in; no extra install needed).
if command -v qlmanage >/dev/null 2>&1; then
    rm -f trials_out/api_cost_ladder_dotplot.svg.png
    qlmanage -t -s 1500 -o trials_out trials_out/api_cost_ladder_dotplot.svg >/dev/null 2>&1 \
        && echo && echo "Dot plot: trials_out/api_cost_ladder_dotplot.svg.png"
fi

echo
read -r -p "Done. Press Return to close."
