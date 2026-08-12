"""
prompt_ablation_ladder.py — Trial 2 (rebuild): does the prompt change the decision?

The question (implications_planning.md:76)
------------------------------------------
"2 — Prompt engineering per agent", scored against **Functionality** and
**Legal/Ethical (honest evaluation)**. Same agent, same data, same parser --
only the instruction text varies. Does prompt engineering actually change the
quality of the decision the agent makes?

Why this replaces the earlier run
---------------------------------
scripts/trials/prompt_version_trial.py compared analyst_eval v1/v2/v3 and
returned a NULL result: all three scored 9/9 on the graded cases, every repeat.
Its own write-up concludes "v2 and v3 are not separated by this test set."

It also changed two things at once between v1 and v2 -- a prose rubric became
quantitative formulas AND a binary verdict became three-way pass/probation/fail.
Had the versions differed, the trial could not have said which change caused it.
That violates .claude/rules/testing/ablation_methodology.md: "one change per
test run."

Both faults have the same root cause: every version tested already contained a
full evaluation rubric, so they were three variations on one design. There was
no bottom rung testing whether the rubric as a whole does anything.

The ladder -- each rung adds exactly ONE element
------------------------------------------------
    P0  bare          data + output contract, NO decision criteria at all
    P1  + criteria    the five criteria in prose, no formulas, no thresholds
    P2  + formulas    explicit sub-score formulas and fixed weights
    P3  + thresholds  score cutoffs 0.70/0.50 + hard auto-fails  == analyst_eval_v2.txt
    P4  + KB context  the {regime_failures} section              == analyst_eval_v3.txt (SHIPS)

P3 and P4 are the shipped prompts verbatim; P0-P2 were built by literal
subtraction from v2, so the ladder is anchored to the real system rather than
paraphrasing it. Every rung carries a byte-identical data block and a
byte-identical response schema -- verified by --verify-rungs. If P0 also saw
less data, or was allowed a different output shape, this would be measuring two
changes again.

Arms
----
    gemini              hosted, free   Trial 1 measured it at 100% accuracy and
                                       100% JSON on these same four cases, i.e.
                                       identical to Claude -- an evidenced free
                                       stand-in, not an assumed one
    ollama_gemma3_4b    local, free    the weak-model contrast (33% baseline).
                                       If prompt structure matters more where
                                       capability is scarce, only this arm can
                                       show it.
    claude_sonnet       hosted, paid   REFERENCE ROW ONLY, P4 only, read free
                                       from reasoning_logs. Not a ladder rung.

Two measurement traps this harness avoids
-----------------------------------------
1. analyst_agent._parse_eval_response falls back to verdict="fail" on any
   exception. Two of the three graded cases EXPECT "fail", so a rung emitting
   pure prose would score ~67% correct AND a perfect 0% false-deploy rate --
   looking like the safest rung on the ethical metric precisely because it
   answered nothing. P0 is the rung most likely to trigger this. JSON validity
   is therefore checked independently, and invalid cells are excluded from both
   accuracy and false-deploy rather than counted as correct or as safe.

2. Running two local models back-to-back on 8 GB of unified memory produces
   false zeroes through RAM contention -- this happened to gemma3 in Trial 1.
   The harness refuses to run more than one Ollama arm per process.

Usage
-----
    python3 -m scripts.trials.prompt_ablation_ladder --verify-rungs
    python3 -m scripts.trials.prompt_ablation_ladder --dry-run
    python3 -m scripts.trials.prompt_ablation_ladder --arms gemini
    python3 -m scripts.trials.prompt_ablation_ladder --arms ollama_gemma3_4b
"""
import argparse
import csv
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402

from config.settings import DB_PATH  # noqa: E402
import src.agents.analyst_agent as analyst_agent  # noqa: E402
from scripts.trials.prompt_version_trial import CASES, DEPLOY_VERDICTS  # noqa: E402
from scripts.trials.provider_comparison_trial import (  # noqa: E402
    ARMS, call_openai_compat, check_arm, claude_from_logs, json_is_valid,
)

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "trials_out"
PROMPT_DIR = ROOT / "prompts"

