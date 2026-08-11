"""
api_cost_trial.py — Trial 1 (API cost / model routing).

MEASURES the real token volume this system has already produced, then COSTS
that same measured workload under three model-routing options.

Data source: the `reasoning_logs` table. Every Claude call the system has ever
made was logged there by `ClaudeClient._log()`, so this is measurement of real
usage, not simulation. No new API calls are made to produce the token counts.

Three options costed
--------------------
  A  all-Sonnet          every agent on CLAUDE_MODEL (what ships today)
  B  Haiku for mechanical  empirical_search + analyst_brief -> Haiku,
                           judgement agents stay on Sonnet
  C  tiered by thinking budget
                           agents whose thinking budget is <= HAIKU_TIER_MAX
                           tokens go to Haiku; the rest stay on Sonnet

Output (written to trials_out/):
  api_cost_measured_tokens.csv   per-agent measured output tokens
  api_cost_options.csv           cost per option per agent
  api_cost_dotplot.svg           dot plot, cost per agent per option

Usage
-----
    python -m scripts.trials.api_cost_trial
    python -m scripts.trials.api_cost_trial --calibrate   # tokenise via API (free)

`--calibrate` samples logged texts and counts them with Anthropic's
count_tokens endpoint, which is FREE and not billed, to derive a real
chars-per-token ratio for this corpus instead of the literature default.
"""
import argparse
import csv
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from config.settings import (  # noqa: E402
    DB_PATH,
    CLAUDE_THINKING_BUDGET_STRATEGY,
    CLAUDE_THINKING_BUDGET_ANALYST,
    CLAUDE_THINKING_BUDGET_ANALYST_BRIEF,
)

OUT_DIR = Path(__file__).resolve().parents[2] / "trials_out"

# ── Pricing, USD per million tokens (Anthropic list price) ───────────────────
PRICING = {
    "sonnet": {"in": 3.00, "out": 15.00, "label": "claude-sonnet-4-6"},
    "haiku": {"in": 1.00, "out": 5.00, "label": "claude-haiku-4-5"},
}

# Thinking budget each agent actually runs at (config/settings.py).
# empirical_search has no LLM judgement step of its own -- it logs under the
# strategy agent's budget -- but it is mechanical, hence its place in option B.
AGENT_THINKING_BUDGET = {
    "strategy_agent": CLAUDE_THINKING_BUDGET_STRATEGY,
    "analyst_eval": CLAUDE_THINKING_BUDGET_ANALYST,
    "analyst_brief": CLAUDE_THINKING_BUDGET_ANALYST_BRIEF,
    "empirical_search": CLAUDE_THINKING_BUDGET_ANALYST_BRIEF,
}

# Option B: which agents are "mechanical" (no open-ended judgement).
MECHANICAL_AGENTS = {"empirical_search", "analyst_brief"}

# Option C: thinking budget at or below this routes to Haiku.
HAIKU_TIER_MAX = 2000

# Fallback chars-per-token. Overridden by --calibrate.
DEFAULT_CHARS_PER_TOKEN = 3.8


# ── Measurement ──────────────────────────────────────────────────────────────

def extract_thinking_text(raw: str) -> str:
    """
    Pull the human-readable thinking text out of a stored thinking blob.

    The column holds a JSON list of content blocks, each carrying a long
    base64 `signature` alongside the actual text. Signatures are roughly 2-3x
    the size of the thinking itself, so measuring the raw column length would
    overstate token volume badly. Only the `thinking` field is billed output.
    """
    if not raw:
        return ""
    try:
        blocks = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw
    if not isinstance(blocks, list):
        return ""
    return "".join(
        b.get("thinking", "") for b in blocks if isinstance(b, dict)
    )


