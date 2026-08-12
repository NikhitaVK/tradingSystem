"""
provider_comparison_trial.py — Trial 1: different AI APIs, cost vs success.

The question (implications_planning.md:77)
------------------------------------------
"1 — Different APIs (cost vs success)", scored against **Functionality** and
**Legal/Ethical (cost)**. Can a free hosted or local model do the analyst's job
well enough to replace the paid frontier API this system was built on?

There is no ADR anywhere in the repo justifying the choice of Claude, and no
mention of any alternative provider. This trial tests that foundational decision
after the fact.

Task benchmarked
----------------
The analyst evaluation (Debate CP1) against the four ground-truth cases in
.claude/rules/testing/calibration_tests.md §5. Chosen because the expected
answers are already defined, Claude's results already exist, and it is a
text-in / JSON-out task that ports cleanly across providers.

Arms
----
    claude_sonnet          hosted, paid   the incumbent -- read from
                                          reasoning_logs (measured by Trial 2)
    gemini                 hosted, free   needs a free Google AI Studio key
    deepseek               hosted, free   needs a free DeepSeek key
    ollama_llama3.2_3b     local, free    runs on this machine, fully private
    ollama_deepseek-r1_7b  local, free    "
    ollama_gemma3_4b       local, free    "

Every hosted and local endpoint here speaks the OpenAI /chat/completions shape,
so one adapter covers all of them. No new dependency: httpx is already installed.

Two naming traps worth stating plainly, because getting them wrong in a write-up
would be a factual error:
  * gemma3 is Google's OPEN model. It is NOT Gemini.
  * deepseek-r1:7b is a small DISTILLED DeepSeek, not the full model behind the
    DeepSeek API.
Neither Claude nor GPT can run in Ollama -- those weights are closed. Ollama's
"launch chatgpt" / "launch claude" commands start coding-agent CLIs, they do not
provide those models.

Only the provider varies. Every arm gets the identical prompt built by
analyst_agent._build_eval_prompt and is judged by the identical parser.

A measurement trap this harness avoids
--------------------------------------
analyst_agent._parse_eval_response falls back to verdict="fail" when it cannot
parse a response. Two of the three graded cases expect "fail", so a model
emitting pure garbage would score 2/3 correct. JSON validity is therefore
checked INDEPENDENTLY here, and unparseable responses are excluded from accuracy
and reported as a compliance failure instead.

Likewise, an arm that is unavailable is reported as "not run" -- never silently
skipped, and never given a default verdict.

Usage
-----
    python3 -m scripts.trials.provider_comparison_trial --dry-run
    python3 -m scripts.trials.provider_comparison_trial
"""
import argparse
import csv
import json
import os
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402

from config.settings import DB_PATH  # noqa: E402
import src.agents.analyst_agent as analyst_agent  # noqa: E402
from scripts.trials.prompt_version_trial import CASES, DEPLOY_VERDICTS  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[2] / "trials_out"

USER_MSG = ("Grade this strategy on the composite score and return the full "
            "JSON object specified in the response-format section.")

# ── Arms ─────────────────────────────────────────────────────────────────────
# price_in / price_out are USD per million tokens, used to compute cost per 1000
# evaluations. `trains_on_data` and `residency` are the Legal/Ethical columns --
# a free tier paid for with training rights is not the same as free.
ARMS = {
    "claude_sonnet": {
        "kind": "claude_logs",
        "model": "claude-sonnet-4-6",
        "tier": "hosted, paid",
        "price_in": 3.00, "price_out": 15.00,
        "trains_on_data": "no (API default)",
        "residency": "vendor cloud",
    },
    "gemini": {
        "kind": "openai_compat",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "key_env": "GEMINI_API_KEY",
        # gemini-2.5-flash is retired for new API keys (404). Verified working
        # on this key 2026-08-12: gemini-3.6-flash / 3.5-flash / flash-latest.
        "model": os.getenv("GEMINI_MODEL", "gemini-3.6-flash"),
        "tier": "hosted, free tier",
        # $0 is correct for the free tier, which is what this trial used. The
        # free tier is rate-limited, so paid list price is recorded separately
        # for the "what would this cost at scale" question.
        "price_in": 0.0, "price_out": 0.0,
        "paid_note": "free tier used; paid list price applies above free quota",
        "trains_on_data": "yes on the free tier",
        "residency": "vendor cloud",
        "reasoning_effort": "low",
    },
    "deepseek": {
        "kind": "openai_compat",
        "base_url": "https://api.deepseek.com/v1",
        "key_env": "DEEPSEEK_API_KEY",
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        "tier": "hosted, free grant",
        "price_in": 0.0, "price_out": 0.0,
        "trains_on_data": "check current terms",
        "residency": "vendor cloud",
    },
}