# Claude Sonnet 4.6 list price, USD per million tokens. Used to price what each
# rung's extra instruction text would cost on the model the system ships on,
# even though the measured arms are free.
PRICE_IN, PRICE_OUT = 3.00, 15.00
CHARS_PER_TOKEN = 4.04          # measured on this corpus while API credit existed

# ── The ladder ───────────────────────────────────────────────────────────────
# `adds` names the single element this rung introduces over the one below it.
RUNGS = [
    ("P0", "ablation/analyst_eval_P0.txt", "bare (no criteria)", "—"),
    ("P1", "ablation/analyst_eval_P1.txt", "+ criteria (prose)", "the five criteria, stated in prose"),
    ("P2", "ablation/analyst_eval_P2.txt", "+ formulas", "sub-score formulas and fixed weights"),
    ("P3", "analyst_eval_v2.txt", "+ thresholds  (= v2)", "score cutoffs 0.70/0.50 and hard auto-fails"),
    ("P4", "analyst_eval_v3.txt", "+ KB context  (= v3, ships)", "the {regime_failures} section"),
]
RUNG_ORDER = [r[0] for r in RUNGS]

# Cases in a fixed order so the Claude log rows line up with the right case.
CASE_ORDER = ["bad", "overfitted", "solid", "borderline"]

# Cases where deploying is the WRONG answer. The false-deploy metric is computed
# over these only: rejecting a good strategy costs an opportunity, deploying a
# bad one puts money at risk, and an accuracy figure hides that asymmetry.
UNSAFE_CASES = [c for c in CASE_ORDER if CASES[c]["expected_deploy"] is False]

# Expected ordering of mean score, worst strategy to best. Only meaningful for
# rungs that emit a numeric score.
MONOTONIC_ORDER = ["bad", "overfitted", "borderline", "solid"]

# ── Groq arms ────────────────────────────────────────────────────────────────
# Added after Gemini's free tier turned out to be 20 requests PER DAY
# (quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier, quotaValue 20),
# which cannot cover a 60-cell ladder at any pacing.
#
# The binding Groq limit is tokens/day, not requests/day, because these prompts
# are large (~1.9k input tokens each):
#     llama-3.1-8b-instant    14,400 req/day   500K tok/day   fits 60 cells
#     llama-3.3-70b-versatile  1,000 req/day   100K tok/day   dies at ~38 cells
# so the 8B is the arm that can actually finish. Hosted, so unlike the local
# Gemma arm it puts no load on this machine.
_GROQ_MODELS = {
    "groq_llama3.1_8b": "llama-3.1-8b-instant",
    "groq_llama3.3_70b": "llama-3.3-70b-versatile",
}
for _arm, _model in _GROQ_MODELS.items():
    ARMS[_arm] = {
        "kind": "openai_compat",
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        "model": _model,
        "tier": "hosted, free tier",
        "price_in": 0.0, "price_out": 0.0,
        "trains_on_data": "no — Groq states it does not train on API data",
        "residency": "vendor cloud",
    }

ARM_NAMES = ["groq_llama3.1_8b", "claude_sonnet"]


def load_rung(rel_path: str) -> str:
    p = PROMPT_DIR / rel_path
    if not p.exists():
        raise FileNotFoundError(f"rung prompt missing: {p}")
    return p.read_text()


def render(rung_path: str, case: dict) -> str:
    """
    Render one rung's full system prompt for one case. Swapping the module
    global is the same mechanism prompt_version_trial used; _build_eval_prompt
    reads _EVAL_PROMPT at call time, so the rebinding takes effect.
    """
    analyst_agent._EVAL_PROMPT = load_rung(rung_path)
    return analyst_agent._build_eval_prompt(
        case["spec"], case["results"], "Knowledge base not consulted.")


# ── Rung integrity ───────────────────────────────────────────────────────────

