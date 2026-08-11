"""
memory_outcome_trial.py — Does better memory produce better STRATEGIES?

This is the outcome arm of the memory trial spec
(claude_docs/trials/2026-08-08-memory-design-options.md). The structural trial
(kb_structure_trial.py) measured what each retrieval design *costs* and what
*shape* of bundle it returns. It never measured whether the retrieved findings
made the system choose better. This does.

The question
------------
Loop 1 is deterministic right up to one point: `candidate_generator` produces a
fixed pool, `empirical_search` backtests and ranks it, and then **an LLM picks
one survivor**. The knowledge base enters at exactly that step, as `kb_context`
in the selector's prompt. So: does changing the memory bundle change which
strategy gets picked, and is the pick better?

Why there is a holdout
----------------------
Candidates are ranked by composite score. If "quality" were measured with that
same score, the best possible pick would always be candidate #0 by definition,
and memory could only ever hurt. That test would be rigged.

So the data is split in two:

    train window   2024-01-01 .. 2025-04-01   rank the candidates here
    holdout window 2025-04-01 .. 2026-04-11   score the CHOSEN one here

Ranking on train and scoring on holdout means the top-ranked candidate is no
longer automatically the best, so memory has genuine room to help or to hurt.

Arms (the only thing that varies)
---------------------------------
    N  none          kb_context = []                     control
    A  flat keyword  query_relevant(keywords)
    B  regime-aware  query_relevant(keywords, regime=)
    C  layered       get_working_memory(regime, mechanism)   -- ships today

Everything else is held constant: same scenario, same candidate pool, same
ranked survivors, same prompt template, same thinking budget.

Metrics
-------
    holdout_score  trade-weighted profit factor of the chosen strategy on the
                   unseen window (NOT the composite used for ranking -- see
                   the note on HOLDOUT_SLICES)
    regret         best_available_holdout - chosen_holdout  (0 = picked the
                   genuinely best survivor; higher is worse)

Paired by scenario across arms -> Wilcoxon signed-rank, the test FinMem uses.

Usage
-----
    python -m scripts.trials.memory_outcome_trial --dry-run
    python -m scripts.trials.memory_outcome_trial --repeats 3
"""
import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.settings import DB_PATH, CLAUDE_THINKING_BUDGET_STRATEGY  # noqa: E402
from src.agents.candidate_generator import generate_candidate_pool  # noqa: E402
from src.agents.empirical_search import run_search  # noqa: E402
from src.agents.claude_client import ClaudeClient  # noqa: E402
from src.agents.strategy_agent import _build_system_prompt, _parse_selection  # noqa: E402
from src.backtest.engine import run_backtest  # noqa: E402
from src.data.knowledge_base import get_working_memory, query_relevant  # noqa: E402
from src.data.schema import init_db  # noqa: E402
from src.loop1 import _flatten_working_memory  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[2] / "trials_out"
WORK = Path("/private/tmp/claude-501/-Users-nikhita-13DIT-tradingSystemv0-01/"
            "f2682cbf-9132-48bf-824f-70f872cc97ce/scratchpad")

# 2025-04-01T00:00:00Z -- a 50/50 split. An 80/20 split was tried first and
# failed: ~4,600 holdout bars run through a 5-slice walk-forward leaves ~185
# out-of-sample bars per slice, yielding 1-3 trades, under the 5-trade floor.
# Every candidate then scored exactly 0.0 and the trial could not discriminate.
SPLIT_MS = 1743465600000

# Slices used when scoring on the holdout window. Fewer than the production 5,
# for the same reason: each slice needs enough bars to produce real trades.
HOLDOUT_SLICES = 3

ARMS = ["N", "A", "B", "C"]

KEYWORDS = {
    "momentum": ["momentum", "macd", "crossover"],
    "mean_reversion": ["rsi", "oversold", "reversion"],
    "breakout": ["breakout", "bollinger", "volatility"],
}

