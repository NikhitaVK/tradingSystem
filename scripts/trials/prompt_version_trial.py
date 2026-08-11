"""
prompt_version_trial.py — Trial 2 (analyst prompt version: v1 vs v2 vs v3).

Runs the four ground-truth analyst test cases defined in
`.claude/rules/testing/calibration_tests.md` §5 against each version of the
analyst evaluation prompt, with repeats, and records what each returned.

This also fills calibration item 5 ("Prompt Quality Tests"), which currently
has no recorded result.

Design
------
Ablation discipline (`.claude/rules/ablation_methodology.md`): the ONLY thing
that varies between arms is the prompt file. Same cases, same client, same
thinking budget, same parser. The harness swaps `analyst_agent._EVAL_PROMPT`
and calls the real `_build_eval_prompt` / `_parse_eval_response`, so it
exercises the production code path rather than a reimplementation of it.

The four cases are constructed synthetically with known properties, per the
repo's own testing rule: ground truth requires that the correct answer be
knowable in advance, which real market data cannot give you.

Cases and their expected verdicts (from calibration_tests.md §5):
  bad         3 slices, all < 10 trades, negative Sharpe   -> should NOT deploy
  overfitted  strong in-sample, collapsed out-of-sample    -> should NOT deploy
  solid       consistent 55-60% win rate across slices     -> should deploy
  borderline  2 passing slices, 1 failing                  -> no correct answer,
                                                              consistency is the
                                                              thing measured

Repeats matter: LLM output varies run to run, so a single call per cell would
be noise rather than evidence.

Cost
----
4 cases x 3 versions x 3 repeats = 36 calls at Sonnet rates. Estimated ~$1-2.
Use --dry-run to print the matrix shape and estimated cost without spending.

Usage
-----
    python -m scripts.trials.prompt_version_trial --dry-run
    python -m scripts.trials.prompt_version_trial --repeats 3
"""
import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.settings import DB_PATH  # noqa: E402
import src.agents.analyst_agent as analyst_agent  # noqa: E402
from src.agents.claude_client import ClaudeClient  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[2] / "trials_out"
PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"

VERSIONS = ["v1", "v2", "v3"]

# "deploy" = the strategy would go live. v2/v3 return three verdicts; probation
# still deploys (at reduced size), so it counts as deploy. v1 returns pass/fail.
DEPLOY_VERDICTS = {"pass", "probation"}


# ── Ground-truth cases ───────────────────────────────────────────────────────

def _slice(i, win_rate, sharpe, trades, pnl, dd=0.08):
    return {
        "slice_id": i,
        "start_date": f"2025-0{i}-01",
        "end_date": f"2025-0{i + 3}-01",
        "win_rate": win_rate,
        "sharpe": sharpe,
        "max_drawdown": dd,
        "total_trades": trades,
        "pnl_pct": pnl,
    }


def _results(slices, wfe, pf, pf_tw=None, regime=None):
    wins = [s["win_rate"] for s in slices]
    return {
        "slices": slices,
        "aggregate": {
            "win_rate_mean": round(sum(wins) / len(wins), 3),
            "sharpe_mean": round(sum(s["sharpe"] for s in slices) / len(slices), 3),
            "max_drawdown_worst": max(s["max_drawdown"] for s in slices),
            "total_trades": sum(s["total_trades"] for s in slices),
            "profit_factor_mean": pf,
            "profit_factor_trade_weighted": pf_tw if pf_tw is not None else pf,
            "regime_breakdown": regime or {"trending_bull": 0.5, "sideways": 0.5},
        },
        "calibration": {
            "walk_forward_efficiency": wfe,
            "degradation_threshold": 0.30,
            "position_sizing": {
                "method": "atr", "atr_period": 14,
                "atr_multiplier": 1.5, "risk_per_trade_pct": 0.01,
            },
        },
        "viable": True,
    }


SPEC_RSI = {
    "name": "RSI Mean Reversion",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "indicators": [{"type": "RSI", "period": 14}, {"type": "EMA", "period": 50}],
    "entry": {"logic": "AND", "conditions": [
        {"indicator": "RSI_14", "operator": "<", "value": 30},
        {"indicator": "price", "operator": ">", "value": "EMA_50"}]},
    "exit": {"logic": "OR", "conditions": [
        {"indicator": "RSI_14", "operator": ">", "value": 70},
        {"type": "stop_loss_pct", "value": 2.0},
        {"type": "take_profit_pct", "value": 4.0}]},
}

CASES = {
    # 3 slices, all under the 10-trade floor, all negative Sharpe.
    "bad": {
        "expected_deploy": False,
        "spec": SPEC_RSI,
        "results": _results(
            [_slice(1, 0.31, -0.82, 7, -0.041, 0.19),
             _slice(2, 0.25, -1.15, 4, -0.063, 0.24),
             _slice(3, 0.33, -0.54, 9, -0.028, 0.17)],
            wfe=0.12, pf=0.61, pf_tw=0.58,
        ),
    },
    # Strong in-sample, collapsed out-of-sample: WFE is the tell.
    "overfitted": {
        "expected_deploy": False,
        "spec": {**SPEC_RSI, "name": "Tuned RSI + MACD Confluence",
                 "indicators": SPEC_RSI["indicators"] + [{"type": "MACD"}]},
        "results": _results(
            [_slice(1, 0.38, 0.21, 34, 0.006),
             _slice(2, 0.35, -0.14, 41, -0.011),
             _slice(3, 0.40, 0.09, 29, 0.003)],
            wfe=0.09, pf=1.04, pf_tw=1.02,
            regime={"trending_bull": 0.91, "sideways": 0.09},
        ),
    },
    # Consistent 55-60% win rate, healthy sample, good WFE.
    "solid": {
        "expected_deploy": True,
        "spec": {**SPEC_RSI, "name": "EMA Trend Pullback"},
        "results": _results(
            [_slice(1, 0.57, 1.34, 48, 0.119, 0.07),
             _slice(2, 0.55, 1.18, 52, 0.104, 0.09),
             _slice(3, 0.58, 1.41, 44, 0.131, 0.06)],
            wfe=0.86, pf=1.74, pf_tw=1.71,
            regime={"trending_bull": 0.42, "sideways": 0.33, "trending_bear": 0.25},
        ),
    },
    # 2 passing slices, 1 failing. No correct answer -- measures consistency.
    "borderline": {
        "expected_deploy": None,
        "spec": {**SPEC_RSI, "name": "Bollinger Reversion"},
        "results": _results(
            [_slice(1, 0.56, 1.21, 37, 0.098, 0.08),
             _slice(2, 0.54, 0.97, 41, 0.071, 0.11),
             _slice(3, 0.36, -0.43, 33, -0.039, 0.21)],
            wfe=0.61, pf=1.28, pf_tw=1.22,
        ),
    },
}


