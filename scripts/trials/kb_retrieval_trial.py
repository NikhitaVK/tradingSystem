"""
kb_retrieval_trial.py — Offline retrieval-ranking bake-off for the KB / layered memory.

Trial doc: claude_docs/trials/2026-08-07-kb-retrieval-ranking.md

Scores the SAME frozen KB corpus under several ranking formulas and reports, per
labelled query scenario, where the gold-standard finding lands in the ranking.
Lower rank = better. Emits a CSV and a dependency-free SVG paired dot plot.

Why not "trade success": the DB holds 2 trades, 0 resolved win/loss. Retrieval rank
is the only KB outcome measurable today. See the trial doc for the full argument.

Usage:
    # 1. Emit a labelling template from real decision contexts
    python -m scripts.trials.kb_retrieval_trial template --out trials_out/scenarios.csv

    # 2. Hand-label the gold column, then run the bake-off
    python -m scripts.trials.kb_retrieval_trial run --scenarios trials_out/scenarios.csv

All variants score an identical corpus at an identical pinned timestamp, so runs are
reproducible. Nothing here writes to the knowledge_base table.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import time
from pathlib import Path

from config.settings import COGNITIVE_SPAN_K, DB_PATH
from src.data.memory_layers import LAYER_CONFIG

# Boost factors as implemented in knowledge_base.get_working_memory().
REGIME_BOOST = 1.5
MECHANISM_BOOST = 1.3


# ── Corpus loading ───────────────────────────────────────────────────────────

def load_corpus(db_path: str) -> list[dict]:
    """Read the whole KB once. All variants score this identical snapshot."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, category, strategy_id, content, created_at,
                   COALESCE(regime, 'unknown')    AS regime,
                   COALESCE(mechanism, 'unknown') AS mechanism,
                   COALESCE(layer, 'shallow')     AS layer,
                   COALESCE(importance, 50)       AS importance
            FROM knowledge_base
            """
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def effective_layer(entry: dict) -> str:
    """
    The layer the entry WOULD have under memory_layers.assign_layer_and_importance.

    Needed because every existing row carries layer='shallow' from the ALTER TABLE
    backfill default rather than from a real classification. See gap G5 in _trials.md.
    """
    if entry["category"] == "failure_diagnosis":
        return "deep"
    if entry["category"] == "parameter_insight":
        return "intermediate"
    return "shallow"


# ── Scoring primitives (pinned clock — the shipped ones call time.time()) ────

def _age_days(created_at_ms: int, now_ms: float) -> float:
    return (now_ms - created_at_ms) / 86_400_000.0


def _recency(created_at_ms: int, layer: str, now_ms: float) -> float:
    q = LAYER_CONFIG.get(layer, LAYER_CONFIG["shallow"])["q"]
    return math.exp(-_age_days(created_at_ms, now_ms) / q)


def _decayed_importance(importance: int, created_at_ms: int, layer: str, now_ms: float) -> float:
    alpha = LAYER_CONFIG.get(layer, LAYER_CONFIG["shallow"])["alpha"]
    return importance * (alpha ** _age_days(created_at_ms, now_ms))


def _boosts(entry: dict, scenario: dict, enabled: bool) -> float:
    if not enabled:
        return 1.0
    factor = 1.0
    if scenario.get("regime") and entry["regime"] == scenario["regime"]:
        factor *= REGIME_BOOST
    if scenario.get("mechanism") and entry["mechanism"] == scenario["mechanism"]:
        factor *= MECHANISM_BOOST
    return factor


# ── Variants — each differs from BASELINE in exactly one way (ablation rule) ─

def score_baseline(entry, scenario, now_ms, layer):
    """A — current get_working_memory(): stored importance x recency, with boosts."""
    return entry["importance"] * _recency(entry["created_at"], layer, now_ms) \
        * _boosts(entry, scenario, True)


def score_finmem(entry, scenario, now_ms, layer):
    """B (T1) — FinMem canonical compound: recency + decayed_importance/100."""
    recency = _recency(entry["created_at"], layer, now_ms)
    imp = _decayed_importance(entry["importance"], entry["created_at"], layer, now_ms)
    return (recency + imp / 100.0) * _boosts(entry, scenario, True)


def score_no_boosts(entry, scenario, now_ms, layer):
    """D — baseline with the x1.5 / x1.3 context boosts removed."""
    return entry["importance"] * _recency(entry["created_at"], layer, now_ms)


def score_double_decay(entry, scenario, now_ms, layer):
    """C' (T2) — the genuine double-decay arm: BOTH alpha and Q applied."""
    imp = _decayed_importance(entry["importance"], entry["created_at"], layer, now_ms)
    return imp * _recency(entry["created_at"], layer, now_ms) \
        * _boosts(entry, scenario, True)


