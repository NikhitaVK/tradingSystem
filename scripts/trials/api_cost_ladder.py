"""
api_cost_ladder.py — Trial 1, rebuilt as a one-change-at-a-time cost ablation.

Why this replaces api_cost_trial.py
-----------------------------------
The first version compared three model-routing options and found a null result:
all three cost within 0.2% of each other, because 99.6% of output volume sits in
two judgement agents that no routing option moves off Sonnet. It also measured
OUTPUT tokens only -- reasoning_logs never recorded input tokens -- and input is
the larger half of the bill for this system.

This version fixes both problems:

  1. It measures INPUT by reconstruction. The prompts are deterministic
     functions of data we still have (prompt templates on disk, real candidate
     specs and backtest results from the DB), so the exact prompt text a Loop 1
     attempt sends can be rebuilt and measured without making any API call.
  2. It ablates the levers that actually move input cost, one at a time.

The ladder (cumulative -- each rung adds exactly one change)
------------------------------------------------------------
    C0  baseline        what ships: flat KB bundle, full backtest JSON in both
                        prompts, no caching
    C1  + layered KB    swap the flat 15-entry bundle for the layered retrieval
                        (this is Trial 4's R2 rung -- the trials connect here)
    C2  + trimmed payload
                        pass aggregate + calibration only; drop the per-slice
                        arrays from the prompt
    C3  + cacheable prefix
                        reorder so static instructions lead, then apply prompt
                        caching to that prefix

Decision rule, fixed BEFORE the run
-----------------------------------
Adopt the cheapest rung that does NOT delete evidence the agent demonstrably
uses. Cost reduction that works by hiding information from the analyst is not a
win -- it is a silent quality cut. C2 is the rung where this bites: the analyst
prompt explicitly reasons over per-slice consistency ("only 2 of 5 slices
profitable"), so trimming slices is a real trade-off, not free money.

Units
-----
One "attempt" = one strategy-agent selection call + one analyst-evaluation call,
which is what a single Loop 1 attempt actually sends. A full Loop 1 run is up to
LOOP1_MAX_ATTEMPTS of those.

No API calls. Nothing here spends credit.

Usage:
    python -m scripts.trials.api_cost_ladder
"""
import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.settings import DB_PATH, LOOP1_MAX_ATTEMPTS  # noqa: E402
import src.agents.analyst_agent as analyst_agent  # noqa: E402
import src.agents.strategy_agent as strategy_agent  # noqa: E402
from src.agents.candidate_generator import generate_candidate_pool  # noqa: E402
from src.agents.empirical_search import run_search  # noqa: E402
from src.data.knowledge_base import get_working_memory, query_relevant  # noqa: E402
from src.loop1 import _flatten_working_memory  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[2] / "trials_out"
CACHE = Path("/private/tmp/claude-501/-Users-nikhita-13DIT-tradingSystemv0-01/"
             "f2682cbf-9132-48bf-824f-70f872cc97ce/scratchpad/api_cost_ranked.json")

# Pricing, USD per million tokens (Anthropic list price, Sonnet 4.6).
PRICE_IN = 3.00
PRICE_OUT = 15.00
# Prompt caching multipliers: writing the cache costs 1.25x base input,
# reading it costs 0.10x.
CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10

# Measured on this corpus with the free count_tokens endpoint on 2026-08-10,
# while API credit was still available. count_tokens is now blocked (no credit),
# so this ratio is reused rather than re-measured. See "Scope limits".
CHARS_PER_TOKEN = 4.04

# Mean output tokens per call, measured from the 336 real logged calls in
# reasoning_logs (see api_cost_trial.py). Output is unaffected by these levers,
# so it is held constant across rungs.
OUT_TOKENS = {"strategy_agent": 696, "analyst_eval": 1450}

REGIME = "high_vol"
MECHANISM = "momentum"
SYMBOL = "BTC/USDT"


def toks(text: str) -> int:
    return round(len(text) / CHARS_PER_TOKEN)


# ── Reconstructing a real Loop 1 attempt ─────────────────────────────────────