# Contexts chosen because the KB actually holds findings tagged this way --
# arms B and C are only meaningful where there is regime/mechanism content.
CONTEXTS = [
    ("high_vol", "momentum"),
    ("trending_bull", "mean_reversion"),
    ("trending_bull", "momentum"),
]
SYMBOLS = ["BTC/USDT", "ETH/USDT"]


# ── Train / holdout split ────────────────────────────────────────────────────

def build_split(src_db: str) -> tuple[str, str]:
    """Materialise a train DB and a holdout DB of real OHLCV bars."""
    train, hold = WORK / "trial_train.db", WORK / "trial_holdout.db"
    for p in (train, hold):
        p.unlink(missing_ok=True)
        init_db(str(p))

    src = sqlite3.connect(src_db)
    for path, op in ((train, "<"), (hold, ">=")):
        rows = src.execute(
            f"SELECT symbol,timeframe,timestamp,open,high,low,close,volume "
            f"FROM ohlcv_history WHERE timestamp {op} ?", (SPLIT_MS,)
        ).fetchall()
        dst = sqlite3.connect(str(path))
        dst.executemany(
            "INSERT OR IGNORE INTO ohlcv_history "
            "(symbol,timeframe,timestamp,open,high,low,close,volume) "
            "VALUES (?,?,?,?,?,?,?,?)", rows,
        )
        dst.commit(); dst.close()
        print(f"  {path.name}: {len(rows):,} bars")
    src.close()
    return str(train), str(hold)


# ── Memory arms ──────────────────────────────────────────────────────────────

def build_kb_context(arm: str, regime: str, mechanism: str, db_path: str) -> list:
    """The ONLY thing that differs between arms."""
    if arm == "N":
        return []
    kw = KEYWORDS.get(mechanism, KEYWORDS["momentum"])
    if arm == "A":
        return query_relevant(kw, db_path, limit=15)
    if arm == "B":
        return query_relevant(kw, db_path, limit=15, regime=regime)
    if arm == "C":
        return _flatten_working_memory(
            get_working_memory(db_path, current_regime=regime, mechanism=mechanism)
        )
    raise ValueError(arm)


# ── Scenario preparation (deterministic, no LLM, no cost) ────────────────────

def prepare_scenario(symbol: str, regime: str, mechanism: str,
                     train_db: str, hold_db: str) -> Optional[dict]:
    """
    Rank candidates on the train window, then score every survivor on the
    holdout window. Both are deterministic -- done once, reused by all arms.
    """
    pair = {"symbol": symbol, "timeframe": "1h"}
    candidates = generate_candidate_pool(regime, pair)
    ranked = run_search(candidates, train_db)
    if not ranked:
        print(f"    no viable candidates for {symbol}/{regime} - skipped")
        return None

    # Holdout is scored on trade-weighted profit factor, NOT the composite score
    # used for ranking. The composite multiplies by walk-forward efficiency,
    # which is a nested concept inside an already-out-of-sample window and
    # collapses to zero there. PF_tw is what the analyst treats as authoritative.
    holdout, trades = [], []
    for spec, _train_results, _train_score in ranked:
        try:
            hr = run_backtest(spec, hold_db, n_slices=HOLDOUT_SLICES)
            agg = hr.get("aggregate", {})
            holdout.append(float(agg.get("profit_factor_trade_weighted", 0.0)))
            trades.append(int(agg.get("total_trades", 0)))
        except Exception as e:
            print(f"    holdout backtest failed for {spec.get('name')}: {e}")
            holdout.append(float("nan"))
            trades.append(0)

    return {
        "symbol": symbol, "regime": regime, "mechanism": mechanism,
        "ranked": ranked,
        "holdout_scores": holdout,
        "holdout_trades": trades,
        "best_holdout": max((h for h in holdout if h == h), default=float("nan")),
        "discriminates": len({round(h, 6) for h in holdout if h == h}) > 1,
        "names": [s.get("name") for s, _, _ in ranked],
    }


# ── Statistics ───────────────────────────────────────────────────────────────