VARIANTS = {
    "A_baseline":     score_baseline,
    "B_finmem":       score_finmem,
    "C_double_decay": score_double_decay,
    "D_no_boosts":    score_no_boosts,
}


# ── Ranking + metrics ────────────────────────────────────────────────────────

def rank_corpus(corpus, scenario, score_fn, now_ms, use_effective_layer):
    """Return corpus ids ordered best-first under score_fn."""
    scored = []
    for entry in corpus:
        layer = effective_layer(entry) if use_effective_layer else entry["layer"]
        scored.append((score_fn(entry, scenario, now_ms, layer), entry["id"]))
    # Tie-break on id so ordering is deterministic across variants.
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [entry_id for _, entry_id in scored]


def evaluate(ranking: list[int], gold: set[int], k: int) -> dict:
    """rank_of_first_gold is 1-indexed; None when no gold entry exists."""
    if not gold:
        return {"rank_first_gold": None, "recall_at_k": None, "reciprocal_rank": None}
    positions = [i + 1 for i, entry_id in enumerate(ranking) if entry_id in gold]
    first = positions[0] if positions else None
    hits_in_k = len([p for p in positions if p <= k])
    return {
        "rank_first_gold": first,
        "recall_at_k": hits_in_k / len(gold),
        "reciprocal_rank": (1.0 / first) if first else 0.0,
    }


# ── Scenario handling ────────────────────────────────────────────────────────

