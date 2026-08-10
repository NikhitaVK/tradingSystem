"""
backfill_kb_from_search_logs.py — Recover empirical findings stranded in reasoning_logs.

Every Loop 1 attempt backtests CANDIDATE_POOL_SIZE candidates. `empirical_search`
writes all 12 results to `reasoning_logs.thinking` as an aggregate row, and Loop 1
then writes exactly ONE knowledge_base entry per attempt — the analyst's rejection
diagnosis for the single candidate that was selected. The other 11 empirical
results per attempt were never written to the KB.

This script recovers them. Findings are aggregated per distinct candidate name
rather than written one row per evaluation: the same candidate recurs across many
attempts, so one row per evaluation would add ~600 near-duplicate entries and make
bundle redundancy worse (measured mean pairwise similarity is already 0.22).
One aggregated finding per strategy is higher-signal.

Category is `parameter_insight` — these are empirical results about specific
indicator/parameter configurations, which is exactly what that category is for.
It also maps to the `intermediate` layer, currently empty in the live KB.

Usage:
    python -m scripts.backfill_kb_from_search_logs            # dry run (default)
    python -m scripts.backfill_kb_from_search_logs --apply    # write to the KB
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

from config.settings import DB_PATH
from src.data.knowledge_base import write_finding

# Marker embedded in generated content so the backfill is idempotent and the
# recovered rows stay auditable/separable from organically-written findings.
MARKER = "[backfilled from empirical_search logs]"

_MECHANISM_HINTS = (
    ("mean_reversion", ("meanrevers", "oversold", "fade", "reversion")),
    ("breakout",       ("breakout", "squeeze", "release", "band")),
    ("volatility",     ("atr", "volatility", "expansion")),
    ("momentum",       ("momentum", "macd", "crossover", "cross", "trend", "adx", "ema")),
)


def infer_mechanism(name: str) -> str:
    lowered = name.lower().replace("_", "")
    for mechanism, hints in _MECHANISM_HINTS:
        if any(h in lowered for h in hints):
            return mechanism
    return "unknown"


def collect(db_path: str) -> tuple[dict, int, int]:
    """Read every empirical_search log and group candidate results by name."""
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT thinking, created_at FROM reasoning_logs "
            "WHERE agent = 'empirical_search' AND thinking IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    by_name: dict[str, dict] = {}
    total_evals = 0
    skipped_logs = 0

    for thinking, created_at in rows:
        try:
            candidates = json.loads(thinking)
        except (ValueError, TypeError):
            skipped_logs += 1
            continue
        if not isinstance(candidates, list):
            skipped_logs += 1
            continue

        for cand in candidates:
            if not isinstance(cand, dict) or "name" not in cand:
                continue
            name = cand["name"]
            total_evals += 1
            entry = by_name.setdefault(
                name, {"sharpes": [], "trades": [], "early_terms": 0, "first_seen": created_at}
            )
            entry["sharpes"].append(float(cand.get("sharpe", 0.0) or 0.0))
            entry["trades"].append(int(cand.get("trades", 0) or 0))
            if cand.get("early_term"):
                entry["early_terms"] += 1
            entry["first_seen"] = min(entry["first_seen"], created_at)

    return by_name, total_evals, skipped_logs


def build_content(name: str, stats: dict) -> str:
    n = len(stats["sharpes"])
    mean_sharpe = statistics.mean(stats["sharpes"])
    mean_trades = statistics.mean(stats["trades"])
    early_rate = stats["early_terms"] / n
    verdict = (
        "never viable in any tested attempt" if mean_sharpe < 0 and early_rate == 1.0
        else "consistently weak" if mean_sharpe < 0
        else "mixed"
    )
    spread = (
        f"sharpe range {min(stats['sharpes']):.2f} to {max(stats['sharpes']):.2f}"
        if n > 1 else "single evaluation"
    )
    return (
        f"Candidate '{name}' evaluated {n}x by empirical search: "
        f"mean Sharpe {mean_sharpe:.2f}, mean {mean_trades:.0f} trades per run, "
        f"early-terminated in {early_rate:.0%} of runs ({spread}). "
        f"Empirical verdict: {verdict}. "
        f"Mechanism class: {infer_mechanism(name)}. {MARKER}"
    )


def already_backfilled(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM knowledge_base WHERE content LIKE ?", (f"%{MARKER}%",)
        ).fetchone()[0]
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--apply", action="store_true", help="write to the KB (default: dry run)")
    args = ap.parse_args()

    existing = already_backfilled(args.db)
    if existing:
        print(f"Already backfilled: {existing} rows carry the marker. Nothing to do.")
        return

    by_name, total_evals, skipped = collect(args.db)
    print(f"empirical_search logs parsed   : {total_evals} candidate evaluations"
          f"{f' ({skipped} unparseable logs skipped)' if skipped else ''}")
    print(f"distinct candidates            : {len(by_name)}")

    mechanisms: dict[str, int] = {}
    for name in by_name:
        m = infer_mechanism(name)
        mechanisms[m] = mechanisms.get(m, 0) + 1
    print(f"mechanism spread               : {mechanisms}")

    if not args.apply:
        print("\n--- DRY RUN — sample of 3 findings that would be written ---")
        for name, stats in list(by_name.items())[:3]:
            print(f"\n  {build_content(name, stats)}")
        print(f"\nRe-run with --apply to write {len(by_name)} findings.")
        return

    backup = Path("backups") / f"trading_system.pre-backfill.{datetime.now():%Y%m%d-%H%M%S}.db"
    backup.parent.mkdir(exist_ok=True)
    shutil.copy2(args.db, backup)
    print(f"\nBackup written to {backup}")

    # Recovered findings must carry the timestamp of the EVIDENCE, not of the
    # backfill run. write_finding() stamps created_at = now; stamping today would
    # give four-month-old empirical results the same recency weight as a finding
    # written this morning, systematically over-ranking stale evidence.
    # Two passes, deliberately. write_finding() opens and commits its own
    # connection; holding a second write connection open across the loop
    # deadlocks SQLite. Collect the ids first, then re-date them in one pass.
    stamps: list[tuple[int, int]] = []
    for name, stats in by_name.items():
        row_id = write_finding(
            category="parameter_insight",
            content=build_content(name, stats),
            db_path=args.db,
            mechanism=infer_mechanism(name),
        )
        stamps.append((stats["first_seen"], row_id))

    conn = sqlite3.connect(args.db)
    try:
        with conn:
            conn.executemany(
                "UPDATE knowledge_base SET created_at = ? WHERE id = ?", stamps
            )
    finally:
        conn.close()
    written = len(stamps)

    print(f"Wrote {written} findings to knowledge_base.")
    conn = sqlite3.connect(args.db)
    try:
        total = conn.execute("SELECT COUNT(*) FROM knowledge_base").fetchone()[0]
        layers = conn.execute(
            "SELECT COALESCE(layer,'NULL'), COUNT(*) FROM knowledge_base GROUP BY 1"
        ).fetchall()
    finally:
        conn.close()
    print(f"KB now holds {total} entries; layers: {dict(layers)}")


if __name__ == "__main__":
    main()
