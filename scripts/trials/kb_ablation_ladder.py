"""
kb_ablation_ladder.py — Trial 4, rebuilt as a one-change-at-a-time ablation.

Why this replaces the A/B/C comparison
--------------------------------------
The original trial compared flat keyword search (A) against regime filtering (B)
against the shipped layered design (C). Two problems with that:

  1. A is a strawman. Nobody proposed building it. "Our design beats the naive
     baseline" is confirmatory, not exploratory.
  2. A and C differ in FOUR ways at once (keyword vs scored retrieval, flat vs
     layered, no relevancy vs relevancy, hard vs soft matching). When C wins you
     cannot tell WHICH of those four earned the win. This breaks the repo's own
     rule in .claude/rules/testing/ablation_methodology.md: "one change per test
     run."

This trial fixes both. Each rung adds exactly ONE design element to the rung
below it, so every metric delta is attributable to one decision.

The ladder (all rungs score the same full corpus; only scoring/partitioning vary)
---------------------------------------------------------------------------------
    R0  recency only          S = recency (flat, single decay)
    R1  + layering            partition into shallow/intermediate/deep,
                              top-K per layer, per-layer decay horizon Q
    R2  + relevancy           S += regime 0.6 + mechanism 0.4
    R3  + importance          S += decayed_importance / 100   <- SHIPS TODAY
                              (this is FinMem's recency + relevancy + importance)

    K   keyword + recency     reported alongside as a reference point, NOT a rung
                              (this is the old "option A")

Decision rule, fixed BEFORE the run
-----------------------------------
Adopt the SIMPLEST rung not clearly beaten on redundancy and responsiveness.
Ties go to the simpler rung: a component must earn its complexity. If adding a
term changes nothing measurable, the honest conclusion is to remove it.

Metrics and what each means for the system
------------------------------------------
    redundancy      mean pairwise content overlap inside a bundle.
                    -> information density. 5 of 13 findings saying the same
                       thing means effective memory is far smaller than it looks.

    bundle_tokens   tokens the bundle adds to the strategy agent's prompt.
                    -> cost per Loop 1 attempt, and dilution risk: a decisive
                       finding buried in 10k tokens may as well not be there.

    responsiveness  mean Jaccard overlap between bundles for DIFFERENT scenarios.
                    -> is this memory at all? If the same findings come back
                       regardless of regime and mechanism, it is a fixed preamble,
                       not situational retrieval. LOWER = more responsive.

    corpus_reach    distinct entries ever surfaced / corpus size.
                    -> if only 20 of 126 entries are reachable, the other 106 are
                       dead weight and writing findings is largely wasted effort.

    purge_exposure  entries that purge_kb() would delete under this rung's layer
                    assignment.
                    -> safety. This is the exact bug that nearly wiped the KB:
                       under flat/shallow-default scoring, 72 of 126 entries were
                       one purge_kb() call from deletion.

    robustness      empty KB / unseen regime / no keyword match.
                    -> fail-safe vs fail-blind.

No API calls. Nothing here spends credit.

Usage:
    python -m scripts.trials.kb_ablation_ladder
"""
import argparse
import json
import re
import sqlite3
import statistics
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.settings import COGNITIVE_SPAN_K, DB_PATH  # noqa: E402
from src.data.memory_layers import (  # noqa: E402
    compute_recency_score,
    compute_importance_score,
    compute_structural_relevancy,
    should_purge,
)

OUT_DIR = Path(__file__).resolve().parents[2] / "trials_out"
K = COGNITIVE_SPAN_K
BUNDLE_LIMIT = K * 3          # 15 — same ceiling for every rung
_WORD = re.compile(r"[a-z0-9]+")

LAYERS = ["shallow", "intermediate", "deep"]

KEYWORDS = {
    "momentum": ["momentum", "macd", "crossover"],
    "mean_reversion": ["rsi", "oversold", "reversion"],
    "breakout": ["breakout", "bollinger", "volatility"],
    "unknown": ["strategy", "trades", "profit"],
}