def verify_rungs() -> bool:
    """
    The ladder is only valid if every rung differs in instruction text ALONE.
    Checks, for a fixed case:
      1. P3 renders identically to analyst_eval_v2, P4 to analyst_eval_v3
         (they are literally those files, so this guards against edits).
      2. Every rung contains the identical rendered data block.
      3. Prompt length increases monotonically up the ladder.
    """
    case = CASES["solid"]
    ok = True
    rendered = {}
    for name, path, _, _ in RUNGS:
        rendered[name] = render(path, case)

    # 1. anchoring
    for rung, ref in (("P3", "analyst_eval_v2.txt"), ("P4", "analyst_eval_v3.txt")):
        if rendered[rung] != render(ref, case):
            print(f"  FAIL  {rung} does not match {ref}")
            ok = False
        else:
            print(f"  ok    {rung} renders identically to {ref}")

    # 2. shared data block -- the rendered strategy spec + backtest results
    marker = json.dumps(case["spec"], indent=2)
    for name in rendered:
        if marker not in rendered[name]:
            print(f"  FAIL  {name} is missing the shared data block")
            ok = False
    if ok:
        print(f"  ok    all {len(rendered)} rungs carry the identical data block")

    # 3. monotonic growth
    lens = [(n, len(rendered[n])) for n in RUNG_ORDER]
    if all(lens[i][1] < lens[i + 1][1] for i in range(len(lens) - 1)):
        print("  ok    prompt length increases monotonically: " +
              " < ".join(f"{n}:{l}" for n, l in lens))
    else:
        print("  FAIL  prompt length is not monotonic: " +
              ", ".join(f"{n}:{l}" for n, l in lens))
        ok = False
    return ok


# ── Rate limiting ────────────────────────────────────────────────────────────

def call_paced(cfg: dict, system_prompt: str, delay: float) -> dict:
    """
    call_openai_compat plus free-tier rate-limit handling.

    Trial 1 never hit this because it made 12 calls per arm; this ladder makes
    60, and Gemini's free tier returns HTTP 429 well before that. The first run
    of this trial lost ALL of rungs P1 and P2 to 429s, which would have looked
    like two rungs failing to produce output rather than a quota ceiling.

    A 429 is a quota signal, not a judgement about the prompt, so it is retried
    with exponential backoff and honours Retry-After. A model that answers badly
    is still never retried -- that is a result.
    """
    for attempt in range(6):
        try:
            if delay:
                time.sleep(delay)
            return call_openai_compat(cfg, system_prompt)
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429 or attempt == 5:
                raise
            wait = float(e.response.headers.get("retry-after") or 0) or 2 ** (attempt + 2)
            print(f"      429 rate limited — waiting {wait:.0f}s "
                  f"(attempt {attempt + 1}/5)")
            time.sleep(wait)
    raise RuntimeError("unreachable")


# ── Scoring ──────────────────────────────────────────────────────────────────

def score_cell(text: str, case_name: str) -> dict:
    """
    Grade one response. Invalid JSON yields blank fields, so the cell drops out
    of accuracy and false-deploy instead of being scored -- see trap 1.
    """
    case = CASES[case_name]
    if not json_is_valid(text):
        return {"json_valid": False, "verdict": "", "score": "",
                "deployed": "", "correct": ""}
    parsed = analyst_agent._parse_eval_response(text)
    verdict = parsed.get("verdict", "")
    deployed = verdict in DEPLOY_VERDICTS
    expected = case["expected_deploy"]
    return {
        "json_valid": True,
        "verdict": verdict,
        "score": parsed.get("score"),
        "deployed": deployed,
        "correct": "" if expected is None else str(deployed == expected),
    }