def get_ranked(db_path: str):
    """
    Real ranked survivors from the deterministic search. Cached to disk because
    it costs ~2 minutes of backtesting and never changes for fixed inputs.
    """
    if CACHE.exists():
        data = json.loads(CACHE.read_text())
        return [(c["spec"], c["results"], c["score"]) for c in data]
    pool = generate_candidate_pool(REGIME, {"symbol": SYMBOL, "timeframe": "1h"})
    ranked = run_search(pool, db_path)
    if not ranked:
        raise SystemExit("empirical search returned no viable candidates")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(
        [{"spec": s, "results": r, "score": sc} for s, r, sc in ranked]))
    return ranked


def trim_results(results: dict) -> dict:
    """
    C2's change: keep aggregate + calibration, replace the per-slice array with
    a one-line summary. This is the rung that trades evidence for cost.
    """
    out = copy.deepcopy(results)
    slices = out.get("slices", [])
    profitable = sum(1 for s in slices if (s.get("pnl_pct") or 0) > 0)
    out["slices"] = f"{profitable}/{len(slices)} slices profitable (detail omitted)"
    return out


def static_prefix_chars(template: str) -> int:
    """Chars before the first {placeholder} -- the only cacheable span as written."""
    i = template.find("{")
    return len(template) if i == -1 else i


# ── The ladder ───────────────────────────────────────────────────────────────

def build_attempt(rung: str, ranked, db_path: str) -> dict:
    """Return the two prompts a single Loop 1 attempt sends, under one rung."""
    # KB bundle: C0 flat keyword; C1+ layered.
    rung = rung.split("_")[0]          # "C0_baseline" -> "C0"
    if rung == "C0":
        kb_context = query_relevant(
            ["momentum", "macd", "crossover"], db_path, limit=15)
    else:
        kb_context = _flatten_working_memory(
            get_working_memory(db_path, current_regime=REGIME, mechanism=MECHANISM))

    # Backtest payload: C2+ trimmed.
    if rung in ("C2", "C3"):          # C1c deliberately excluded
        ranked_used = [(s, trim_results(r), sc) for s, r, sc in ranked]
    else:
        ranked_used = ranked

    strat_prompt = strategy_agent._build_system_prompt(
        kb_context, ranked_used, REGIME)

    top_spec, top_results, _ = ranked_used[0]
    analyst_prompt = analyst_agent._build_eval_prompt(
        top_spec, top_results, "Knowledge base not consulted.")

    return {"strategy_agent": strat_prompt, "analyst_eval": analyst_prompt,
            "kb_entries": len(kb_context)}


def cost_attempt(prompts: dict, cached: bool) -> dict:
    """Cost one attempt. `cached` applies prompt caching to the static prefix."""
    templates = {
        "strategy_agent": strategy_agent._STRATEGY_PROMPT,
        "analyst_eval": analyst_agent._EVAL_PROMPT,
    }
    in_tok = out_tok = 0
    cacheable = 0
    cost_in = 0.0
    for agent in ("strategy_agent", "analyst_eval"):
        text = prompts[agent]
        total = toks(text)
        in_tok += total
        out_tok += OUT_TOKENS[agent]
        cost_out = 0.0  # accumulated below

        if cached:
            # C3 reorders so the whole template body leads; everything before
            # the first per-call value becomes a reusable prefix.
            prefix = toks(templates[agent])
            prefix = min(prefix, total)
            variable = total - prefix
            cacheable += prefix
            # First attempt writes the cache, the remaining ones read it.
            n = LOOP1_MAX_ATTEMPTS
            prefix_cost = (
                prefix * CACHE_WRITE_MULT + prefix * CACHE_READ_MULT * (n - 1)
            ) / n
            cost_in += (prefix_cost + variable) / 1e6 * PRICE_IN
        else:
            cacheable += static_prefix_chars(templates[agent]) / CHARS_PER_TOKEN
            cost_in += total / 1e6 * PRICE_IN

    cost_out = out_tok / 1e6 * PRICE_OUT
    return {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "cacheable_tokens": round(cacheable),
        "cost_in": cost_in,
        "cost_out": cost_out,
        "cost_attempt": cost_in + cost_out,
    }


