#!/bin/bash
# Trial 2 (rebuild) — prompt engineering ablation ladder.
# Double-click this file in Finder to run the trial.
#
# Runs the two free arms one at a time:
#   Gemini free tier   ~8 min (paced at 6s/call to stay under the free quota)
#   Gemma 3 4B local   ~35 min (never alongside another local model — 8 GB RAM)
# Total spend: $0.00

cd "$(dirname "$0")" || exit 1

if [ -d .venv ]; then
    source .venv/bin/activate
fi

echo "==================================================================="
echo " Trial 2 — does the prompt change the decision?"
echo " 5 rungs x 4 cases x 3 repeats per arm. Free tiers only, \$0.00."
echo "==================================================================="
echo

echo "--- Checking ladder integrity -------------------------------------"
python3 -m scripts.trials.prompt_ablation_ladder --verify-rungs || {
    echo "Ladder is not valid — stopping."; read -r -p "Press Return to close."; exit 1; }
echo

echo "--- Arm 1/2: Gemini (free tier) + Claude reference ----------------"
python3 -m scripts.trials.prompt_ablation_ladder \
    --arms gemini,claude_sonnet --tag _gemini --delay 6
echo

echo "--- Arm 2/2: Gemma 3 4B (local) -----------------------------------"
echo "This one is slow (~35 min). Leave it running."
python3 -m scripts.trials.prompt_ablation_ladder \
    --arms ollama_gemma3_4b --tag _gemma
echo

echo "==================================================================="
echo " Done. Results in trials_out/:"
echo "   prompt_ablation_matrix_gemini.csv / _gemma.csv"
echo "   prompt_ablation_summary_gemini.json / _gemma.json"
echo "==================================================================="
read -r -p "Press Return to close this window."
