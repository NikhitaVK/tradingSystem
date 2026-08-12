"""
combine_prompt_ablation.py — pool the Trial 2 runs and check replication.

Why this exists
---------------
The first 60-cell run put P2 ahead of the shipped P3/P4 on accuracy and false
deploys. That margin was 5/7 against 4/8 -- a difference of one or two cells,
which is well inside noise. Trial 1 already demonstrated the hazard: llama-3.2-3b
scored 25% and 75% JSON compliance on two 12-cell runs of the identical setup,
and only n=36 settled it.

So the trial was run a second time, independently, and this script reports the
two runs SIDE BY SIDE before pooling them. A result that appears in run 1 and
vanishes in run 2 is noise, and saying so is the point of the exercise.

It also recomputes every summary from the raw matrices with the out-of-range
score fix (see summarise_rung), which the first run's stored summary predates.

Usage:
    python3 -m scripts.trials.combine_prompt_ablation
"""
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.trials.prompt_ablation_ladder import (  # noqa: E402
    CASE_ORDER, RUNG_ORDER, RUNGS, UNSAFE_CASES, fmt_opt, summarise_rung,
)

OUT_DIR = Path(__file__).resolve().parents[2] / "trials_out"

RUN_FILES = {
    "run 1": "prompt_ablation_matrix_groq8b.csv",
    "run 2": "prompt_ablation_matrix_groq8b_rep2.csv",
}
ARM = "groq_llama3.1_8b"


def load_rows(path: Path) -> list:
    """Rebuild typed rows from a matrix CSV (everything comes back as str)."""
    out = []
    for r in csv.DictReader(path.open()):
        row = dict(r)
        row["json_valid"] = (True if r["json_valid"] == "True"
                             else False if r["json_valid"] == "False" else None)
        row["deployed"] = (True if r["deployed"] == "True"
                           else False if r["deployed"] == "False" else "")
        for k in ("in_tokens", "out_tokens"):
            row[k] = int(float(r[k])) if r[k] else None
        row["latency_s"] = float(r["latency_s"]) if r["latency_s"] else None
        try:
            row["score"] = float(r["score"])
        except (ValueError, TypeError):
            row["score"] = r["score"]
        row["repeat"] = int(r["repeat"])
        return_row = row
        out.append(return_row)
    return out


def table(title: str, rungs: dict) -> None:
    print(f"\n{title}")
    hdr = (f"  {'rung':<6}{'n':>5}{'json':>8}{'graded':>8}{'correct':>9}"
           f"{'acc':>7}{'unsafe':>8}{'fDep':>6}{'fDepRate':>10}{'consist':>9}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for k in RUNG_ORDER:
        x = rungs.get(k)
        if not x:
            continue
        print(f"  {k:<6}{x['responses']:>5}{fmt_opt(x['json_compliance']):>8}"
              f"{x['graded']:>8}{x['correct']:>9}{fmt_opt(x['accuracy']):>7}"
              f"{x['unsafe_cells']:>8}{x['false_deploys']:>6}"
              f"{fmt_opt(x['false_deploy_rate']):>10}{x['consistency']:>9}")


def main() -> None:
    runs, all_rows = {}, []
    for label, fname in RUN_FILES.items():
        p = OUT_DIR / fname
        if not p.exists():
            print(f"  (missing {fname} — skipping {label})")
            continue
        rows = [r for r in load_rows(p) if r["arm"] == ARM]
        runs[label] = {k: summarise_rung([r for r in rows if r["rung"] == k])
                       for k in RUN_ORDER_SAFE(rows)}
        all_rows.extend(rows)

    for label, rungs in runs.items():
        table(f"{label} — {ARM} (3 repeats)", rungs)

    if len(runs) < 2:
        print("\nOnly one run present — no replication check possible.")
    else:
        print("\nReplication check — does each rung's ranking hold across runs?")
        for metric, better in (("accuracy", max), ("false_deploy_rate", min)):
            line = []
            for label, rungs in runs.items():
                vals = {k: rungs[k][metric] for k in RUNG_ORDER
                        if k in rungs and rungs[k][metric] is not None}
                if not vals:
                    continue
                win = better(vals, key=vals.get)
                line.append(f"{label}: best={win} ({vals[win]:.2f})")
            print(f"  {metric:<18} " + "   ".join(line))

    if all_rows:
        pooled = {k: summarise_rung([r for r in all_rows if r["rung"] == k])
                  for k in RUNG_ORDER}
        n_reps = 3 * len(runs)   # each run is a 3-repeat pass over the ladder
        table(f"POOLED — {ARM} ({len(runs)} run(s), {n_reps} repeats, "
              f"{len(all_rows)} cells)", pooled)

        payload = {
            "trial": "2 (rebuild) — prompt ablation ladder, pooled",
            "arm": ARM,
            "runs_pooled": list(runs.keys()),
            "cells_total": len(all_rows),
            "cases": CASE_ORDER,
            "unsafe_cases": UNSAFE_CASES,
            "rungs_meta": [{"rung": r[0], "file": r[1], "adds": r[3]} for r in RUNGS],
            "per_run": runs,
            "pooled": pooled,
        }
        out = OUT_DIR / "prompt_ablation_summary_pooled.json"
        out.write_text(json.dumps(payload, indent=2, default=str))
        print(f"\nWrote {out}")


def RUN_ORDER_SAFE(rows) -> list:
    """Rungs actually present in these rows, in ladder order."""
    present = {r["rung"] for r in rows}
    return [k for k in RUNG_ORDER if k in present]


if __name__ == "__main__":
    main()
