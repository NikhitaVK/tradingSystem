# Phase 10 — Adversarial Regime Robustness in CP1 Analyst

**What**: Extend the analyst's CP1 evaluation to explicitly challenge regime robustness. Currently the analyst challenges mechanism, WFE, cost survival, and statistical significance. Phase 10 adds a mandatory adversarial challenge: "If the market regime shifts from the detected regime to a different one, does this strategy fail?"

**Why this matters**: The Phase 1 HMM will detect which regime each backtest slice operated in and tag them. The analyst should then explicitly evaluate whether the strategy is robust to regime changes — i.e., is it likely to survive if the market shifts from trending to range-bound or vice versa. This is a key question human traders ask before committing capital, and it is not currently evaluated anywhere in the system.

## Step 10.1 — Update analyst evaluation prompt

**File to modify**: `prompts/analyst_eval_v1.txt`

Add a fifth adversarial criterion to the evaluation framework:

```
FIFTH CRITERION — REGIME ROBUSTNESS:
Review the regime_breakdown from the backtest results.
  - Which regimes did this strategy operate in successfully?
  - If the strategy passed with mostly 'sideways' slices, what happens if the market shifts to 'trending_bull'?
  - If it passed in 'trending_bull', does it survive 'sideways' or 'trending_bear'?
  - Strategies that only work in one regime are fragile — flag any regime single-home bias.

For each regime in regime_breakdown, evaluate: if the market were to shift to a different regime,
what is the expected performance impact based on the strategy's mechanism?
```

## Step 10.2 — Update `_parse_eval_response`

**File to modify**: `src/agents/analyst_agent.py` / `_parse_eval_response`

Add `regime_robustness` to the returned evaluation dict:
```python
def _parse_eval_response(response_text: str) -> dict:
    try:
        data = _extract_json(response_text)
        return {
            "pass": bool(data.get("pass", False)),
            "diagnosis": str(data.get("diagnosis", "")),
            "challenges": list(data.get("challenges", [])),
            "regime_robustness": {   # NEW
                "dominant_regime": data.get("dominant_regime", "unknown"),
                "fragile_regimes": data.get("fragile_regimes", []),
                "failure_risk": data.get("failure_risk", "unknown"),
            },
        }
    except Exception:
        ...
```

## Step 10.3 — Persist regime robustness in KB on failure

**File to modify**: `src/agents/analyst_agent.py` / `evaluate()`

When `pass=False` and the diagnosis mentions regime fragility, include regime robustness data in the KB failure write:
```python
# In loop1.py, when writing failure diagnosis:
write_finding(
    category="failure_diagnosis",
    content=(
        f"Attempt {attempt} failure.\n"
        f"Strategy: {spec.get('name', 'unnamed')}\n"
        f"Diagnosis: {diagnosis}\n"
        f"Challenges: {json.dumps(eval_result['challenges'])}\n"
        f"Regime robustness: {json.dumps(eval_result.get('regime_robustness', {}))}"  # NEW
    ),
    db_path=db_path,
    regime=eval_result.get("regime_robustness", {}).get("dominant_regime"),  # NEW
)
```


## Related

- MOC: [[_tasks]]
- [[2026-04-15-hmm-regime-detection]]
- [[backtesting]]