# Fixed scenario set, identical for every rung, so bundles are comparable.
SCENARIOS = [
    {"id": "high_vol/momentum", "regime": "high_vol", "mechanism": "momentum"},
    {"id": "trending_bull/mean_reversion", "regime": "trending_bull",
     "mechanism": "mean_reversion"},
    {"id": "trending_bull/momentum", "regime": "trending_bull", "mechanism": "momentum"},
    {"id": "sideways/mean_reversion", "regime": "sideways", "mechanism": "mean_reversion"},
    {"id": "high_vol/mean_reversion", "regime": "high_vol", "mechanism": "mean_reversion"},
    {"id": "high_vol/breakout", "regime": "high_vol", "mechanism": "breakout"},
]


# ── Corpus ───────────────────────────────────────────────────────────────────

def load_corpus(db_path: str) -> list:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, content, category, regime, mechanism, created_at, "
        "COALESCE(layer,'shallow') AS layer, COALESCE(importance,50) AS importance "
        "FROM knowledge_base"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── The ladder: each rung adds exactly one term ──────────────────────────────

def s_recency(e, scen, flat: bool) -> float:
    """Flat rung uses one decay horizon for everything; layered uses the entry's own."""
    return compute_recency_score(e["created_at"], "shallow" if flat else e["layer"])


def rung_r0(corpus, scen):
    """R0 — recency only, flat. No layers, no relevancy, no importance."""
    scored = sorted(corpus, key=lambda e: -s_recency(e, scen, flat=True))
    return scored[:BUNDLE_LIMIT]


def _layered(corpus, scen, score_fn):
    """Shared partitioning for R1-R3: top-K from each layer."""
    out = []
    for layer in LAYERS:
        pool = [e for e in corpus if e["layer"] == layer]
        pool.sort(key=lambda e: -score_fn(e, scen))
        out.extend(pool[:K])
    return out


def rung_r1(corpus, scen):
    """R1 — R0 + layering (partition + per-layer decay horizon)."""
    return _layered(corpus, scen, lambda e, s: s_recency(e, s, flat=False))


def rung_r2(corpus, scen):
    """R2 — R1 + structural relevancy (regime 0.6 + mechanism 0.4)."""
    def score(e, s):
        return s_recency(e, s, flat=False) + compute_structural_relevancy(
            e, s["regime"], s["mechanism"])
    return _layered(corpus, scen, score)


def rung_r3(corpus, scen):
    """R3 — R2 + decayed importance. Full FinMem compound. SHIPS TODAY."""
    def score(e, s):
        return (
            s_recency(e, s, flat=False)
            + compute_structural_relevancy(e, s["regime"], s["mechanism"])
            + compute_importance_score(e["importance"], e["created_at"], e["layer"]) / 100
        )
    return _layered(corpus, scen, score)


def ref_keyword(corpus, scen):
    """K — keyword + recency. The old 'option A'. Reference point, not a rung."""
    kw = KEYWORDS.get(scen["mechanism"], KEYWORDS["unknown"])
    hits = [e for e in corpus
            if any(k in (e["content"] or "").lower() for k in kw)]
    hits.sort(key=lambda e: -e["created_at"])
    return hits[:BUNDLE_LIMIT]


RUNGS = [
    ("K_keyword_recency", ref_keyword, "reference — keyword + recency (old option A)"),
    ("R0_recency", rung_r0, "recency only, flat"),
    ("R1_layered", rung_r1, "+ layering"),
    ("R2_relevancy", rung_r2, "+ relevancy term"),
    ("R3_importance", rung_r3, "+ importance  [SHIPS]"),
]


# ── Metrics ──────────────────────────────────────────────────────────────────

def _toks(text):
    return set(_WORD.findall((text or "").lower()))


def redundancy(bundle) -> float:
    """Mean pairwise content overlap. Lower = more distinct lessons per token."""
    if len(bundle) < 2:
        return 0.0
    t = [_toks(e["content"]) for e in bundle]
    sims = [len(t[i] & t[j]) / len(t[i] | t[j])
            for i, j in combinations(range(len(t)), 2) if t[i] | t[j]]
    return statistics.mean(sims) if sims else 0.0


