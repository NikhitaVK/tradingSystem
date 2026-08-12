"""
plot_prompt_ablation.py — dot plot for Trial 2 (prompt ablation ladder).

Reads the per-arm summary JSONs produced by prompt_ablation_ladder.py and
renders one SVG.

Layout differs from Trial 1's plot on purpose. Trial 1 compared providers, so
its dots were arms. This is a ladder, so the dots are RUNGS (P0 → P4) and each
metric panel carries one axis per arm, stacked. That puts the strong model and
the weak model on the same scale for the same metric, which is the only way to
see whether prompt structure matters more where capability is scarce.

Axis domains are shared across arms within a metric — per-arm scaling would make
a 0.33 on one arm look level with a 1.00 on the other.

Usage:
    python3 -m scripts.trials.plot_prompt_ablation
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

OUT_DIR = Path(__file__).resolve().parents[2] / "trials_out"

RUNGS = ["P0", "P1", "P2", "P3", "P4"]
RUNG_LABEL = {
    "P0": "P0 bare",
    "P1": "P1 +criteria",
    "P2": "P2 +formulas",
    "P3": "P3 +thresholds (=v2)",
    "P4": "P4 +KB (=v3, ships)",
}
# Sequential palette: the ladder has an order, so the colours should too.
RUNG_COLOUR = {
    "P0": "#D6336C", "P1": "#F08C00", "P2": "#2F9E44",
    "P3": "#1C7ED6", "P4": "#5F3DC4",
}

METRICS = [
    ("json_compliance", "Valid JSON rate  (1.0 = always parseable)", True, "{:.2f}"),
    ("accuracy", "Verdict accuracy on the 3 graded cases", True, "{:.2f}"),
    ("false_deploy_rate", "False-deploy rate  (deployed a strategy that should be rejected)", False, "{:.2f}"),
    ("consistency", "Cases where all 3 repeats agreed  (of 4)", True, "{:.0f}"),
    ("prompt_tokens", "Prompt size in tokens  (what the instructions cost)", False, "{:.0f}"),
]

ARM_LABEL = {
    "gemini": "Gemini 3.6 Flash  (free tier — frontier-class)",
    "ollama_gemma3_4b": "Gemma 3 4B  (local — weak-model contrast)",
    "claude_sonnet": "Claude Sonnet 4.6  (reference, P4 only, from logs)",
}


def load() -> dict:
    """Merge the per-arm summary files into {arm: {rung: metrics}}."""
    arms = {}
    for tag in ("_gemini", "_gemma"):
        p = OUT_DIR / f"prompt_ablation_summary{tag}.json"
        if not p.exists():
            continue
        data = json.loads(p.read_text())
        for arm, s in data.get("arms", {}).items():
            if s.get("ran") and s.get("rungs"):
                arms[arm] = s["rungs"]
    return arms


def build(arms: dict) -> str:
    # Claude only has P4, so it is drawn as a reference tick, not its own axis.
    ladder_arms = [a for a in ("gemini", "ollama_gemma3_4b") if a in arms]
    if not ladder_arms:
        raise SystemExit("no ladder arms found in trials_out/")

    row_h, panel_pad, top = 74, 46, 150
    panel_h = len(ladder_arms) * row_h + panel_pad
    W = 1000
    H = top + len(METRICS) * panel_h + 92
    left, right = 176, W - 168

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="-apple-system,Segoe UI,Helvetica,sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
         '<text x="28" y="38" font-size="17" font-weight="700" fill="#111">'
         'Trial 2 — prompt engineering: does the prompt change the decision?</text>',
         '<text x="28" y="60" font-size="12" fill="#555">'
         'Same agent, same data, same parser. Each rung adds exactly one element '
         'to the rung below it.</text>',
         '<text x="28" y="78" font-size="11.5" fill="#777">'
         'P3 and P4 are the shipped prompts verbatim (analyst_eval_v2 / v3). '
         'P0–P2 were built by subtraction from v2.</text>',
         '<text x="28" y="95" font-size="11" fill="#777">'
         'Unparseable responses are excluded from accuracy AND from false-deploy '
         '— a rung that answers nothing is not "safe".</text>',
         '<text x="28" y="112" font-size="11" fill="#999">'
         'Axis domains are shared between arms within a panel, so the two models '
         'are directly comparable.</text>']

    # legend
    for j, r in enumerate(RUNGS):
        cx = 32 + j * 190
        p.append(f'<circle cx="{cx}" cy="{132}" r="6" fill="{RUNG_COLOUR[r]}"/>')
        p.append(f'<text x="{cx + 12}" y="{136}" font-size="10.5" fill="#333">'
                 f'{RUNG_LABEL[r]}</text>')

    for i, (key, label, higher, fmt) in enumerate(METRICS):
        y0 = top + i * panel_h
        p.append(f'<text x="28" y="{y0 + 6}" font-size="12.5" font-weight="600" '
                 f'fill="#222">{label}</text>')
        arrow = "higher is better →" if higher else "← lower is better"
        p.append(f'<text x="{right + 16}" y="{y0 + 6}" font-size="10" fill="#999">'
                 f'{arrow}</text>')

        # shared domain across arms for this metric
        vals = [arms[a][r][key] for a in ladder_arms for r in RUNGS
                if r in arms[a] and arms[a][r].get(key) is not None]
        if not vals:
            continue
        domain = (max(vals) or 1.0) * 1.12

        for k, arm in enumerate(ladder_arms):
            axis_y = y0 + panel_pad + k * row_h
            p.append(f'<text x="28" y="{axis_y - 12}" font-size="10" fill="#666">'
                     f'{ARM_LABEL.get(arm, arm)}</text>')
            p.append(f'<line x1="{left}" y1="{axis_y}" x2="{right}" y2="{axis_y}" '
                     f'stroke="#dcdcdc" stroke-width="1.2"/>')
            for frac in (0, 0.5, 1.0):
                tx = left + frac * (right - left)
                p.append(f'<line x1="{tx}" y1="{axis_y - 4}" x2="{tx}" '
                         f'y2="{axis_y + 4}" stroke="#dcdcdc" stroke-width="1"/>')
                p.append(f'<text x="{tx}" y="{axis_y + 17}" font-size="8.5" '
                         f'fill="#bbb" text-anchor="middle">'
                         f'{fmt.format(domain * frac)}</text>')

            pts = []
            for r in RUNGS:
                v = arms[arm].get(r, {}).get(key)
                if v is None:
                    continue
                pts.append((v, r, left + (v / domain) * (right - left)))
            pts.sort(key=lambda t: t[0])

            # fan overlapping dots so stacked rungs stay individually visible
            clusters = []
            for v, r, x in pts:
                for c in clusters:
                    if abs(c["x"] - x) < 40:
                        c["items"].append((v, r, x)); break
                else:
                    clusters.append({"x": x, "items": [(v, r, x)]})
            for c in clusters:
                fan = [0, -13, 13, -26, 26]
                hi = 0
                for j, (v, r, x) in enumerate(c["items"]):
                    dy = fan[min(j, len(fan) - 1)]
                    hi = min(hi, dy)
                    if dy:
                        p.append(f'<line x1="{x:.1f}" y1="{axis_y}" x2="{x:.1f}" '
                                 f'y2="{axis_y + dy}" stroke="{RUNG_COLOUR[r]}" '
                                 f'stroke-width="1" stroke-opacity="0.3"/>')
                    p.append(f'<circle cx="{x:.1f}" cy="{axis_y + dy}" r="7" '
                             f'fill="{RUNG_COLOUR[r]}" fill-opacity="0.92" '
                             f'stroke="#fff" stroke-width="1.5"/>')
                    p.append(f'<text x="{x:.1f}" y="{axis_y + dy + 3.2}" '
                             f'font-size="7.5" fill="#fff" font-weight="700" '
                             f'text-anchor="middle">{r[1]}</text>')
                base = axis_y + hi - 12
                for j, (v, r, x) in enumerate(c["items"]):
                    p.append(f'<text x="{c["x"]:.1f}" y="{base - j * 11:.1f}" '
                             f'font-size="9" fill="{RUNG_COLOUR[r]}" '
                             f'font-weight="600" text-anchor="middle">'
                             f'{fmt.format(v)}</text>')

    p.append(f'<text x="28" y="{H - 30}" font-size="10" fill="#888">'
             f'Dots are labelled with their rung number. Where two rungs scored '
             f'the same, the dots are fanned vertically off a shared point.</text>')
    p.append("</svg>")
    return "\n".join(p)


def main() -> None:
    arms = load()
    if not arms:
        raise SystemExit("no summary files — run prompt_ablation_ladder.py first")
    out = OUT_DIR / "prompt_ablation_dotplot.svg"
    out.write_text(build(arms))
    print(f"Wrote {out}")
    print(f"Arms plotted: {', '.join(arms)}")


if __name__ == "__main__":
    main()