# Local models, one arm each. Ollama's whole point is that it runs many models,
# so "local" is not a single data point -- these differ enormously in capability.
# NOTE ON NAMING, because it is easy to get wrong in a write-up:
#   * gemma3 is Google's OPEN model. It is NOT Gemini -- different family.
#   * deepseek-r1:7b is a small DISTILLED DeepSeek, not the full V3/R1 model
#     served by the DeepSeek API.
# Sizes are chosen to fit 8 GB of unified memory; anything near or above that
# thrashes (the pre-existing gemma4 at 9.6 GB is unusable on this machine).
_LOCAL_MODELS = {
    "ollama_llama3.2_3b": "llama3.2:3b",
    "ollama_deepseek-r1_7b": "deepseek-r1:7b",
    "ollama_gemma3_4b": "gemma3:4b",
}
for _arm, _model in _LOCAL_MODELS.items():
    ARMS[_arm] = {
        "kind": "openai_compat",
        "base_url": "http://localhost:11434/v1",
        "key_env": None,                       # local server needs no key
        "model": _model,
        "tier": "local, free",
        "price_in": 0.0, "price_out": 0.0,
        "trains_on_data": "no — never leaves the machine",
        "residency": "local",
    }



# ── Availability ─────────────────────────────────────────────────────────────

def check_arm(name: str, cfg: dict, db_path: str) -> tuple:
    """(available: bool, note: str). Never guesses -- probes for real."""
    if cfg["kind"] == "claude_logs":
        conn = sqlite3.connect(db_path)
        n = conn.execute(
            "SELECT COUNT(*) FROM reasoning_logs WHERE agent='analyst_eval'"
        ).fetchone()[0]
        conn.close()
        if n < 12:
            return False, f"only {n} logged analyst_eval rows, need 12"
        return True, "from reasoning_logs (Trial 2 v3 arm)"

    if cfg.get("key_env"):
        if not os.getenv(cfg["key_env"]):
            return False, f"{cfg['key_env']} not set in .env"

    if cfg.get("base_url", "").startswith("http://localhost:11434"):
        try:
            r = httpx.get("http://localhost:11434/api/tags", timeout=4)
            tags = [m["name"] for m in r.json().get("models", [])]
        except Exception as e:
            return False, f"ollama server unreachable ({type(e).__name__})"
        if cfg["model"] not in tags:
            return False, (f"model '{cfg['model']}' not pulled "
                           f"(have: {', '.join(tags) or 'none'})")
        return True, f"local model {cfg['model']}"

    return True, f"key present, model {cfg['model']}"


# ── Providers ────────────────────────────────────────────────────────────────

def call_openai_compat(cfg: dict, system_prompt: str) -> dict:
    """One POST to an OpenAI-compatible /chat/completions endpoint."""
    headers = {"Content-Type": "application/json"}
    if cfg.get("key_env"):
        headers["Authorization"] = f"Bearer {os.getenv(cfg['key_env'])}"

    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": USER_MSG},
        ],
        "temperature": 1,
    }
    if cfg.get("reasoning_effort"):
        payload["reasoning_effort"] = cfg["reasoning_effort"]

    # Retry transient TRANSPORT faults only (DNS blips, dropped connections).
    # A model that answers badly is a result and is never retried -- only a
    # failure to reach it at all, which says nothing about the provider.
    t0 = time.time()
    last = None
    for attempt in range(3):
        try:
            r = httpx.post(f"{cfg['base_url']}/chat/completions", headers=headers,
                           json=payload, timeout=300)
            break
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
            last = e
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    latency = time.time() - t0
    r.raise_for_status()
    data = r.json()

    text = data["choices"][0]["message"].get("content") or ""
    usage = data.get("usage") or {}
    return {
        "text": text,
        "latency_s": latency,
        "in_tokens": usage.get("prompt_tokens"),
        "out_tokens": usage.get("completion_tokens"),
    }


def reconstructed_input_tokens(case: dict) -> int:
    """
    Input tokens for a case, reconstructed from the same prompt builder every
    arm uses. reasoning_logs never stored input tokens, so without this the
    Claude arm would be costed on output alone and would look artificially
    cheap next to arms whose API reports both halves.
    4.04 chars/token, measured on this corpus while API credit existed.
    """
    prompt = analyst_agent._build_eval_prompt(
        case["spec"], case["results"], "Knowledge base not consulted.")
    return round((len(prompt) + len(USER_MSG)) / 4.04)