def bundle_tokens(bundle) -> int:
    return sum(len(e["content"] or "") for e in bundle) // 4


def responsiveness(bundles: dict) -> float:
    """
    Mean Jaccard overlap of bundle ID sets across DIFFERENT scenarios.
    Lower = the bundle actually changes with the situation.
    """
    sets = [{e["id"] for e in b} for b in bundles.values()]
    sims = [len(a & b) / len(a | b)
            for a, b in combinations(sets, 2) if a | b]
    return statistics.mean(sims) if sims else 0.0


def corpus_reach(bundles: dict, corpus_size: int) -> float:
    seen = set()
    for b in bundles.values():
        seen |= {e["id"] for e in b}
    return len(seen) / corpus_size if corpus_size else 0.0


def purge_exposure(corpus, flat: bool) -> int:
    """
    Entries purge_kb() would delete under this rung's layer assignment.
    A flat rung has no layer concept, so every entry decays on the shallow
    horizon (Q=14) -- which is exactly the state that nearly wiped the KB.
    """
    return sum(
        1 for e in corpus
        if should_purge(e["created_at"], "shallow" if flat else e["layer"],
                        e["importance"])
    )


def robustness(fn, corpus, flat_rung: bool) -> dict:
    """Empty corpus / unseen regime / no-match keywords. Must not raise."""
    cases = {
        "empty knowledge base": ([], {"id": "x", "regime": "high_vol",
                                      "mechanism": "momentum"}),
        "regime that does not exist": (corpus, {"id": "x", "regime": "no_such_regime",
                                                "mechanism": "momentum"}),
        "regime is None": (corpus, {"id": "x", "regime": None, "mechanism": None}),
        "mechanism unknown": (corpus, {"id": "x", "regime": "high_vol",
                                       "mechanism": "zzzz_no_such"}),
    }
    out = {}
    for name, (c, s) in cases.items():
        try:
            out[name] = f"ok ({len(fn(c, s))} rows)"
        except Exception as e:
            out[name] = f"RAISED {type(e).__name__}: {e}"
    return out


# ── Dot plot ─────────────────────────────────────────────────────────────────