def summarise_rung(rows: list) -> dict:
    """rows = every cell for one (arm, rung)."""
    received = [r for r in rows if r["error"] == ""]
    valid = [r for r in received if r["json_valid"] is True]

    graded = [r for r in valid if r["correct"] != ""]
    correct = [r for r in graded if r["correct"] == "True"]

    # false deploy: an unsafe case that the rung was willing to deploy
    unsafe = [r for r in valid if r["case"] in UNSAFE_CASES]
    false_deploys = [r for r in unsafe if r["deployed"] is True]

    # consistency: cases where every repeat produced the same verdict
    consistent = 0
    for c in CASE_ORDER:
        verdicts = {r["verdict"] for r in valid if r["case"] == c}
        if len(verdicts) == 1:
            consistent += 1

    # Score monotonicity, only where a numeric score exists AND is on the
    # defined 0-1 scale. Measured on the first run: P1 returned 5.0 and 53.0 on
    # the same case, because that rung names five criteria but never states the
    # range, so the model invented 0-10 and 0-100 scales. Averaging those in
    # would produce a mean of 29.0 and silently destroy the metric. Out-of-range
    # scores are excluded here and counted separately -- the VERDICT from those
    # cells is still valid and still graded.
    out_of_range = 0
    means = {}
    for c in CASE_ORDER:
        vals = []
        for r in valid:
            if r["case"] != c or not isinstance(r["score"], (int, float)):
                continue
            if 0.0 <= r["score"] <= 1.0:
                vals.append(r["score"])
            else:
                out_of_range += 1
        means[c] = round(statistics.mean(vals), 3) if vals else None
    ordered = [means[c] for c in MONOTONIC_ORDER]
    monotonic = (None if any(v is None for v in ordered)
                 else all(ordered[i] <= ordered[i + 1] for i in range(len(ordered) - 1)))

    in_toks = [r["in_tokens"] for r in received if r["in_tokens"]]
    out_toks = [r["out_tokens"] for r in received if r["out_tokens"]]
    lats = [r["latency_s"] for r in received if r["latency_s"]]
    mean_in = statistics.mean(in_toks) if in_toks else None
    mean_out = statistics.mean(out_toks) if out_toks else None

    return {
        "cells": len(rows),
        "responses": len(received),
        "errors": len(rows) - len(received),
        "json_compliance": round(len(valid) / len(received), 4) if received else None,
        "graded": len(graded),
        "correct": len(correct),
        "accuracy": round(len(correct) / len(graded), 4) if graded else None,
        "unsafe_cells": len(unsafe),
        "false_deploys": len(false_deploys),
        "false_deploy_rate": (round(len(false_deploys) / len(unsafe), 4)
                              if unsafe else None),
        "consistency": consistent,
        "mean_score_by_case": means,
        "scores_out_of_range": out_of_range,
        "score_monotonic": monotonic,
        "prompt_tokens": round(mean_in) if mean_in else None,
        "output_tokens": round(mean_out) if mean_out else None,
        "cost_per_1k": (round((mean_in / 1e6 * PRICE_IN
                               + (mean_out or 0) / 1e6 * PRICE_OUT) * 1000, 4)
                        if mean_in else None),
        "mean_latency": round(statistics.mean(lats), 2) if lats else None,
    }


# ── Run ──────────────────────────────────────────────────────────────────────

def run_arm(arm: str, cfg: dict, repeats: int, db_path: str,
            delay: float = 0.0) -> list:
    rows = []

    # Claude is a reference row, not a ladder: only P4 (= v3) was ever logged.
    if cfg["kind"] == "claude_logs":
        logged = claude_from_logs(db_path)
        for i, case_name in enumerate(CASE_ORDER):
            for rep in range(repeats):
                idx = i * repeats + rep
                if idx >= len(logged):
                    rows.append(_blank(arm, "P4", case_name, rep,
                                       "no logged row for this cell"))
                    continue
                resp = logged[idx]
                cell = score_cell(resp["text"], case_name)
                rows.append(_row(arm, "P4", case_name, rep, resp, cell, ""))
        return rows

    for rung, path, _, _ in RUNGS:
        for case_name in CASE_ORDER:
            case = CASES[case_name]
            system_prompt = render(path, case)
            for rep in range(repeats):
                try:
                    resp = call_paced(cfg, system_prompt, delay)
                    cell = score_cell(resp["text"], case_name)
                    rows.append(_row(arm, rung, case_name, rep, resp, cell, ""))
                    flag = "" if cell["json_valid"] else "  [invalid JSON]"
                    print(f"    {rung} {case_name:<11} r{rep} "
                          f"{cell['verdict'] or '-':<10} "
                          f"{resp['latency_s']:.1f}s{flag}")
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"[:200]
                    rows.append(_blank(arm, rung, case_name, rep, err))
                    print(f"    {rung} {case_name:<11} r{rep} ERROR {err[:70]}")
    return rows