RUNGS = [
    ("C0_baseline", "what ships: flat KB bundle, full backtest JSON"),
    ("C1_layered_kb", "+ layered KB retrieval (Trial 4 R2)"),
    ("C2_trimmed_payload", "+ drop per-slice detail from prompts"),
    ("C3_prompt_caching", "+ cacheable static prefix"),
    # Not a rung: the ladder is cumulative, so C3 inherits C2's information
    # loss. The decision rule needs an option that keeps every slice the
    # analyst reasons over, so this is C1 + caching with NO payload trim.
    ("C1c_layered_cached", "off-ladder: layered KB + caching, payload intact"),
]


# ── Dot plot ─────────────────────────────────────────────────────────────────

def write_dotplot(summary: dict, path: Path) -> None:
    metrics = [
        ("input_tokens", "Input tokens per Loop 1 attempt", False, "{:,.0f}"),
        ("cost_attempt", "USD per attempt", False, "${:.4f}"),
        ("cost_run", f"USD per full Loop 1 run ({LOOP1_MAX_ATTEMPTS} attempts)",
         False, "${:.3f}"),
        ("kb_entries", "KB findings in the bundle", True, "{:.0f}"),
    ]
    names = list(summary.keys())
    colour = {"C0_baseline": "#E8590C", "C1_layered_kb": "#1C7ED6",
              "C2_trimmed_payload": "#F08C00", "C3_prompt_caching": "#2F9E44",
              "C1c_layered_cached": "#5F3DC4"}
    note = {"C0_baseline": "ships today",
            "C1c_layered_cached": "off-ladder, payload intact"}

    # Canvas kept near-square: qlmanage rasterises into a 1500x1500 box and
    # clips wide aspect ratios, so panels are spaced to keep H close to W.
    W, panel_h, top = 980, 196, 104
    H = top + len(metrics) * panel_h + 96
    left, right = 62, W - 190

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="-apple-system,Segoe UI,Helvetica,sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
         '<text x="28" y="38" font-size="17" font-weight="700" fill="#111">'
         'API cost: one-change-at-a-time ablation</text>',
         '<text x="28" y="60" font-size="12" fill="#555">'
         'Each rung adds exactly one cost lever to the rung below it. '
         'Prompts reconstructed from real data; no API calls.</text>',
         '<text x="28" y="78" font-size="11.5" fill="#777">'
         'One attempt = 1 strategy-selection call + 1 analyst-evaluation call. '
         'Output tokens held constant (measured from 336 logged calls).</text>']

    for i, (key, label, higher_better, fmt) in enumerate(metrics):
        y0 = top + i * panel_h
        axis_y = y0 + 96
        vals = [summary[n][key] for n in names]
        domain = (max(vals) or 1.0) * 1.10
        direction = "higher is better →" if higher_better else "← lower is better"
        p.append(f'<text x="28" y="{y0 + 20}" font-size="12.5" font-weight="600" '
                 f'fill="#222">{label}</text>')
        p.append(f'<text x="28" y="{y0 + 36}" font-size="10.5" fill="#888">'
                 f'{direction}</text>')
        p.append(f'<line x1="{left}" y1="{axis_y}" x2="{right}" y2="{axis_y}" '
                 f'stroke="#d0d0d0" stroke-width="1.2"/>')
        for frac in (0, 0.5, 1.0):
            tx = left + frac * (right - left)
            p.append(f'<line x1="{tx}" y1="{axis_y - 4}" x2="{tx}" y2="{axis_y + 4}" '
                     f'stroke="#d0d0d0" stroke-width="1"/>')
            p.append(f'<text x="{tx}" y="{axis_y + 18}" font-size="9" fill="#aaa" '
                     f'text-anchor="middle">{fmt.format(domain * frac)}</text>')

        pts = sorted(((summary[n][key], n) for n in names), key=lambda kv: kv[0])
        clusters = []
        for v, n in pts:
            x = left + (v / domain) * (right - left)
            for c in clusters:
                if abs(c["x"] - x) < 46:
                    c["items"].append((v, n, x)); break
            else:
                clusters.append({"x": x, "items": [(v, n, x)]})
        for c in clusters:
            fan = [0, -14, 14, -28]
            top_dy = 0
            for j, (v, n, x) in enumerate(c["items"]):
                dy = fan[min(j, len(fan) - 1)]
                top_dy = min(top_dy, dy)
                p.append(f'<circle cx="{x:.1f}" cy="{axis_y + dy}" r="7" '
                         f'fill="{colour[n]}" fill-opacity="0.9" stroke="#fff" '
                         f'stroke-width="1.5"/>')
                if dy:
                    p.append(f'<line x1="{x:.1f}" y1="{axis_y}" x2="{x:.1f}" '
                             f'y2="{axis_y + dy}" stroke="{colour[n]}" '
                             f'stroke-width="1" stroke-opacity="0.3"/>')
            base = axis_y + top_dy - 13
            for j, (v, n, x) in enumerate(c["items"]):
                p.append(f'<text x="{c["x"]:.1f}" y="{base - j * 12:.1f}" '
                         f'font-size="9.5" fill="{colour[n]}" font-weight="600" '
                         f'text-anchor="middle">{fmt.format(v)}</text>')
        best = max(vals) if higher_better else min(vals)
        win = [n.split("_")[0] for n in names if summary[n][key] == best]
        p.append(f'<text x="{right + 16}" y="{axis_y + 4}" font-size="9.5" '
                 f'fill="#666">best: {"/".join(win)}</text>')

    ly = H - 38
    p.append(f'<line x1="28" y1="{ly - 22}" x2="{W - 28}" y2="{ly - 22}" '
             f'stroke="#eee" stroke-width="1"/>')
    for j, n in enumerate(names):
        cx = 34 + (j % 2) * 440
        cy = ly + (j // 2) * 18
        p.append(f'<circle cx="{cx}" cy="{cy - 4}" r="6" fill="{colour[n]}"/>')
        extra = ('  — ' + note[n]) if n in note else ''
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

    print("Reconstructing a real Loop 1 attempt (deterministic, no API calls)...")
    ranked = get_ranked(args.db)
    print(f"  {len(ranked)} ranked survivors, top = {ranked[0][0].get('name')}")

    summary = {}
    for rung, desc in RUNGS:
        prompts = build_attempt(rung, ranked, args.db)
        c = cost_attempt(prompts, cached=rung in
                         ("C3_prompt_caching", "C1c_layered_cached"))
        c["description"] = desc
        c["kb_entries"] = prompts["kb_entries"]
        c["cost_run"] = c["cost_attempt"] * LOOP1_MAX_ATTEMPTS
        summary[rung] = c

    (OUT_DIR / "api_cost_ladder_summary.json").write_text(json.dumps(
        {"summary": summary,
         "chars_per_token": CHARS_PER_TOKEN,
         "output_tokens_per_call": OUT_TOKENS,
         "loop1_max_attempts": LOOP1_MAX_ATTEMPTS,
         "pricing_usd_per_mtok": {"input": PRICE_IN, "output": PRICE_OUT},
         "decision_rule": ("Cheapest rung that does not delete evidence the "
                           "agent demonstrably uses.")}, indent=2))
    write_dotplot(summary, OUT_DIR / "api_cost_ladder_dotplot.svg")

    hdr = (f"  {'rung':<22}{'kb':>4}{'in tok':>9}{'out tok':>9}"
           f"{'$/attempt':>11}{'$/run':>9}{'cacheable':>11}")
    print()
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for rung, _ in RUNGS:
        s = summary[rung]
        print(f"  {rung:<22}{s['kb_entries']:>4}{s['input_tokens']:>9,}"
              f"{s['output_tokens']:>9,}{s['cost_attempt']:>11.4f}"
              f"{s['cost_run']:>9.3f}{s['cacheable_tokens']:>11,}")

    print("\n  Deltas — what each single change bought:")
    order = [r for r, _ in RUNGS if not r.startswith("C1c")]
    for a, b in zip(order, order[1:]):
        sa, sb = summary[a], summary[b]
        pct = (sb["cost_attempt"] - sa["cost_attempt"]) / sa["cost_attempt"] * 100
        print(f"    {a:<20} -> {b:<20} "
              f"in {sb['input_tokens'] - sa['input_tokens']:+,} tok   "
              f"cost {pct:+.1f}%")

    base = summary["C0_baseline"]["cost_run"]
    best = summary["C3_prompt_caching"]["cost_run"]
    print(f"\n  Full ladder: ${base:.3f} -> ${best:.3f} per Loop 1 run "
          f"({(best - base) / base * 100:+.1f}%)")
    print("\nWrote api_cost_ladder_summary.json and api_cost_ladder_dotplot.svg")


if __name__ == "__main__":
    main()