def write_dotplot(summary: dict, path: Path) -> None:
    """
    One panel per metric. Real axis from 0 to the metric max, a labelled dot per
    rung, and an explicit better-direction arrow. Values are printed next to
    each dot because several rungs land almost on top of each other -- that
    near-overlap IS the result, so it must stay legible.
    """
    metrics = [
        ("redundancy",     "Redundancy  (mean pairwise overlap in a bundle)", False, "{:.4f}"),
        ("bundle_tokens",  "Bundle tokens  (added to the agent's prompt)",    False, "{:,.0f}"),
        ("responsiveness", "Responsiveness  (1.0 = same bundle every scenario)", False, "{:.4f}"),
        ("corpus_reach",   "Corpus reach  (share of the 126 entries reachable)", True, "{:.3f}"),
        ("purge_exposure", "Purge exposure  (entries at risk of deletion)",   False, "{:.0f}"),
    ]
    names = list(summary.keys())
    colour = {
        "K_keyword_recency": "#ADB5BD",
        "R0_recency":        "#E8590C",
        "R1_layered":        "#1C7ED6",
        "R2_relevancy":      "#2F9E44",
        "R3_importance":     "#9C36B5",
    }
    note = {"R2_relevancy": "decision rule selects", "R3_importance": "ships today"}

    W = 980
    panel_h = 152
    top = 104
    H = top + len(metrics) * panel_h + 92
    left, right = 62, W - 190

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="-apple-system,Segoe UI,Helvetica,sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
         '<text x="28" y="38" font-size="17" font-weight="700" fill="#111">'
         'Knowledge-base retrieval: one-change-at-a-time ablation</text>',
         '<text x="28" y="60" font-size="12" fill="#555">'
         'Each rung adds exactly one design element to the rung below it, so every '
         'shift is attributable to one decision.</text>',
         '<text x="28" y="78" font-size="11.5" fill="#777">'
         'Corpus: 126 findings \u00b7 6 scenarios \u00b7 bundle ceiling 15 \u00b7 no API calls</text>']

    for i, (key, label, higher_better, fmt) in enumerate(metrics):
        y0 = top + i * panel_h
        axis_y = y0 + 96
        vals = [summary[n][key] for n in names]
        vmax = max(vals) or 1.0
        domain = vmax * 1.10

        p.append(f'<text x="28" y="{y0 + 20}" font-size="12.5" font-weight="600" '
                 f'fill="#222">{label}</text>')
        direction = "higher is better \u2192" if higher_better else "\u2190 lower is better"
        p.append(f'<text x="28" y="{y0 + 36}" font-size="10.5" fill="#888">'
                 f'{direction}</text>')

        # axis
        p.append(f'<line x1="{left}" y1="{axis_y}" x2="{right}" y2="{axis_y}" '
                 f'stroke="#d0d0d0" stroke-width="1.2"/>')
        for frac in (0, 0.5, 1.0):
            tx = left + frac * (right - left)
            tv = domain * frac
            p.append(f'<line x1="{tx}" y1="{axis_y - 4}" x2="{tx}" y2="{axis_y + 4}" '
                     f'stroke="#d0d0d0" stroke-width="1"/>')
            p.append(f'<text x="{tx}" y="{axis_y + 18}" font-size="9" fill="#aaa" '
                     f'text-anchor="middle">{fmt.format(tv)}</text>')

        # Dots and labels. Rungs frequently share a value -- that near-identity
        # IS the finding -- so co-located dots are fanned vertically and their
        # labels stacked ABOVE the whole cluster, never behind a dot.
        pts = sorted(((summary[n][key], n) for n in names), key=lambda kv: kv[0])

        clusters = []   # each: {"x":, "items":[(v, n, dot_dy)]}
        for v, n in pts:
            x = left + (v / domain) * (right - left)
            for c in clusters:
                if abs(c["x"] - x) < 46:
                    c["items"].append((v, n, x))
                    break
            else:
                clusters.append({"x": x, "items": [(v, n, x)]})

        for c in clusters:
            fan = [0, -14, 14, -28, 28]
            top_dy = 0
            for i, (v, n, x) in enumerate(c["items"]):
                dot_dy = fan[min(i, len(fan) - 1)]
                top_dy = min(top_dy, dot_dy)
                p.append(f'<circle cx="{x:.1f}" cy="{axis_y + dot_dy}" r="7" '
                         f'fill="{colour[n]}" fill-opacity="0.9" stroke="#fff" '
                         f'stroke-width="1.5"/>')
                if dot_dy:
                    p.append(f'<line x1="{x:.1f}" y1="{axis_y}" x2="{x:.1f}" '
                             f'y2="{axis_y + dot_dy}" stroke="{colour[n]}" '
                             f'stroke-width="1" stroke-opacity="0.3"/>')
            base = axis_y + top_dy - 13
            for i, (v, n, x) in enumerate(c["items"]):
                p.append(f'<text x="{c["x"]:.1f}" y="{base - i * 12:.1f}" '
                         f'font-size="9.5" fill="{colour[n]}" font-weight="600" '
                         f'text-anchor="middle">{fmt.format(v)}</text>')

        best = max(vals) if higher_better else min(vals)
        winners = [n.split("_")[0] for n in names if summary[n][key] == best]
        p.append(f'<text x="{right + 16}" y="{axis_y + 4}" font-size="9.5" '
                 f'fill="#666">best: {"/".join(winners)}</text>')

    # legend
    ly = H - 54
    p.append(f'<line x1="28" y1="{ly - 22}" x2="{W - 28}" y2="{ly - 22}" '
             f'stroke="#eee" stroke-width="1"/>')
    for j, n in enumerate(names):
        cx = 34 + (j % 3) * 290
        cy = ly + (j // 3) * 20
        p.append(f'<circle cx="{cx}" cy="{cy - 4}" r="6" fill="{colour[n]}"/>')
        extra = ('  \u2014 ' + note[n]) if n in note else ''
        p.append(f'<text x="{cx + 12}" y="{cy}" font-size="10" fill="#333">'
                 f'{n}{extra}</text>')

    p.append("</svg>")
    path.write_text("\n".join(p))


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DB_PATH)
    args = ap.parse_args()
    OUT_DIR.mkdir(exist_ok=True)

    corpus = load_corpus(args.db)
    print(f"corpus: {len(corpus)} entries  scenarios: {len(SCENARIOS)}  "
          f"bundle ceiling: {BUNDLE_LIMIT}  K per layer: {K}")
    dist = {l: sum(1 for e in corpus if e["layer"] == l) for l in LAYERS}
    print(f"layer distribution: {dist}\n")

    summary, robust = {}, {}
    for name, fn, desc in RUNGS:
        flat = name in ("R0_recency", "K_keyword_recency")
        bundles = {s["id"]: fn(corpus, s) for s in SCENARIOS}
        summary[name] = {
            "description": desc,
            "n_returned": round(statistics.mean(len(b) for b in bundles.values()), 2),
            "redundancy": round(statistics.mean(
                redundancy(b) for b in bundles.values()), 4),
            "bundle_tokens": int(statistics.mean(
                bundle_tokens(b) for b in bundles.values())),
            "responsiveness": round(responsiveness(bundles), 4),
            "corpus_reach": round(corpus_reach(bundles, len(corpus)), 4),
            "purge_exposure": purge_exposure(corpus, flat),
        }
        robust[name] = robustness(fn, corpus, flat)

    (OUT_DIR / "kb_ablation_summary.json").write_text(json.dumps(
        {"corpus_size": len(corpus), "layer_distribution": dist,
         "scenarios": [s["id"] for s in SCENARIOS],
         "summary": summary, "robustness": robust,
         "decision_rule": ("Adopt the simplest rung not clearly beaten on "
                           "redundancy and responsiveness. Ties go to simpler.")},
        indent=2))
    write_dotplot(summary, OUT_DIR / "kb_ablation_dotplot.svg")

    hdr = (f"  {'rung':<20}{'returned':>9}{'redund':>9}{'tokens':>9}"
           f"{'respons':>9}{'reach':>8}{'purge':>7}")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for name, _, _ in RUNGS:
        s = summary[name]
        print(f"  {name:<20}{s['n_returned']:>9}{s['redundancy']:>9.4f}"
              f"{s['bundle_tokens']:>9}{s['responsiveness']:>9.4f}"
              f"{s['corpus_reach']:>8.3f}{s['purge_exposure']:>7}")

    print("\n  Deltas — what each single change bought:")
    ladder = ["R0_recency", "R1_layered", "R2_relevancy", "R3_importance"]
    for a, b in zip(ladder, ladder[1:]):
        sa, sb = summary[a], summary[b]
        print(f"    {a:<15} -> {b:<15} "
              f"redund {sb['redundancy'] - sa['redundancy']:+.4f}  "
              f"tokens {sb['bundle_tokens'] - sa['bundle_tokens']:+d}  "
              f"respons {sb['responsiveness'] - sa['responsiveness']:+.4f}  "
              f"reach {sb['corpus_reach'] - sa['corpus_reach']:+.3f}")

    print("\n  Robustness:")
    for name, _, _ in RUNGS:
        bad = {k: v for k, v in robust[name].items() if v.startswith("RAISED")}
        # An empty KB SHOULD return 0 rows -- that is correct, not a failure.
        # Only returning nothing when the corpus is populated is a blind spot.
        zero = {k: v for k, v in robust[name].items()
                if v == "ok (0 rows)" and k != "empty knowledge base"}
        flag = ""
        if bad:
            flag = f"  RAISED on {list(bad)}"
        elif zero:
            flag = f"  BLIND on {list(zero)}"
        print(f"    {name:<20}{'all cases handled' if not flag else flag}")

    print(f"\nWrote kb_ablation_summary.json and kb_ablation_dotplot.svg to trials_out/")


if __name__ == "__main__":
    main()