def wilcoxon(x: list, y: list) -> tuple:
    """Wilcoxon signed-rank on paired samples. Returns (statistic, p, n)."""
    try:
        from scipy.stats import wilcoxon as _w
    except ImportError:
        return (None, None, 0)
    pairs = [(a, b) for a, b in zip(x, y) if a == a and b == b and a != b]
    if len(pairs) < 3:
        return (None, None, len(pairs))
    try:
        r = _w([p[0] for p in pairs], [p[1] for p in pairs])
        return (round(float(r.statistic), 3), round(float(r.pvalue), 4), len(pairs))
    except ValueError:
        return (None, None, len(pairs))


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    scen_defs = [(s, r, m) for s in SYMBOLS for r, m in CONTEXTS]
    n_calls = len(scen_defs) * len(ARMS) * args.repeats

    print(f"Scenarios: {len(scen_defs)}  arms: {len(ARMS)}  repeats: {args.repeats}")
    print(f"LLM calls: {n_calls}")
    if args.dry_run:
        est = n_calls * (15000 / 1e6 * 3.00 + 700 / 1e6 * 15.00)
        print(f"Estimated cost: ~${est:.2f}  (dry run - nothing spent)")
        for s, r, m in scen_defs:
            print(f"  {s:<10} {r:<15} {m}")
        return

    print("\nBuilding train/holdout split...")
    train_db, hold_db = build_split(args.db)

    print("\nPreparing scenarios (deterministic, no API cost)...")
    scenarios = []
    for s, r, m in scen_defs:
        print(f"  {s} / {r} / {m}")
        sc = prepare_scenario(s, r, m, train_db, hold_db)
        if sc:
            print(f"    survivors: {sc['names']}")
            print(f"    holdout PF: {[round(h,3) for h in sc['holdout_scores']]}  "
                  f"trades: {sc['holdout_trades']}  "
                  f"discriminates: {sc['discriminates']}")
            scenarios.append(sc)
    if not scenarios:
        print("No usable scenarios. Stopping.")
        return

    client = ClaudeClient(db_path=args.db)
    rows = []
    print(f"\nRunning {len(scenarios) * len(ARMS) * args.repeats} selector calls...")
    for sc in scenarios:
        sid = f"{sc['symbol']}|{sc['regime']}|{sc['mechanism']}"
        for arm in ARMS:
            kb_context = build_kb_context(arm, sc["regime"], sc["mechanism"], args.db)
            for rep in range(1, args.repeats + 1):
                prompt = _build_system_prompt(kb_context, sc["ranked"], sc["regime"])
                try:
                    text, _, _ = client.chat(
                        messages=[{"role": "user", "content": (
                            "Review the empirically-tested candidates below and select "
                            "the best one. Respond with the JSON selection object only."
                        )}],
                        tools=[], system_prompt=prompt,
                        thinking_budget=CLAUDE_THINKING_BUDGET_STRATEGY,
                        agent_name="strategy_agent",
                    )
                    idx = _parse_selection(text, len(sc["ranked"]))
                    err = ""
                except Exception as e:
                    # Do NOT fall back to index 0 here. _parse_selection already
                    # defaults to 0, so a failed call recorded as idx=0 is
                    # indistinguishable from a genuine choice of 0 and would
                    # silently fabricate data. Record it as invalid instead.
                    idx, err = None, str(e)[:150]
                if idx is None:
                    print(f"  {sid:<38} {arm}  rep{rep}  -> FAILED ({err[:60]})")
                    rows.append({
                        "scenario": sid, "symbol": sc["symbol"], "regime": sc["regime"],
                        "mechanism": sc["mechanism"], "arm": arm, "repeat": rep,
                        "kb_entries": len(kb_context), "chosen_idx": "",
                        "chosen_name": "", "holdout_score": "",
                        "holdout_trades": "", "best_holdout": round(sc["best_holdout"], 6),
                        "regret": "", "error": err,
                    })
                    continue
                hs = sc["holdout_scores"][idx]
                regret = sc["best_holdout"] - hs if hs == hs else float("nan")
                print(f"  {sid:<38} {arm}  rep{rep}  -> #{idx} "
                      f"{sc['names'][idx][:26]:<28} holdout={hs:.4f}")
                rows.append({
                    "scenario": sid, "symbol": sc["symbol"], "regime": sc["regime"],
                    "mechanism": sc["mechanism"], "arm": arm, "repeat": rep,
                    "kb_entries": len(kb_context), "chosen_idx": idx,
                    "chosen_name": sc["names"][idx],
                    "holdout_score": round(hs, 6) if hs == hs else "",
                    "holdout_trades": sc["holdout_trades"][idx],
                    "best_holdout": round(sc["best_holdout"], 6),
                    "regret": round(regret, 6) if regret == regret else "",
                    "error": err,
                })

    matrix = OUT_DIR / "memory_outcome_matrix.csv"
    with matrix.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    # ── Per-arm aggregates ───────────────────────────────────────────────────
    by_arm = defaultdict(list)
    for r in rows:
        if r["holdout_score"] != "":
            by_arm[r["arm"]].append(r)

    summary = {}
    for arm in ARMS:
        rs = by_arm.get(arm, [])
        if not rs:
            continue
        hs = [r["holdout_score"] for r in rs]
        rg = [r["regret"] for r in rs if r["regret"] != ""]
        # how often it picked the genuinely best survivor on holdout
        optimal = sum(1 for r in rs if abs(r["regret"]) < 1e-9) if rg else 0
        summary[arm] = {
            "n": len(rs),
            "mean_holdout_score": round(sum(hs) / len(hs), 4),
            "mean_regret": round(sum(rg) / len(rg), 4) if rg else None,
            "picked_best_pct": round(optimal / len(rs), 3),
            "mean_kb_entries": round(
                sum(r["kb_entries"] for r in rs) / len(rs), 1),
        }

    # Paired-by-scenario means, for Wilcoxon.
    paired = defaultdict(dict)
    for arm in ARMS:
        for sid in {r["scenario"] for r in rows}:
            v = [r["holdout_score"] for r in by_arm.get(arm, [])
                 if r["scenario"] == sid and r["holdout_score"] != ""]
            if v:
                paired[arm][sid] = sum(v) / len(v)

    sids = sorted(set.intersection(*[set(paired[a]) for a in ARMS if paired[a]])) \
        if all(paired.get(a) for a in ARMS) else []
    tests = {}
    for a1, a2 in combinations(ARMS, 2):
        if not sids:
            continue
        stat, p, n = wilcoxon([paired[a1][s] for s in sids],
                              [paired[a2][s] for s in sids])
        tests[f"{a1}_vs_{a2}"] = {"statistic": stat, "p_value": p, "n_pairs": n}

    (OUT_DIR / "memory_outcome_summary.json").write_text(json.dumps(
        {"summary": summary, "wilcoxon": tests,
         "paired_means_by_scenario": {a: paired[a] for a in ARMS},
         "split": {"train_end": "2025-10-01", "holdout": "2025-10-01..2026-04-11"},
         "repeats": args.repeats}, indent=2))

    print()
    print(f"  {'arm':<5}{'n':>4}{'mean holdout':>15}{'mean regret':>14}"
          f"{'picked best':>13}{'kb entries':>12}")
    for arm in ARMS:
        s = summary.get(arm)
        if not s:
            continue
        print(f"  {arm:<5}{s['n']:>4}{s['mean_holdout_score']:>15.4f}"
              f"{(s['mean_regret'] if s['mean_regret'] is not None else 0):>14.4f}"
              f"{s['picked_best_pct']*100:>12.0f}%{s['mean_kb_entries']:>12.1f}")

    if tests:
        print("\n  Wilcoxon signed-rank (paired by scenario, holdout score):")
        for k, v in tests.items():
            print(f"    {k:<10} p={v['p_value']}  n={v['n_pairs']}")

    print(f"\nWrote {matrix.name} and memory_outcome_summary.json to trials_out/")


if __name__ == "__main__":
    main()
