#!/bin/bash
# TRIAL 1 — Different AI APIs: cost vs success.
#
# Double-click this in Finder (or run ./run_trial1_providers.command).
#
# Harness: scripts/trials/provider_comparison_trial.py
# Outputs: trials_out/provider_comparison_matrix.csv
#          trials_out/provider_comparison_summary.json
#          trials_out/provider_comparison_dotplot.svg (+ .png)
#
# Costs nothing. The Claude arm is read from the database (already measured by
# trial 2); the local Ollama arms run on this machine. Hosted free arms (Gemini,
# DeepSeek) only run if their keys are in .env — otherwise they are reported as
# NOT RUN rather than silently skipped.
cd "$(dirname "$0")" || exit 1

# Load .env so GEMINI_API_KEY / DEEPSEEK_API_KEY are visible if present.
if [ -f .env ]; then set -a; . ./.env; set +a; fi

echo "Trial 1 — different AI APIs: cost vs success"
echo

PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python

"$PY" -m scripts.trials.provider_comparison_trial "$@" || {
    echo
    echo "Trial failed. Is the Ollama app running? (needed for the local arms)"
    read -r -p "Press Return to close."
    exit 1
}

if command -v qlmanage >/dev/null 2>&1; then
    rm -f trials_out/provider_comparison_dotplot.svg.png
    qlmanage -t -s 1500 -o trials_out trials_out/provider_comparison_dotplot.svg >/dev/null 2>&1 \
        && echo && echo "Dot plot: trials_out/provider_comparison_dotplot.svg.png"
fi

echo
read -r -p "Done. Press Return to close."