def _row(arm, rung, case_name, rep, resp, cell, error) -> dict:
    return {
        "arm": arm, "rung": rung, "case": case_name, "repeat": rep,
        "json_valid": cell["json_valid"], "verdict": cell["verdict"],
        "score": cell["score"], "deployed": cell["deployed"],
        "correct": cell["correct"],
        "expected_deploy": CASES[case_name]["expected_deploy"],
        "in_tokens": resp.get("in_tokens"), "out_tokens": resp.get("out_tokens"),
        "latency_s": (round(resp["latency_s"], 2)
                      if resp.get("latency_s") is not None else None),
        "error": error,
        "response_chars": len(resp.get("text") or ""),
    }


def _blank(arm, rung, case_name, rep, error) -> dict:
    """An explicit failure row. Never a default verdict -- that is fabrication."""
    return {
        "arm": arm, "rung": rung, "case": case_name, "repeat": rep,
        "json_valid": None, "verdict": "", "score": "", "deployed": "",
        "correct": "", "expected_deploy": CASES[case_name]["expected_deploy"],
        "in_tokens": None, "out_tokens": None, "latency_s": None,
        "error": error, "response_chars": 0,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Trial 2 rebuild: prompt ablation ladder")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--arms", help="comma-separated subset of arm names")
    ap.add_argument("--tag", default="", help="suffix for output filenames")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="seconds to pause before each call; use ~6 on Gemini's "
                         "free tier, which 429s well before 60 calls")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify-rungs", action="store_true",
                    help="check ladder integrity and exit")
    args = ap.parse_args()

    if args.verify_rungs:
        print("Rung integrity check:")
        sys.exit(0 if verify_rungs() else 1)

    wanted = ([a.strip() for a in args.arms.split(",")] if args.arms else ARM_NAMES)
    unknown = [a for a in wanted if a not in ARMS]
    if unknown:
        sys.exit(f"unknown arm(s): {', '.join(unknown)}")

    # RAM contention guard -- see trap 2.
    local = [a for a in wanted
             if str(ARMS[a].get("base_url", "")).startswith("http://localhost:11434")]
    if len(local) > 1:
        sys.exit(f"refusing to run {len(local)} local models in one process "
                 f"({', '.join(local)}) — 8 GB of unified memory produces false "
                 f"zeroes through RAM contention. Run them one at a time with --arms.")

    print("Ladder integrity:")
    if not verify_rungs():
        sys.exit("ladder is not valid — refusing to run")

    print("\nArm availability:")
    avail = {}
    for a in wanted:
        ok, note = check_arm(a, ARMS[a], args.db)
        avail[a] = ok
        print(f"  {'ok  ' if ok else 'MISS'}  {a:<20} {note}")

    n_rungs = len(RUNGS)
    live = [a for a in wanted if avail[a]]
    calls = sum(1 if ARMS[a]["kind"] == "claude_logs"
                else n_rungs * len(CASE_ORDER) * args.repeats for a in live)
    print(f"\nMatrix: {n_rungs} rungs x {len(CASE_ORDER)} cases x {args.repeats} "
          f"repeats = {n_rungs * len(CASE_ORDER) * args.repeats} cells per live arm")
    print(f"Live arms: {', '.join(live) or 'none'}  →  {calls} calls, $0.00 "
          f"(free tiers and local inference only)")

    if args.dry_run:
        print("\n--dry-run: nothing called.")
        return
    if not live:
        sys.exit("no arms available")

    all_rows, summary = [], {}
    for a in live:
        print(f"\n=== {a} ===")
        t0 = time.time()
        rows = run_arm(a, ARMS[a], args.repeats, args.db, args.delay)
        all_rows.extend(rows)
        by_rung = {}
        for rung in RUNG_ORDER:
            sub = [r for r in rows if r["rung"] == rung]
            if sub:
                by_rung[rung] = summarise_rung(sub)
        summary[a] = {
            "tier": ARMS[a]["tier"], "model": ARMS[a]["model"],
            "ran": True, "wall_seconds": round(time.time() - t0, 1),
            "rungs": by_rung,
        }
    for a in wanted:
        if a not in summary:
            summary[a] = {"ran": False, "tier": ARMS[a]["tier"],
                          "reason": "arm not available"}

    OUT_DIR.mkdir(exist_ok=True)
    tag = args.tag
    matrix = OUT_DIR / f"prompt_ablation_matrix{tag}.csv"
    with matrix.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(all_rows[0].keys()))
        w.writeheader()
        w.writerows(all_rows)

    payload = {
        "trial": "2 (rebuild) — prompt engineering ablation ladder",
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "repeats": args.repeats,
        "cases": CASE_ORDER,
        "unsafe_cases": UNSAFE_CASES,
        "rungs": [{"rung": r[0], "file": r[1], "label": r[2], "adds": r[3]}
                  for r in RUNGS],
        "decision_rule": (
            "Adopt the simplest rung that (a) records zero false deploys across "
            "all repeats, (b) returns valid JSON 100% of the time, and (c) is not "
            "beaten on verdict accuracy by any rung above it. Ties go to the "
            "simpler rung. Fixed before the run."),
        "note_on_parse_failures": (
            "_parse_eval_response defaults to verdict='fail'. Two of three graded "
            "cases expect 'fail', so an unparseable rung would score ~67% correct "
            "AND 0% false deploys. Invalid cells are therefore excluded from both "
            "metrics rather than counted as correct or as safe."),
        "arms": summary,
    }
    summ = OUT_DIR / f"prompt_ablation_summary{tag}.json"
    summ.write_text(json.dumps(payload, indent=2, default=str))

    print(f"\nWrote {matrix}\n      {summ}")
    _print_table(summary)