def measure(db_path: str) -> dict:
    """Per-agent measured character volume, split thinking vs response."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT agent, thinking, response FROM reasoning_logs"
    ).fetchall()
    conn.close()

    per_agent = {}
    for agent, thinking, response in rows:
        rec = per_agent.setdefault(
            agent, {"calls": 0, "thinking_chars": 0, "response_chars": 0}
        )
        rec["calls"] += 1
        rec["thinking_chars"] += len(extract_thinking_text(thinking))
        rec["response_chars"] += len(response or "")
    return per_agent


def calibrate_ratio(db_path: str, sample_size: int = 20) -> float:
    """
    Derive chars-per-token for this corpus using the free count_tokens endpoint.

    count_tokens is not billed, so this adds no cost to the trial. Returns the
    default ratio if the SDK or key is unavailable.
    """
    try:
        import anthropic
    except ImportError:
        print("  anthropic SDK not installed - using default ratio")
        return DEFAULT_CHARS_PER_TOKEN
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("  ANTHROPIC_API_KEY not set - using default ratio")
        return DEFAULT_CHARS_PER_TOKEN

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT thinking, response FROM reasoning_logs "
        "WHERE length(response) > 200 ORDER BY id LIMIT ?",
        (sample_size,),
    ).fetchall()
    conn.close()

    client = anthropic.Anthropic()
    total_chars = total_tokens = 0
    for thinking, response in rows:
        text = extract_thinking_text(thinking) + (response or "")
        if not text.strip():
            continue
        try:
            r = client.messages.count_tokens(
                model=PRICING["sonnet"]["label"],
                messages=[{"role": "user", "content": text}],
            )
        except Exception as e:  # network, auth, rate limit
            print(f"  count_tokens failed ({e}) - using default ratio")
            return DEFAULT_CHARS_PER_TOKEN
        total_chars += len(text)
        total_tokens += r.input_tokens

    if not total_tokens:
        return DEFAULT_CHARS_PER_TOKEN
    ratio = total_chars / total_tokens
    print(f"  calibrated on {len(rows)} samples: {ratio:.2f} chars/token")
    return ratio


# ── Costing ──────────────────────────────────────────────────────────────────

def route(agent: str, option: str) -> str:
    """Which model tier this agent runs on under a given routing option."""
    if option == "A":
        return "sonnet"
    if option == "B":
        return "haiku" if agent in MECHANICAL_AGENTS else "sonnet"
    if option == "C":
        budget = AGENT_THINKING_BUDGET.get(agent, CLAUDE_THINKING_BUDGET_STRATEGY)
        return "haiku" if budget <= HAIKU_TIER_MAX else "sonnet"
    raise ValueError(f"unknown option {option}")


def cost_output(tokens: int, tier: str) -> float:
    return tokens / 1_000_000 * PRICING[tier]["out"]


# ── Dot plot ─────────────────────────────────────────────────────────────────

def write_dotplot(per_agent: dict, options: dict, path: Path) -> None:
    """Cost per agent, one row per agent, one dot per routing option."""
    agents = sorted(per_agent, key=lambda a: -options["A"][a])
    opt_colour = {"A": "#c0392b", "B": "#2980b9", "C": "#27ae60"}

    W, H = 760, 90 + len(agents) * 56
    left, right = 170, W - 60
    max_cost = max(
        max(options[o][a] for o in options) for a in agents
    ) or 1.0

    def x(v):
        return left + (v / max_cost) * (right - left)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="-apple-system,Segoe UI,sans-serif">',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        '<text x="24" y="30" font-size="15" font-weight="600" fill="#111">'
        'Measured API cost by agent, under three routing options</text>',
        '<text x="24" y="48" font-size="11" fill="#666">'
        'Output-token cost for the workload already logged in reasoning_logs. '
        'Lower is cheaper.</text>',
    ]

    for i, agent in enumerate(agents):
        y = 86 + i * 56
        parts.append(
            f'<line x1="{left}" y1="{y}" x2="{right}" y2="{y}" '
            f'stroke="#e8e8e8" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{left - 12}" y="{y + 4}" font-size="12" fill="#333" '
            f'text-anchor="end">{agent}</text>'
        )
        for opt in ("A", "B", "C"):
            v = options[opt][agent]
            parts.append(
                f'<circle cx="{x(v):.1f}" cy="{y}" r="6.5" '
                f'fill="{opt_colour[opt]}" fill-opacity="0.8"/>'
            )
        parts.append(
            f'<text x="{right + 8}" y="{y + 4}" font-size="10" fill="#888">'
            f'${options["A"][agent]:.2f}</text>'
        )

    ly = H - 22
    for j, opt in enumerate(("A", "B", "C")):
        cx = left + j * 190
        parts.append(
            f'<circle cx="{cx}" cy="{ly - 4}" r="6.5" fill="{opt_colour[opt]}"/>'
        )
        parts.append(
            f'<text x="{cx + 13}" y="{ly}" font-size="11" fill="#333">'
            f'Option {opt}</text>'
        )

    parts.append("</svg>")
    path.write_text("\n".join(parts))


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument(
        "--calibrate",
        action="store_true",
        help="derive chars/token via the free count_tokens endpoint",
    )
    args = ap.parse_args()

    OUT_DIR.mkdir(exist_ok=True)

    print(f"Reading logged Claude calls from {args.db}")
    per_agent = measure(args.db)
    if not per_agent:
        print("No rows in reasoning_logs - nothing to measure.")
        return

    if args.calibrate:
        print("Calibrating chars-per-token (free endpoint, not billed)...")
        ratio = calibrate_ratio(args.db)
        ratio_source = "measured via count_tokens"
    else:
        ratio = DEFAULT_CHARS_PER_TOKEN
        ratio_source = "default (run with --calibrate to measure)"

    # Measured output tokens per agent.
    for agent, rec in per_agent.items():
        rec["thinking_tokens"] = round(rec["thinking_chars"] / ratio)
        rec["response_tokens"] = round(rec["response_chars"] / ratio)
        rec["output_tokens"] = rec["thinking_tokens"] + rec["response_tokens"]

    # Cost per option per agent.
    options = {
        o: {a: cost_output(r["output_tokens"], route(a, o))
            for a, r in per_agent.items()}
        for o in ("A", "B", "C")
    }

    # ── Write measured tokens ────────────────────────────────────────────────
    tok_path = OUT_DIR / "api_cost_measured_tokens.csv"
    with tok_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "agent", "calls", "thinking_tokens", "response_tokens",
            "output_tokens", "output_tokens_per_call", "thinking_budget",
        ])
        for agent in sorted(per_agent, key=lambda a: -per_agent[a]["output_tokens"]):
            r = per_agent[agent]
            w.writerow([
                agent, r["calls"], r["thinking_tokens"], r["response_tokens"],
                r["output_tokens"], round(r["output_tokens"] / r["calls"]),
                AGENT_THINKING_BUDGET.get(agent, ""),
            ])

    # ── Write option costs ───────────────────────────────────────────────────
    opt_path = OUT_DIR / "api_cost_options.csv"
    with opt_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "option", "agent", "model_tier", "output_tokens",
            "output_cost_usd",
        ])
        for opt in ("A", "B", "C"):
            for agent in sorted(per_agent):
                w.writerow([
                    opt, agent, route(agent, opt),
                    per_agent[agent]["output_tokens"],
                    round(options[opt][agent], 4),
                ])
        for opt in ("A", "B", "C"):
            w.writerow([
                opt, "TOTAL", "-",
                sum(r["output_tokens"] for r in per_agent.values()),
                round(sum(options[opt].values()), 4),
            ])

    write_dotplot(per_agent, options, OUT_DIR / "api_cost_dotplot.svg")

    # ── Console table ────────────────────────────────────────────────────────
    total_calls = sum(r["calls"] for r in per_agent.values())
    total_tokens = sum(r["output_tokens"] for r in per_agent.values())

    print()
    print("MEASURED WORKLOAD (already-logged calls, no new API spend)")
    print(f"  chars/token ratio: {ratio:.2f}  [{ratio_source}]")
    print(f"  {total_calls} calls, {total_tokens:,} output tokens")
    print()
    print(f"  {'agent':<18}{'calls':>7}{'out tokens':>13}{'per call':>10}")
    for agent in sorted(per_agent, key=lambda a: -per_agent[a]["output_tokens"]):
        r = per_agent[agent]
        print(f"  {agent:<18}{r['calls']:>7}{r['output_tokens']:>13,}"
              f"{round(r['output_tokens'] / r['calls']):>10,}")

    print()
    print("COST OF THAT WORKLOAD, PER ROUTING OPTION (output tokens only)")
    base = sum(options["A"].values())
    names = {
        "A": "all-Sonnet (current)",
        "B": "Haiku for mechanical agents",
        "C": f"tiered: thinking budget <= {HAIKU_TIER_MAX} -> Haiku",
    }
    for opt in ("A", "B", "C"):
        total = sum(options[opt].values())
        delta = "" if opt == "A" else f"   {(total - base) / base * 100:+.1f}% vs A"
        print(f"  Option {opt}  ${total:>7.2f}   {names[opt]}{delta}")

    print()
    print("Wrote:")
    for p in (tok_path, opt_path, OUT_DIR / "api_cost_dotplot.svg"):
        print(f"  {p.relative_to(Path.cwd())}")
    print()
    print("SCOPE: output tokens only. Input tokens are not recorded in")
    print("reasoning_logs, so input cost is outside what this data can measure.")


if __name__ == "__main__":
    main()