def claude_from_logs(db_path: str) -> list:
    """
    The 12 Trial 2 v3-arm responses, in case order (bad, overfitted, solid,
    borderline) x 3 repeats. Validated against trials_out/prompt_version_matrix.csv:
    log ids 361-372 reproduce that arm's verdict sequence exactly.
    """
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT thinking, response FROM reasoning_logs WHERE agent='analyst_eval' "
        "ORDER BY id DESC LIMIT 12"
    ).fetchall()
    conn.close()
    rows = rows[::-1]

    out = []
    for thinking, response in rows:
        think_text = ""
        try:
            blocks = json.loads(thinking) if thinking else []
            think_text = "".join(b.get("thinking", "") for b in blocks
                                 if isinstance(b, dict))
        except (json.JSONDecodeError, TypeError):
            pass
        # 4.04 chars/token, measured on this corpus while credit existed.
        out.append({
            "text": response or "",
            "latency_s": None,          # not recorded at the time
            "in_tokens": None,          # reasoning_logs never stored input
            "out_tokens": round((len(think_text) + len(response or "")) / 4.04),
        })
    return out


# ── Scoring ──────────────────────────────────────────────────────────────────

def json_is_valid(text: str) -> bool:
    """Independent check -- the shared parser silently defaults to 'fail'."""
    try:
        analyst_agent._extract_json(text)
        return True
    except Exception:
        return False


def score_cell(text: str, case: dict) -> dict:
    valid = json_is_valid(text)
    if not valid:
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


# ── Dot plot ─────────────────────────────────────────────────────────────────

