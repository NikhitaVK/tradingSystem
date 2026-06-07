# Phase 3 — Regime Match Pre-Check Before Backtesting

**What**: Before the strategy agent runs a backtest, check whether the current regime has historically killed similar strategies. Surface a warning before the expensive backtest runs.

## Step 3.1 — Add `regime_failures` query helper

**File to modify**: `src/data/knowledge_base.py`

```python
def query_regime_failures(
    regime: str,
    mechanism: str,
    db_path: str,
) -> list[dict]:
    """
    Return KB entries where a strategy with the given mechanism
    failed in the given regime. Used for pre-backtest warning.
    """
    conn = schema.get_connection(db_path)
    rows = conn.execute("""
        SELECT id, content, strategy_id, created_at
        FROM knowledge_base
        WHERE regime = ?
          AND mechanism = ?
          AND category = 'failure_diagnosis'
        ORDER BY importance DESC, created_at DESC
        LIMIT 5
    """, (regime, mechanism)).fetchall()
    conn.close()
    return [dict(row) for row in rows]
```

## Step 3.2 — Add pre-backtest check in `handle_run_backtest`

**File to modify**: `src/agents/tools.py` / `handle_run_backtest`

```python
def handle_run_backtest(args: dict, db_path: str) -> str:
    spec = _normalise_spec(args["strategy_spec"])
    mechanism = _infer_mechanism(spec)  # NEW — extract mechanism label from spec

    # Pre-backtest regime check
    current_regime = _detect_current_regime_from_db(db_path)
    failures = query_regime_failures(current_regime, mechanism, db_path)
    if failures:
        logger.warning(
            "Regime match warning: %d prior failures for mechanism=%s in regime=%s",
            len(failures), mechanism, current_regime
        )

    try:
        result = _run_backtest(spec, db_path)
        # ... rest unchanged
```

Add `_infer_mechanism(spec)` — extracts a simple mechanism label from the strategy spec entry conditions (e.g. "RSI < 30" → "mean_reversion", "price > EMA" → "momentum").

## Step 3.3 — Return regime warnings in tool result

**File to modify**: `handle_run_backtest` return dict

Add to the return:
```python
{
    "viable": result.get("viable"),
    "aggregate": result.get("aggregate"),
    "slices": result.get("slices"),
    "calibration": result.get("calibration"),
    "regime_warning": len(failures) > 0,       # NEW
    "regime_warning_count": len(failures),     # NEW
    "prior_failures": [f["content"][:200] for f in failures],  # NEW — first 200 chars
}
```

## Step 3.4 — Update strategy agent prompt injection

**File to modify**: `src/agents/strategy_agent.py` / `_build_system_prompt`

Inject the regime warning into the prompt so Claude sees it before designing the strategy:
```python
regime_warning = (
    f"\n\nREGIME WARNING: This strategy's mechanism has failed {len(failures)} times "
    f"in the current regime ('{current_regime}'). Prior failure summary:\n"
    + "\n".join(f"- {f['content'][:200]}" for f in failures)
    if failures else ""
)
return (
    _STRATEGY_PROMPT
    .replace("{kb_context}", kb_str)
    .replace("{pair_candidates}", pairs_str)
    .replace("{previous_diagnosis}", diag_str)
    .replace("{regime_warning}", regime_warning)  # NEW
)
```

**Update the prompt template** (`prompts/strategy_agent_v1.txt`): Add a `The current market regime is {current_regime}. {regime_warning}` placeholder near the start of the prompt.

## Step 3.5 — Add Phase 3 isolation tests

**File to modify**: `tests/test_loop1.py`

New tests:
1. When KB has a `failure_diagnosis` with `regime='sideways'` and `mechanism='mean_reversion'`, and current regime is `'sideways'`, assert `regime_warning` is True in backtest result.
2. When KB has a failure but current regime differs, assert `regime_warning` is False.
3. Assert that when `regime_warning` is True, the strategy agent's system prompt contains the warning text.

## Verification checklist
- [ ] Pre-backtest regime check fires when matching failures exist
- [ ] `regime_warning` appears in backtest result JSON
- [ ] Strategy agent prompt contains warning text when applicable
- [ ] Test: no warning when no prior failures in current regime


## Related

- MOC: [[_tasks]]
- [[2026-04-15-hmm-regime-detection]]
- [[agents]]