def fmt_opt(v, spec: str = "{:.2f}") -> str:
    """Format a metric that may be None, without printing a misleading 0."""
    return "-" if v is None else spec.format(v)


def _print_table(summary: dict) -> None:
    for arm, s in summary.items():
        if not s.get("ran"):
            print(f"\n{arm}: NOT RUN — {s.get('reason')}")
            continue
        print(f"\n{arm} ({s['tier']}, {s['model']})")
        hdr = (f"  {'rung':<6}{'resp':>6}{'json':>7}{'acc':>7}"
               f"{'falseDep':>10}{'consist':>9}{'mono':>7}{'tokens':>8}{'lat':>8}")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for rung in RUNG_ORDER:
            r = s["rungs"].get(rung)
            if not r:
                continue
            fdep = "{} ({})".format(fmt_opt(r["false_deploy_rate"]),
                                    r["false_deploys"])
            cells = [
                "{:<6}".format(rung),
                "{:>6}".format(r["responses"]),
                "{:>7}".format(fmt_opt(r["json_compliance"])),
                "{:>7}".format(fmt_opt(r["accuracy"])),
                "{:>10}".format(fdep),
                "{:>9}".format(r["consistency"]),
                "{:>7}".format(str(r["score_monotonic"])),
                "{:>8}".format(fmt_opt(r["prompt_tokens"], "{:.0f}")),
                "{:>8}".format(fmt_opt(r["mean_latency"], "{:.1f}s")),
            ]
            print("  " + "".join(cells))


if __name__ == "__main__":
    main()
