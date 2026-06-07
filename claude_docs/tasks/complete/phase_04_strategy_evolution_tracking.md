# Phase 4 — Strategy Evolution Tracking (RL Layer)

**What**: Track not just that a strategy failed, but what specifically changed between attempt N and attempt N+1, and whether that change improved performance. This is the reinforcement learning layer.

## Step 4.1 — Add `strategy_evolutions` table

**File to modify**: `src/data/schema.py`

```sql
CREATE TABLE IF NOT EXISTS strategy_evolutions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_a       INTEGER NOT NULL,   -- previous attempt number
    attempt_b       INTEGER NOT NULL,   -- new attempt number
    strategy_id     INTEGER,
    spec_delta      TEXT,               -- JSON: what changed in the spec
    performance_delta TEXT,             -- JSON: {win_rate_change, sharpe_change, trades_change}
    outcome         TEXT,               -- improved | degraded | unchanged
    diagnosis       TEXT,               -- why the change was made
    created_at      INTEGER,
    FOREIGN KEY(strategy_id) REFERENCES strategies(id)
);
```

## Step 4.2 — Add evolution tracking in `loop1.py`

**File to modify**: `src/loop1.py`

Track the spec diff between attempts and write evolution records:
```python
# In run_loop1(), inside the attempt loop:
prev_spec = current_spec  # captured from previous attempt
# ... strategy_agent.generate() runs ...
current_spec = spec

if prev_spec is not None and attempt > 1:
    spec_delta = _compute_spec_delta(prev_spec, current_spec)
    performance_delta = _compute_perf_delta(prev_results, current_results)
    write_evolution(
        attempt_a=attempt - 1,
        attempt_b=attempt,
        strategy_id=None,  # not yet saved
        spec_delta=spec_delta,
        performance_delta=performance_delta,
        outcome=_classify_outcome(performance_delta),
        diagnosis=last_diagnosis,
        db_path=db_path,
    )
```

Add helpers:
```python
def _compute_spec_delta(spec_a: dict, spec_b: dict) -> dict:
    """Return dict of fields that changed between two specs."""
    changes = {}
    for key in set(spec_a.keys()) | set(spec_b.keys()):
        if spec_a.get(key) != spec_b.get(key):
            changes[key] = {"from": spec_a.get(key), "to": spec_b.get(key)}
    return changes

def _classify_outcome(perf_delta: dict) -> str:
    win_rate_change = perf_delta.get("win_rate_change", 0)
    if win_rate_change > 0.05:
        return "improved"
    elif win_rate_change < -0.05:
        return "degraded"
    return "unchanged"
```

## Step 4.3 — Persist evolution when strategy is finally saved

**File to modify**: `loop1.py` / after `handle_save_validated_strategy`

When a strategy is successfully saved, backfill the `strategy_id` into all evolution records from this attempt chain:
```python
# After strategy saved:
conn = schema.get_connection(db_path)
conn.execute("UPDATE strategy_evolutions SET strategy_id = ? WHERE strategy_id IS NULL", (strategy_id,))
conn.commit()
conn.close()
```

## Step 4.4 — Use evolution data to guide retries

**File to modify**: `strategy_agent.py` / `_build_system_prompt`

Add evolution context to the prompt:
```python
evolution_history = get_recent_evolutions(db_path, limit=5)  # new KB query
evolution_str = json.dumps(evolution_history) if evolution_history else "No prior evolution."
return (
    _STRATEGY_PROMPT
    ...
    .replace("{evolution_history}", evolution_str)
)
```

**Update prompt template** (`strategy_agent_v1.txt`): Add `{evolution_history}` placeholder.

## Step 4.5 — Add Phase 4 tests

**File to modify**: `tests/test_loop1.py`

New tests:
1. Two attempts with different specs produce a `strategy_evolutions` row with correct `spec_delta`.
2. Performance improvement between attempts → `outcome='improved'`.
3. Strategy ID backfilled after save.
4. Evolution history appears in strategy agent prompt when available.

## Verification checklist
- [ ] `strategy_evolutions` table created correctly
- [ ] Evolution records written each retry
- [ ] `spec_delta` correctly captures what changed (RSI period 14→20, etc.)
- [ ] `outcome` correctly classifies improved/degraded/unchanged
- [ ] Strategy agent prompt shows evolution history


## Related

- MOC: [[_tasks]]
- [[data_pipeline]]
- [[agents]]