def write_dotplot(summary: dict, path: Path) -> None:
    metrics = [
        ("accuracy", "Verdict accuracy on the 3 graded cases", True, "{:.2f}"),
        ("json_compliance", "Valid JSON rate (1.0 = always parseable)", True, "{:.2f}"),
        ("consistency", "Cases where all 3 repeats agreed (of 4)", True, "{:.0f}"),
        ("cost_per_1k", "USD per 1,000 evaluations", False, "${:.2f}"),
        ("mean_latency", "Mean seconds per evaluation", False, "{:.1f}s"),
    ]
    all_names = [n for n in summary if summary[n].get("ran")]
    if not all_names:
        return
    palette = ["#E8590C", "#1C7ED6", "#2F9E44", "#9C36B5", "#F08C00", "#5F3DC4"]
    colour = {n: palette[i % len(palette)] for i, n in enumerate(summary)}
    note = {"claude_sonnet": "incumbent, paid"}

    W, panel_h, top = 980, 152, 124
    H = top + len(metrics) * panel_h + 96
    left, right = 62, W - 200

    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
         f'viewBox="0 0 {W} {H}" font-family="-apple-system,Segoe UI,Helvetica,sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
         '<text x="28" y="38" font-size="17" font-weight="700" fill="#111">'
         'Trial 1 — different AI APIs: cost vs success</text>',
         '<text x="28" y="60" font-size="12" fill="#555">'
         'Same analyst task, same prompt, same parser. Only the provider varies.</text>',
         '<text x="28" y="78" font-size="11.5" fill="#777">'
         '4 ground-truth cases x 3 repeats per arm. Unparseable responses are '
         'excluded from accuracy, not scored as "fail".</text>',
         '<text x="28" y="94" font-size="11" fill="#999">'
         'An arm missing from a panel had no measurement for that metric '
         '(the Claude arm is read from logs, which never recorded latency).</text>']

    for i, (key, label, higher, fmt) in enumerate(metrics):
        y0 = top + i * panel_h
        axis_y = y0 + 96
        # An arm with no measurement for this metric is omitted from the panel
        # rather than plotted at zero.
        names = [n for n in all_names if summary[n].get(key) is not None]
        if not names:
            continue
        vals = [summary[n][key] for n in names]
        domain = (max(vals) or 1.0) * 1.10
        direction = "higher is better →" if higher else "← lower is better"
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
                         f'fill="{colour.get(n, "#888")}" fill-opacity="0.9" '
                         f'stroke="#fff" stroke-width="1.5"/>')
                if dy:
                    p.append(f'<line x1="{x:.1f}" y1="{axis_y}" x2="{x:.1f}" '
                             f'y2="{axis_y + dy}" stroke="{colour.get(n, "#888")}" '
                             f'stroke-width="1" stroke-opacity="0.3"/>')
            base = axis_y + top_dy - 13
            for j, (v, n, x) in enumerate(c["items"]):
                p.append(f'<text x="{c["x"]:.1f}" y="{base - j * 12:.1f}" '
                         f'font-size="9.5" fill="{colour.get(n, "#888")}" '
                         f'font-weight="600" text-anchor="middle">{fmt.format(v)}</text>')
        best = max(vals) if higher else min(vals)
        win = [n for n in names if summary[n][key] == best]
        p.append(f'<text x="{right + 16}" y="{axis_y + 4}" font-size="9.5" '
                 f'fill="#666">best: {"/".join(win)}</text>')

    ly = H - 42
    p.append(f'<line x1="28" y1="{ly - 22}" x2="{W - 28}" y2="{ly - 22}" '
             f'stroke="#eee" stroke-width="1"/>')
    for j, n in enumerate(all_names):
        cx = 34 + (j % 2) * 440
        cy = ly + (j // 2) * 18
        p.append(f'<circle cx="{cx}" cy="{cy - 4}" r="6" fill="{colour.get(n, "#888")}"/>')
        extra = ('  — ' + note[n]) if n in note else ''
        p.append(f'<text x="{cx + 12}" y="{cy}" font-size="10" fill="#333">'
                 f'{n}{extra}  ({summary[n]["tier"]}){extra and ""}</text>')
    p.append("</svg>")
    path.write_text("\n".join(p))


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--arms", help="comma-separated subset of arm names")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tag", default="", help="suffix for output filenames")
    args = ap.parse_args()
    OUT_DIR.mkdir(exist_ok=True)

    wanted = args.arms.split(",") if args.arms else list(ARMS)
    availability = {}
    print("Arm availability")
    for name in wanted:
        cfg = ARMS[name]
        ok, note = check_arm(name, cfg, args.db)
        availability[name] = (ok, note)
        print(f"  {'OK  ' if ok else 'SKIP'} {name:<15} {cfg['tier']:<18} {note}")

    live = [n for n in wanted if availability[n][0]]
    n_calls = sum(len(CASES) * args.repeats for n in live
                  if ARMS[n]["kind"] != "claude_logs")
    print(f"\n{len(CASES)} cases x {args.repeats} repeats x {len(live)} arms")
    print(f"New API calls needed: {n_calls}   (Claude arm reads from the DB)")
    if args.dry_run:
        print("\nDry run — nothing called.")
        return
    if not live:
        print("\nNo arms available. Nothing to run.")
        return

    rows = []
    for name in live:
        cfg = ARMS[name]
        print(f"\n{name} ({cfg['tier']})")

        if cfg["kind"] == "claude_logs":
            responses = claude_from_logs(args.db)
            idx = 0
            for case_name, case in CASES.items():
                for rep in range(1, args.repeats + 1):
                    r = responses[idx]; idx += 1
                    sc = score_cell(r["text"], case)
                    rows.append({"arm": name, "case": case_name, "repeat": rep,
                                 "error": "", **sc,
                                 "in_tokens": reconstructed_input_tokens(case),
                                 "out_tokens": r["out_tokens"] or "",
                                 "latency_s": ""})
                    print(f"  {case_name:<12} rep{rep}  {sc['verdict'] or 'UNPARSEABLE'}")
            continue

        for case_name, case in CASES.items():
            for rep in range(1, args.repeats + 1):
                prompt = analyst_agent._build_eval_prompt(
                    case["spec"], case["results"], "Knowledge base not consulted.")
                try:
                    r = call_openai_compat(cfg, prompt)
                    sc = score_cell(r["text"], case)
                    rows.append({"arm": name, "case": case_name, "repeat": rep,
                                 "error": "", **sc,
                                 "in_tokens": r["in_tokens"] or "",
                                 "out_tokens": r["out_tokens"] or "",
                                 "latency_s": round(r["latency_s"], 2)})
                    print(f"  {case_name:<12} rep{rep}  "
                          f"{sc['verdict'] or 'UNPARSEABLE':<12} "
                          f"{r['latency_s']:.1f}s")
                except Exception as e:
                    msg = f"{type(e).__name__}: {str(e)[:120]}"
                    # Never invent a verdict for a failed call.
                    rows.append({"arm": name, "case": case_name, "repeat": rep,
                                 "error": msg, "json_valid": "", "verdict": "",
                                 "score": "", "deployed": "", "correct": "",
                                 "in_tokens": "", "out_tokens": "", "latency_s": ""})
                    print(f"  {case_name:<12} rep{rep}  FAILED  {msg}")

    tag = f"_{args.tag}" if args.tag else ""
    matrix = OUT_DIR / f"provider_comparison_matrix{tag}.csv"
    with matrix.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    # ── Aggregate ────────────────────────────────────────────────────────────
    summary = {}
    for name in wanted:
        cfg = ARMS[name]
        if not availability[name][0]:
            summary[name] = {"ran": False, "tier": cfg["tier"],
                             "not_run_reason": availability[name][1]}
            continue
        rs = [r for r in rows if r["arm"] == name]
        graded = [r for r in rs if r["correct"] != ""]
        valid = [r for r in rs if r["json_valid"] is True]
        errs = [r for r in rs if r["error"]]
        lat = [r["latency_s"] for r in rs if r["latency_s"] != ""]
        agree = sum(1 for c in CASES
                    if len({r["verdict"] for r in rs if r["case"] == c
                            and r["json_valid"] is True}) == 1)
        in_tok = [r["in_tokens"] for r in rs if r["in_tokens"] != ""]
        out_tok = [r["out_tokens"] for r in rs if r["out_tokens"] != ""]
        mean_in = statistics.mean(in_tok) if in_tok else 0
        mean_out = statistics.mean(out_tok) if out_tok else 0
        cost_1k = (mean_in / 1e6 * cfg["price_in"]
                   + mean_out / 1e6 * cfg["price_out"]) * 1000
        n_correct = sum(1 for r in graded if r["correct"] == "True")
        summary[name] = {
            "ran": True,
            "tier": cfg["tier"],
            "model": cfg["model"],
            "n_cells": len(rs),
            "errors": len(errs),
            # Denominator is RESPONSES RECEIVED, not cells attempted. A call
            # that never reached the model (DNS/connection failure) is not a
            # formatting failure and must not be charged against the provider.
            "responses": len(rs) - len(errs),
            "json_compliance": (round(len(valid) / (len(rs) - len(errs)), 3)
                                if (len(rs) - len(errs)) else None),
            "accuracy": round(n_correct / len(graded), 3) if graded else 0.0,
            "correct": f"{n_correct}/{len(graded)}",
            "consistency": agree,
            "mean_in_tokens": round(mean_in),
            "mean_out_tokens": round(mean_out),
            "cost_per_1k": round(cost_1k, 4),
            # None, not 0.0: the Claude arm comes from logs that never recorded
            # latency. Reporting 0.0 would claim it is instant.
            "mean_latency": round(statistics.mean(lat), 2) if lat else None,
            "trains_on_data": cfg["trains_on_data"],
            "residency": cfg["residency"],
        }
        if graded and n_correct:
            summary[name]["cost_per_correct"] = round(
                cost_1k / 1000 / (n_correct / len(graded)), 6)

    (OUT_DIR / f"provider_comparison_summary{tag}.json").write_text(json.dumps(
        {"summary": summary, "cases": list(CASES),
         "repeats": args.repeats,
         "decision_rule": (
             "Adopt the cheapest arm that matches the incumbent's accuracy, "
             "returns valid JSON 100% of the time, and does not require "
             "granting training rights. Ties go to the incumbent."),
         "note_on_parse_failures": (
             "analyst_agent._parse_eval_response defaults to verdict='fail' on "
             "a parse error, and 2 of 3 graded cases expect 'fail'. JSON "
             "validity is checked independently here; unparseable responses are "
             "excluded from accuracy and counted as compliance failures.")},
        indent=2))
    write_dotplot(summary, OUT_DIR / f"provider_comparison_dotplot{tag}.svg")

    hdr = (f"  {'arm':<24}{'correct':>9}{'json':>7}{'consist':>9}"
           f"{'$/1k':>9}{'latency':>9}{'errors':>8}")
    print("\n" + hdr)
    print("  " + "-" * (len(hdr) - 2))
    for name in wanted:
        s = summary[name]
        if not s.get("ran"):
            print(f"  {name:<15}  NOT RUN — {s['not_run_reason']}")
            continue
        lat = "n/a" if s["mean_latency"] is None else f"{s['mean_latency']:.1f}s"
        print(f"  {name:<24}{s['correct']:>9}{s['json_compliance']:>7.2f}"
              f"{s['consistency']:>6}/4{s['cost_per_1k']:>9.2f}"
              f"{lat:>9}{s['errors']:>8}")

    print("\nWrote provider_comparison_matrix.csv, provider_comparison_summary.json,")
    print("      provider_comparison_dotplot.svg to trials_out/")


if __name__ == "__main__":
    main()
