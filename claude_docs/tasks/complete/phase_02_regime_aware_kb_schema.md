# Phase 2 — Regime-Aware KB Schema

**What**: Add regime context and mechanism fields to KB entries, so failures are stored with the market conditions that caused them.

## Step 2.1 — Add KB columns

**File to modify**: `src/data/schema.py`

Add 5 new columns to `knowledge_base` table:
```sql
ALTER TABLE knowledge_base ADD COLUMN regime TEXT;           -- e.g. 'trending_bull', 'sideways'
ALTER TABLE knowledge_base ADD COLUMN mechanism TEXT;       -- e.g. 'mean_reversion', 'momentum'
ALTER TABLE knowledge_base ADD COLUMN conditions TEXT;       -- JSON: e.g. '{"atr": 0.8, "adx": 18}'
ALTER TABLE knowledge_base ADD COLUMN layer TEXT DEFAULT 'shallow';  -- shallow|intermediate|deep
ALTER TABLE knowledge_base ADD COLUMN importance INTEGER DEFAULT 50; -- 0-100 score
```

**Note**: `ALTER TABLE ADD COLUMN` is safe in SQLite — it just adds the column to new rows. Existing rows will have NULL for the new columns (handle with `COALESCE` in reads).

## Step 2.2 — Update `write_finding`

**File to modify**: `src/data/knowledge_base.py`

New signature:
```python
def write_finding(
    category: str,
    content: str,
    db_path: str,
    strategy_id: int = None,
    regime: str = None,
    mechanism: str = None,
    conditions: dict = None,
    layer: str = "shallow",
    importance: int = 50,
) -> int:
```

**Layer assignment** (from FinMem paper, adapted):
- `failure_diagnosis` + high importance (≥70) → `deep`
- `failure_diagnosis` + lower importance → `intermediate`
- `market_regime` observations → `shallow`
- `parameter_insight` → `intermediate`
- `general` → `shallow`

**Importance assignment** (stochastic, from FinMem):
- Base values {40, 60, 80} assigned with probability distribution per layer
- Decays over time: `importance_t = importance_0 * (decay_rate ** days_elapsed)`
- Layer decay rates: `shallow=0.9/day`, `intermediate=0.967/day`, `deep=0.988/day`

## Step 2.3 — Update `query_relevant`

**File to modify**: `src/data/knowledge_base.py`

Add regime-filtering:
```python
def query_relevant(
    keywords: list,
    db_path: str,
    limit: int = 10,
    category: str = None,
    regime: str = None,        # NEW
    layer: str = None,         # NEW
    min_importance: int = 5,   # NEW — purge threshold
) -> list:
```

Also add `importance` column to the SELECT so it can be returned and used for scoring.

## Step 2.4 — Update `loop1.py` KB query to use regime

**File to modify**: `src/loop1.py:65-68`

Current:
```python
kb_context = query_relevant(
    ["failure", "regime", "overfitting", "mechanism", "RSI", "EMA", "MACD"],
    db_path, limit=10,
)
```

New:
```python
current_regime = _detect_current_regime(db_path)  # from live_candles or latest slice
kb_context = query_relevant(
    ["failure", "regime", "overfitting", "mechanism", "RSI", "EMA", "MACD"],
    db_path,
    limit=10,
    regime=current_regime,          # get failures from same regime first
    layer=None,                      # get from all layers (Top-K per layer in Phase 6)
    min_importance=5,
)
```

Add helper `_detect_current_regime()` that reads the most recent `live_candles` or `ohlcv_history` and runs the HMM classifier on it to get the current regime.

## Step 2.5 — Update analyst reflection to write regime context

**File to modify**: `src/agents/analyst_agent.py` / `reflect()`

When calling `write_to_knowledge_base`, include the detected regime:
```python
# In reflect(), after diagnosis is generated:
handle_write_to_knowledge_base({
    "category": "failure_diagnosis",
    "content": diagnosis,
    "strategy_id": strategy.get("id"),
    "regime": _detect_current_regime(db_path),       # ADD
    "mechanism": strategy_spec.get("entry_mechanism", "unknown"),  # ADD
    "conditions": json.dumps(current_conditions),     # ADD — ATR, ADX, etc.
}, db_path)
```

This requires:
- Getting current market conditions (ATR, ADX, etc.) from `live_candles` or `ohlcv_history`
- The strategy spec needs an `entry_mechanism` field — add to `_normalise_spec` if missing

## Step 2.6 — Add Phase 2 isolation tests

**File to create**: `tests/test_knowledge_base.py`

New tests:
1. `write_finding` with all new fields persists correctly
2. `query_relevant` with `regime=` filter returns only matching regime entries
3. `query_relevant` with `min_importance=5` excludes low-importance entries
4. Layer assignment follows the stochastic distribution approximately
5. Importance decays correctly over time simulation
6. NULL handling on old rows (pre-schema-addition): `COALESCE(regime, 'unknown')` returns known regimes for old rows

## Verification checklist
- [ ] New columns exist in `knowledge_base` table (check `PRAGMA table_info`)
- [ ] `write_finding` with `regime=` persists and `query_relevant(regime=...)` retrieves it
- [ ] Old KB entries (before migration) return `'unknown'` for regime (COALESCE)
- [ ] Analyst `reflect()` writes regime context on degradation


## Related

- MOC: [[_tasks]]
- [[2026-04-15-hmm-regime-detection]]
- [[data_pipeline]]
