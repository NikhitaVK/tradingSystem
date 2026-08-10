"""
kb_structure_trial.py — Measured re-run of Component Trial #4 (Knowledge-Base Structure).

Trial doc: claude_docs/trials/2026-08-07-kb-structure-measured.md

Compares the three KB retrieval designs that were originally scored qualitatively:

    A  flat keyword          query_relevant(keywords)                  — LIKE + recency
    B  regime-aware          query_relevant(keywords, regime=...)      — A + strict regime filter
    C  layered memory        get_working_memory(regime, mechanism)     — compound score + layers
    C2 layered + semantic    query_relevant(..., query_context=...)    — C + Haiku rerank (COSTS API)

Every metric here is label-free and objective, so the trial can be conducted and
reproduced without a hand-labelled relevance set. Relevance ranking itself still needs
human gold labels — that arm lives in kb_retrieval_trial.py and is reported as pending.

C2 is OFF by default because it spends Anthropic API credit. Enable with --with-semantic.

Usage:
    python -m scripts.trials.kb_structure_trial run
    python -m scripts.trials.kb_structure_trial run --with-semantic
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
import time
from pathlib import Path

from config.settings import COGNITIVE_SPAN_K, DB_PATH
from src.data.knowledge_base import get_working_memory, query_relevant
from src.data.schema import init_db

# Bundle size held constant across options so comparisons are like-for-like.
# C returns k per layer across 3 layers, so A/B get the same total ceiling.
K = COGNITIVE_SPAN_K
BUNDLE_LIMIT = K * 3

_WORD = re.compile(r"[a-z0-9]+")


# ── Options under trial ──────────────────────────────────────────────────────

def option_a(db_path, scenario, _semantic):
    """A — flat keyword: LIKE across content, ordered by recency. No market context."""
    return query_relevant(scenario["keywords"], db_path, limit=BUNDLE_LIMIT)


def option_b(db_path, scenario, _semantic):
    """B — regime-aware: A plus a strict filter to the current market regime."""
    return query_relevant(
        scenario["keywords"], db_path, limit=BUNDLE_LIMIT, regime=scenario["regime"]
    )


def option_c(db_path, scenario, _semantic):
    """C — layered memory: compound score (importance x recency x layer decay) + boosts."""
    memory = get_working_memory(
        db_path,
        current_regime=scenario["regime"],
        mechanism=scenario["mechanism"],
        k_per_layer=K,
    )
    bundle = []
    for layer_entries in memory["layers"].values():
        bundle.extend(layer_entries)
    bundle.sort(key=lambda e: e.get("_layer_score", 0), reverse=True)
    return bundle


def option_c2(db_path, scenario, semantic_enabled):
    """C2 — C plus Claude Haiku semantic reranking. Spends API credit."""
    if not semantic_enabled:
        return None
    return query_relevant(
        scenario["keywords"], db_path, limit=BUNDLE_LIMIT,
        regime=scenario["regime"], query_context=scenario["context"],
    )


OPTIONS = {
    "A_flat_keyword": option_a,
    "B_regime_aware": option_b,
    "C_layered": option_c,
    "C2_layered_semantic": option_c2,
}


# ── Label-free metrics, each tied to an implication row ──────────────────────

def _tokens(text: str) -> set:
    return set(_WORD.findall((text or "").lower()))


def _redundancy(bundle: list) -> float:
    """
    Functionality / signal-vs-noise. The pre-trial matrix claimed A "can flood the prompt
    with near-duplicates".

    A threshold count is useless here — max pairwise Jaccard across the whole corpus is
    0.714, so no pair clears a 0.8 "near-duplicate" bar. Mean pairwise Jaccard within the
    bundle is the honest continuous version: how much the retrieved findings repeat each
    other. Lower = the agent sees more distinct lessons per token spent.
    """
    if len(bundle) < 2:
        return 0.0
    toks = [_tokens(e.get("content", "")) for e in bundle]
    sims = []
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            union = toks[i] | toks[j]
            if union:
                sims.append(len(toks[i] & toks[j]) / len(union))
    return statistics.mean(sims) if sims else 0.0


def _median_age_days(bundle: list, now_ms: float) -> float:
    """Functionality / staleness. Lower = fresher context reaching the agent."""
    if not bundle:
        return float("nan")
    return statistics.median((now_ms - e["created_at"]) / 86_400_000 for e in bundle)


def _bundle_tokens(bundle: list) -> int:
    """Usability / token budget. ~4 chars per token."""
    return sum(len(e.get("content", "")) for e in bundle) // 4


def _context_match_rate(bundle: list, scenario: dict) -> float:
    """
    Share of the bundle actually tagged with the scenario's regime.
    Descriptive only — B and C filter/boost on regime, so this is not a fair
    winner criterion for them. Reported to show what each option puts in front
    of the agent, not to score relevance.
    """
    if not bundle:
        return 0.0
    return sum(1 for e in bundle if e.get("regime") == scenario["regime"]) / len(bundle)


def measure(bundle: list, scenario: dict, now_ms: float, elapsed_ms: float) -> dict:
    return {
        "n_returned": len(bundle),
        "median_age_days": round(_median_age_days(bundle, now_ms), 1) if bundle else None,
        "redundancy": round(_redundancy(bundle), 4),
        "bundle_tokens": _bundle_tokens(bundle),
        "context_match_rate": round(_context_match_rate(bundle, scenario), 4),
        "latency_ms": round(elapsed_ms, 2),
    }


# ── Robustness suite (Functionality — graceful handling) ─────────────────────

def robustness_suite(db_path: str, semantic_enabled: bool) -> dict:
    """
    Straight from implications_planning.md §1: "Does it handle empty tables / blank input /
    missing data gracefully?" Each case must return a list without raising.
    """
    empty_db = Path("trials_out/_empty_kb.db")
    empty_db.parent.mkdir(parents=True, exist_ok=True)
    if empty_db.exists():
        empty_db.unlink()
    init_db(str(empty_db))

    cases = [
        ("empty knowledge base", str(empty_db),
         {"keywords": ["rsi"], "regime": "high_vol", "mechanism": "momentum", "context": "x"}),
        ("blank keyword list", db_path,
         {"keywords": [], "regime": "high_vol", "mechanism": "momentum", "context": "x"}),
        ("regime that does not exist", db_path,
         {"keywords": ["rsi"], "regime": "no_such_regime", "mechanism": "momentum", "context": "x"}),
        ("regime is None", db_path,
         {"keywords": ["rsi"], "regime": None, "mechanism": None, "context": "x"}),
        ("keyword matches nothing", db_path,
         {"keywords": ["zzzzqqq"], "regime": "high_vol", "mechanism": "momentum", "context": "x"}),
    ]

    results = {name: {} for name in OPTIONS}
    for case_name, case_db, scenario in cases:
        for opt_name, fn in OPTIONS.items():
            try:
                out = fn(case_db, scenario, semantic_enabled)
                if out is None:
                    results[opt_name][case_name] = "skipped"
                else:
                    results[opt_name][case_name] = f"ok ({len(out)} rows)"
            except Exception as exc:
                results[opt_name][case_name] = f"RAISED {type(exc).__name__}: {exc}"
    return results


# ── Scenarios ────────────────────────────────────────────────────────────────

def build_scenarios(db_path: str) -> list:
    """
    One scenario per (regime, mechanism) pair the KB actually contains — these are the
    real retry situations Loop 1 has been in. Keywords mimic what the strategy agent
    would search for in that situation.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT COALESCE(regime,'unknown') AS regime,
                   COALESCE(mechanism,'unknown') AS mechanism,
                   COUNT(*) AS n
            FROM knowledge_base
            WHERE regime IS NOT NULL AND LENGTH(regime) < 30
            GROUP BY 1, 2 HAVING n >= 1 ORDER BY n DESC
            """
        ).fetchall()
    finally:
        conn.close()

    keyword_sets = {
        "momentum": ["momentum", "macd", "crossover"],
        "mean_reversion": ["rsi", "oversold", "reversion"],
        "breakout": ["breakout", "bollinger", "volatility"],
        "unknown": ["strategy", "trades", "profit"],
    }
    scenarios = []
    for r in rows:
        d = dict(r)
        mech = d["mechanism"]
        scenarios.append({
            "scenario_id": f"{d['regime']}/{mech}",
            "regime": d["regime"],
            "mechanism": mech,
            "keywords": keyword_sets.get(mech, keyword_sets["unknown"]),
            "context": (
                f"About to retry a {mech} strategy in a {d['regime']} market. "
                f"What has already failed in these conditions?"
            ),
        })
    return scenarios


# ── Dot plot ─────────────────────────────────────────────────────────────────

_COLOURS = {
    "A_flat_keyword": "#E8590C",
    "B_regime_aware": "#1C7ED6",
    "C_layered": "#2F9E44",
    "C2_layered_semantic": "#9C36B5",
}

PANELS = [
    ("median_age_days", "Staleness — median age of retrieved findings (days)", "lower", "Functionality"),
    ("redundancy", "Signal vs noise — mean pairwise similarity within bundle", "lower", "Functionality"),
    ("bundle_tokens", "Token budget — size of context sent to the agent", "lower", "Usability"),
    ("n_returned", "Coverage — findings actually returned", "higher", "Functionality"),
]


def render_dotplots(per_scenario: dict, scenarios: list, out_path: Path, active: list) -> None:
    row_h, pad_l, plot_w, panel_gap = 22, 190, 480, 58
    n_rows = len(scenarios)
    panel_h = n_rows * row_h + panel_gap
    width = pad_l + plot_w + 205
    height = 74 + panel_h * len(PANELS) + 40

    p = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="system-ui,-apple-system,sans-serif">',
        f'<rect width="{width}" height="{height}" fill="#fff"/>',
        f'<text x="24" y="30" font-size="16" font-weight="700" fill="#111">'
        f'Component Trial #4 — KB structure, measured</text>',
        f'<text x="24" y="48" font-size="11" fill="#666">'
        f'one dot per option, one row per real (regime / mechanism) retry situation · '
        f'n={n_rows} scenarios · corpus=74 findings</text>',
    ]

    for pi, (metric, title, better, implication) in enumerate(PANELS):
        top = 84 + pi * panel_h
        vals = [
            per_scenario[o][s["scenario_id"]][metric]
            for o in active for s in scenarios
            if per_scenario[o][s["scenario_id"]].get(metric) is not None
        ]
        if not vals:
            continue
        lo, hi = min(vals), max(vals)
        if hi == lo:
            hi = lo + 1

        def x_of(v):
            return pad_l + (v - lo) / (hi - lo) * plot_w

        p.append(f'<text x="24" y="{top - 12}" font-size="12.5" font-weight="600" '
                 f'fill="#212529">{title}</text>')
        p.append(f'<text x="{pad_l + plot_w + 10}" y="{top - 12}" font-size="10" '
                 f'fill="#868e96">{implication} · {better} is better</text>')

        for i, s in enumerate(scenarios):
            y = top + i * row_h + row_h / 2
            p.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" y2="{y:.1f}" '
                     f'stroke="#f1f3f5" stroke-width="{row_h - 4}"/>')
            p.append(f'<text x="{pad_l - 8}" y="{y + 3.5}" font-size="10" fill="#495057" '
                     f'text-anchor="end">{s["scenario_id"]}</text>')
            pts = []
            for o in active:
                v = per_scenario[o][s["scenario_id"]].get(metric)
                if v is not None:
                    pts.append((o, v))
            if len(pts) > 1:
                xs = [x_of(v) for _, v in pts]
                p.append(f'<line x1="{min(xs):.1f}" y1="{y:.1f}" x2="{max(xs):.1f}" '
                         f'y2="{y:.1f}" stroke="#ced4da" stroke-width="1.2"/>')
            for o, v in pts:
                p.append(f'<circle cx="{x_of(v):.1f}" cy="{y:.1f}" r="5" fill="{_COLOURS[o]}" '
                         f'opacity="0.9"><title>{o} — {metric}={v}</title></circle>')

        yb = top + n_rows * row_h + 6
        for v in (lo, (lo + hi) / 2, hi):
            p.append(f'<text x="{x_of(v):.1f}" y="{yb + 10}" font-size="9.5" fill="#868e96" '
                     f'text-anchor="middle">{v:g}</text>')

    lx = 24
    ly = height - 14
    for o in active:
        p.append(f'<circle cx="{lx + 5}" cy="{ly - 4}" r="5" fill="{_COLOURS[o]}"/>')
        p.append(f'<text x="{lx + 15}" y="{ly}" font-size="11" fill="#343a40">{o}</text>')
        lx += 28 + 7 * len(o)

    p.append("</svg>")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(p))


# ── Runner ───────────────────────────────────────────────────────────────────

def run(db_path: str, out_dir: Path, semantic_enabled: bool) -> None:
    scenarios = build_scenarios(db_path)
    active = [o for o in OPTIONS if o != "C2_layered_semantic" or semantic_enabled]
    now_ms = time.time() * 1000

    per_scenario = {o: {} for o in OPTIONS}
    for opt_name in active:
        fn = OPTIONS[opt_name]
        for s in scenarios:
            t0 = time.perf_counter()
            bundle = fn(db_path, s, semantic_enabled) or []
            elapsed = (time.perf_counter() - t0) * 1000
            per_scenario[opt_name][s["scenario_id"]] = measure(bundle, s, now_ms, elapsed)

    out_dir.mkdir(parents=True, exist_ok=True)
    render_dotplots(per_scenario, scenarios, out_dir / "kb_structure_dotplot.svg", active)

    print(f"scenarios={len(scenarios)}  bundle_limit={BUNDLE_LIMIT}  k={K}")
    print(f"semantic arm (C2): {'ENABLED — spending API credit' if semantic_enabled else 'off'}\n")

    header = f"{'option':<22}" + "".join(f"{m:>20}" for m, _, _, _ in PANELS) + f"{'latency_ms':>12}"
    print(header)
    print("-" * len(header))
    summary = {}
    for opt_name in active:
        rows = [per_scenario[opt_name][s["scenario_id"]] for s in scenarios]
        agg = {}
        for metric, _, _, _ in PANELS:
            vals = [r[metric] for r in rows if r.get(metric) is not None]
            agg[metric] = round(statistics.mean(vals), 3) if vals else None
        lat = round(statistics.mean(r["latency_ms"] for r in rows), 2)
        agg["latency_ms"] = lat
        agg["context_match_rate"] = round(
            statistics.mean(r["context_match_rate"] for r in rows), 3
        )
        summary[opt_name] = agg
        print(f"{opt_name:<22}"
              + "".join(f"{str(agg[m]):>20}" for m, _, _, _ in PANELS)
              + f"{lat:>12}")

    print("\nRobustness — implications_planning.md §1 (empty / blank / missing input)")
    rob = robustness_suite(db_path, semantic_enabled)
    for opt_name in active:
        print(f"\n  {opt_name}")
        for case, outcome in rob[opt_name].items():
            mark = "FAIL" if "RAISED" in outcome else "pass"
            print(f"    [{mark}] {case:<32} {outcome}")

    (out_dir / "kb_structure_summary.json").write_text(
        json.dumps({"summary": summary, "robustness": rob,
                    "per_scenario": per_scenario, "scenarios":
                    [s["scenario_id"] for s in scenarios]}, indent=2)
    )
    print(f"\nWrote {out_dir/'kb_structure_dotplot.svg'}")
    print(f"      {out_dir/'kb_structure_summary.json'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--db", default=DB_PATH)
    r.add_argument("--out-dir", type=Path, default=Path("trials_out"))
    r.add_argument("--with-semantic", action="store_true",
                   help="enable the C2 Haiku arm (spends Anthropic API credit)")
    a = ap.parse_args()
    run(a.db, a.out_dir, a.with_semantic)


if __name__ == "__main__":
    main()