# ── Harness ──────────────────────────────────────────────────────────────────

def run_cell(version: str, case_name: str, case: dict, client) -> dict:
    """One call: one prompt version against one ground-truth case."""
    # The ONLY variable that changes between arms.
    analyst_agent._EVAL_PROMPT = (PROMPT_DIR / f"analyst_eval_{version}.txt").read_text()

    try:
        result = analyst_agent.evaluate(
            case["spec"], case["results"], client, db_path=None
        )
    except Exception as e:
        return {"verdict": "ERROR", "score": None, "error": str(e)[:200],
                "diagnosis": "", "n_challenges": 0}

    return {
        "verdict": result.get("verdict", ""),
        "score": result.get("score"),
        "error": "",
        "diagnosis": (result.get("diagnosis") or "").replace("\n", " ")[:300],
        "n_challenges": len(result.get("challenges") or []),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the matrix and cost estimate, spend nothing")
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    n_calls = len(CASES) * len(VERSIONS) * args.repeats

    print(f"Matrix: {len(CASES)} cases x {len(VERSIONS)} versions "
          f"x {args.repeats} repeats = {n_calls} calls")
    if args.dry_run:
        # ~4k input + ~1.5k output per call at Sonnet list price.
        est = n_calls * (4000 / 1e6 * 3.00 + 1500 / 1e6 * 15.00)
        print(f"Estimated cost: ~${est:.2f}  (dry run - nothing spent)")
        for name, c in CASES.items():
            agg = c["results"]["aggregate"]
            print(f"  {name:<12} expect_deploy={str(c['expected_deploy']):<5} "
                  f"WFE={c['results']['calibration']['walk_forward_efficiency']} "
                  f"PF={agg['profit_factor_trade_weighted']} "
                  f"trades={agg['total_trades']}")
        return

    client = ClaudeClient(db_path=args.db)
    rows = []
    for version in VERSIONS:
        for case_name, case in CASES.items():
            for rep in range(1, args.repeats + 1):
                print(f"  {version} / {case_name} / rep {rep} ... ", end="", flush=True)
                r = run_cell(version, case_name, case, client)
                deployed = r["verdict"] in DEPLOY_VERDICTS
                expected = case["expected_deploy"]
                correct = "" if expected is None else str(deployed == expected)
                print(f"{r['verdict'] or r['error']}")
                rows.append({
                    "version": version, "case": case_name, "repeat": rep,
                    "verdict": r["verdict"], "score": r["score"],
                    "deployed": deployed, "expected_deploy": expected,
                    "correct": correct, "n_challenges": r["n_challenges"],
                    "diagnosis": r["diagnosis"], "error": r["error"],
                })

    # ── Raw matrix ───────────────────────────────────────────────────────────
    matrix_path = OUT_DIR / "prompt_version_matrix.csv"
    with matrix_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ── Per-version summary ──────────────────────────────────────────────────
    by_version = defaultdict(list)
    for r in rows:
        by_version[r["version"]].append(r)

    summary = {}
    for version, rs in by_version.items():
        graded = [r for r in rs if r["correct"] != ""]
        n_correct = sum(1 for r in graded if r["correct"] == "True")
        # Consistency: did all repeats of a case agree?
        agree = 0
        for case_name in CASES:
            verdicts = {r["verdict"] for r in rs if r["case"] == case_name}
            agree += 1 if len(verdicts) == 1 else 0
        scores = [r["score"] for r in rs if isinstance(r["score"], (int, float))]
        summary[version] = {
            "graded_cells": len(graded),
            "correct": n_correct,
            "accuracy": round(n_correct / len(graded), 3) if graded else None,
            "cases_fully_consistent": agree,
            "n_cases": len(CASES),
            "errors": sum(1 for r in rs if r["error"]),
            "mean_score": round(sum(scores) / len(scores), 3) if scores else None,
        }

    (OUT_DIR / "prompt_version_summary.json").write_text(
        json.dumps(summary, indent=2)
    )

    print()
    print(f"  {'version':<10}{'correct':>10}{'accuracy':>11}"
          f"{'consistent':>13}{'errors':>9}")
    for v in VERSIONS:
        s = summary[v]
        print(f"  {v:<10}{s['correct']:>4}/{s['graded_cells']:<5}"
              f"{str(s['accuracy']):>11}"
              f"{s['cases_fully_consistent']:>8}/{s['n_cases']:<4}"
              f"{s['errors']:>9}")
    print()
    print(f"Wrote {matrix_path.name} and prompt_version_summary.json to trials_out/")


if __name__ == "__main__":
    main()