def emit_template(db_path: str, out_path: Path, limit: int = 15) -> int:
    """
    Build a labelling template from real Loop 1 decision points.

    Each row is a moment the system actually had to retrieve context. You fill in
    `gold_ids` — the KB entry ids a human judges genuinely relevant. That human
    judgement is the ground truth the whole trial rests on; it cannot be automated
    without circularity (using the ranker to grade the ranker).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, attempt_b, strategy_id, spec_delta, outcome, diagnosis
            FROM strategy_evolutions
            WHERE diagnosis IS NOT NULL AND TRIM(diagnosis) != ''
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["scenario_id", "regime", "mechanism", "outcome", "context", "gold_ids"])
        for r in rows:
            d = dict(r)
            # mechanism isn't its own column — it lives inside the spec_delta JSON.
            mechanism = ""
            try:
                delta = json.loads(d.get("spec_delta") or "{}")
                mechanism = (delta.get("mechanism") or {}).get("to", "") or ""
            except (ValueError, AttributeError):
                pass
            context = " ".join((d.get("diagnosis") or "").split())[:300]
            writer.writerow([
                f"S{d['id']}", "", mechanism, d.get("outcome") or "", context, "",
            ])
    return len(rows)


def load_scenarios(path: Path) -> list[dict]:
    scenarios = []
    with path.open(newline="") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("gold_ids") or "").strip()
            gold = {int(x) for x in raw.replace(";", ",").split(",") if x.strip().isdigit()}
            scenarios.append({
                "scenario_id": row["scenario_id"],
                "regime": (row.get("regime") or "").strip(),
                "mechanism": (row.get("mechanism") or "").strip(),
                "context": row.get("context") or "",
                "gold": gold,
            })
    return scenarios


# ── Dependency-free SVG paired dot plot ──────────────────────────────────────

_COLOURS = {
    "A_baseline":     "#4C6EF5",
    "B_finmem":       "#F76707",
    "C_double_decay": "#37B24D",
    "D_no_boosts":    "#AE3EC9",
}


def render_dotplot(results: dict, scenarios: list[dict], corpus_size: int, out_path: Path) -> None:
    """
    One row per scenario. x = rank of the first gold finding (log-ish linear scale),
    a connector line spans the variants so movement is legible at a glance.
    Left is better.
    """
    labelled = [s for s in scenarios if s["gold"]]
    if not labelled:
        return

    row_h, pad_l, pad_r, pad_t, pad_b = 26, 130, 40, 60, 70
    plot_w = 620
    height = pad_t + row_h * len(labelled) + pad_b
    width = pad_l + plot_w + pad_r
    max_rank = corpus_size

    def x_of(rank: int) -> float:
        return pad_l + (rank - 1) / max(1, max_rank - 1) * plot_w

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui,sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{pad_l}" y="26" font-size="15" font-weight="600" fill="#111">'
        f'KB retrieval — rank of first relevant finding</text>',
        f'<text x="{pad_l}" y="44" font-size="11" fill="#666">'
        f'lower is better · corpus n={corpus_size} · k={COGNITIVE_SPAN_K}</text>',
    ]

    # Gridlines + axis ticks.
    for rank in [1, max_rank // 4, max_rank // 2, 3 * max_rank // 4, max_rank]:
        if rank < 1:
            continue
        x = x_of(rank)
        parts.append(
            f'<line x1="{x:.1f}" y1="{pad_t - 8}" x2="{x:.1f}" '
            f'y2="{pad_t + row_h * len(labelled)}" stroke="#e9ecef" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x:.1f}" y="{pad_t + row_h * len(labelled) + 16}" font-size="10" '
            f'fill="#868e96" text-anchor="middle">{rank}</text>'
        )

    # Shade the top-k band — inside it means the finding actually reached the prompt.
    parts.insert(2, (
        f'<rect x="{pad_l}" y="{pad_t - 8}" width="{x_of(COGNITIVE_SPAN_K) - pad_l:.1f}" '
        f'height="{row_h * len(labelled) + 8}" fill="#d3f9d8" opacity="0.55"/>'
    ))

    for i, scenario in enumerate(labelled):
        y = pad_t + i * row_h + row_h / 2
        parts.append(
            f'<text x="{pad_l - 10}" y="{y + 4}" font-size="11" fill="#343a40" '
            f'text-anchor="end">{scenario["scenario_id"]}</text>'
        )
        ranks = []
        for name in VARIANTS:
            metric = results[name][scenario["scenario_id"]]["rank_first_gold"]
            if metric:
                ranks.append((name, metric))
        if len(ranks) > 1:
            xs = [x_of(r) for _, r in ranks]
            parts.append(
                f'<line x1="{min(xs):.1f}" y1="{y:.1f}" x2="{max(xs):.1f}" y2="{y:.1f}" '
                f'stroke="#ced4da" stroke-width="1.5"/>'
            )
        for name, rank in ranks:
            parts.append(
                f'<circle cx="{x_of(rank):.1f}" cy="{y:.1f}" r="5" '
                f'fill="{_COLOURS[name]}" opacity="0.85"><title>{name}: rank {rank}</title></circle>'
            )

    # Legend.
    lx = pad_l
    ly = height - 30
    for name in VARIANTS:
        parts.append(f'<circle cx="{lx + 5}" cy="{ly - 4}" r="5" fill="{_COLOURS[name]}"/>')
        parts.append(f'<text x="{lx + 16}" y="{ly}" font-size="11" fill="#343a40">{name}</text>')
        lx += 20 + 8 * len(name)

    parts.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts))


# ── Entry points ─────────────────────────────────────────────────────────────

def run_trial(db_path: str, scenarios_path: Path, out_dir: Path, use_effective_layer: bool) -> None:
    corpus = load_corpus(db_path)
    scenarios = load_scenarios(scenarios_path)
    labelled = [s for s in scenarios if s["gold"]]
    now_ms = time.time() * 1000  # pinned once — every variant sees the same clock

    if not labelled:
        print(f"No labelled scenarios in {scenarios_path} — fill in the gold_ids column first.")
        return

    results = {name: {} for name in VARIANTS}
    for name, score_fn in VARIANTS.items():
        for scenario in labelled:
            ranking = rank_corpus(corpus, scenario, score_fn, now_ms, use_effective_layer)
            results[name][scenario["scenario_id"]] = evaluate(
                ranking, scenario["gold"], COGNITIVE_SPAN_K
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "kb_retrieval_results.csv"
    with csv_path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["scenario_id", "variant", "rank_first_gold", "recall_at_k", "reciprocal_rank"])
        for name in VARIANTS:
            for scenario in labelled:
                m = results[name][scenario["scenario_id"]]
                writer.writerow([
                    scenario["scenario_id"], name, m["rank_first_gold"],
                    round(m["recall_at_k"], 4) if m["recall_at_k"] is not None else "",
                    round(m["reciprocal_rank"], 4) if m["reciprocal_rank"] is not None else "",
                ])

    svg_path = out_dir / "kb_retrieval_dotplot.svg"
    render_dotplot(results, scenarios, len(corpus), svg_path)

    print(f"corpus={len(corpus)} entries · scenarios labelled={len(labelled)} · k={COGNITIVE_SPAN_K}")
    print(f"layer source: {'category-derived (effective)' if use_effective_layer else 'as stored in DB'}\n")
    print(f"{'variant':<16} {'MRR':>7} {'Recall@k':>9} {'median rank':>12}")
    summary = {}
    for name in VARIANTS:
        ms = [results[name][s["scenario_id"]] for s in labelled]
        mrr = sum(m["reciprocal_rank"] for m in ms) / len(ms)
        recall = sum(m["recall_at_k"] for m in ms) / len(ms)
        ranks = sorted(m["rank_first_gold"] for m in ms if m["rank_first_gold"])
        median = ranks[len(ranks) // 2] if ranks else None
        summary[name] = {"mrr": mrr, "recall_at_k": recall, "median_rank": median}
        print(f"{name:<16} {mrr:>7.3f} {recall:>9.3f} {str(median):>12}")

    (out_dir / "kb_retrieval_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nWrote {csv_path}\n      {svg_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_tpl = sub.add_parser("template", help="emit a gold-labelling template CSV")
    p_tpl.add_argument("--out", type=Path, default=Path("trials_out/scenarios.csv"))
    p_tpl.add_argument("--limit", type=int, default=15)
    p_tpl.add_argument("--db", default=DB_PATH)

    p_run = sub.add_parser("run", help="run the variant bake-off")
    p_run.add_argument("--scenarios", type=Path, default=Path("trials_out/scenarios.csv"))
    p_run.add_argument("--out-dir", type=Path, default=Path("trials_out"))
    p_run.add_argument("--db", default=DB_PATH)
    p_run.add_argument(
        "--stored-layer", action="store_true",
        help="rank using the layer stored in the DB instead of the category-derived one "
             "(shows the damage from the backfill mislabel — see gap G5)",
    )

    args = parser.parse_args()
    if args.cmd == "template":
        n = emit_template(args.db, args.out, args.limit)
        print(f"Wrote {n} scenarios to {args.out} — fill in the gold_ids column, then run.")
    else:
        run_trial(args.db, args.scenarios, args.out_dir, use_effective_layer=not args.stored_layer)


if __name__ == "__main__":
    main()
