# Phase 6 — Layered Memory Architecture

**What**: Implement FinMem's three-layer memory with importance scoring, layer promotion, **downward migration**, content-type enforcement, and **feedback-driven importance updates** from trading outcomes — not just categorisation.

**Content type enforcement per layer** (from FinMem comparison):
| Layer | Retention | Content Type | Example |
|---|---|---|---|
| `shallow` | ~14 days | Market regime observations, short-term signals, daily findings | "ATR dropped below 1.0 indicating low volatility regime" |
| `intermediate` | ~90 days | Parameter insights, mechanism findings, quarterly patterns | "RSI period 14 produces better Sharpe than period 20 on BTC 1h" |
| `deep` | ~365 days | Failure diagnoses, regime-aware failures, strategic learnings, LLM-generated rationales | "Mean reversion failed in trending bull regime — mechanism mismatch" |

## Step 6.1 — Add layer promotion logic

**File to create**: `src/data/memory_layers.py`

```python
"""
memory_layers.py — FinMem-inspired layered memory management.

Three layers with content-type enforcement:
  shallow      (Q=14 days)   — daily regime observations, short-term signals
  intermediate (Q=90 days)   — parameter insights, mechanism findings
  deep         (Q=365 days)  — failure diagnoses, strategic learnings, rationales

Importance scoring (from FinMem):
  Base values {40, 60, 80} assigned stochastically per layer probability.
  Importance decays: I_t = I_0 * (alpha_l ^ delta_days)
  Layer alpha: shallow=0.90/day, intermediate=0.967/day, deep=0.988/day

Compound score (for ranking within layer):
  S_compound = S_recency + I_current / 100
  where I_current = compute_importance_score(initial, created_at, layer)

Jump thresholds (from FinMem):
  shallow → intermediate: I >= 60 and recency > 0.7
  intermediate → deep:    I >= 80 and recency > 0.7
  deep → intermediate:    I < 80
  intermediate → shallow: I < 60
  (never demote from shallow — it has no lower threshold)
"""

import time
import random
import math
import json

LAYER_CONFIG = {
    "shallow":       {"q": 14,  "alpha": 0.90},
    "intermediate":  {"q": 90,  "alpha": 0.967},
    "deep":          {"q": 365, "alpha": 0.988},
}

LAYER_BASE_VALUES = [40, 60, 80]
LAYER_PROBABILITIES = {
    "shallow":       [0.80, 0.15, 0.05],
    "intermediate":  [0.05, 0.80, 0.15],
    "deep":          [0.05, 0.15, 0.80],
}

# Jump thresholds (from FinMem config)
JUMP_THRESHOLD_UPPER = {"shallow": 60, "intermediate": 80, "deep": 999999999}
JUMP_THRESHOLD_LOWER = {"shallow": -999999999, "intermediate": 60, "deep": 80}

PURGE_IMPORTANCE_THRESHOLD = 5
PURGE_RECENCY_THRESHOLD = 0.05

LAYER_CONTENT_TYPES = {
    "shallow":       ["market_regime", "general"],
    "intermediate":  ["parameter_insight"],
    "deep":          ["failure_diagnosis"],
}


def assign_layer_and_importance(category: str, content: str) -> tuple[str, int]:
    """
    Assign initial layer and importance to a new KB entry.
    Returns (layer: str, importance: int).
    Enforces content-type per layer.
    """
    if category == "failure_diagnosis":
        layer = "deep"
    elif category == "parameter_insight":
        layer = "intermediate"
    else:  # market_regime, general
        layer = "shallow"

    base = random.choices(LAYER_BASE_VALUES, weights=LAYER_PROBABILITIES[layer], k=1)[0]
    return layer, base


def compute_recency_score(entry_created_at_ms: int, layer: str) -> float:
    """
    S_Recency = e^(-delta / Q_l)
    delta = current_time - entry_time (in days)
    Q_l = layer stability period
    """
    delta_days = (time.time() * 1000 - entry_created_at_ms) / (1000 * 60 * 60 * 24)
    q = LAYER_CONFIG[layer]["q"]
    return math.exp(-delta_days / q)


def compute_importance_score(initial_importance: int, entry_created_at_ms: int, layer: str) -> float:
    """
    I_t = I_0 * (alpha_l ^ delta_days)
    """
    delta_days = (time.time() * 1000 - entry_created_at_ms) / (1000 * 60 * 60 * 24)
    alpha = LAYER_CONFIG[layer]["alpha"]
    return initial_importance * (alpha ** delta_days)


def compute_compound_score(entry_created_at_ms: int, layer: str, importance: int) -> float:
    """
    FinMem compound score: S_compound = S_recency + I_current / 100
    Used for ranking entries within a layer before returning Top-K.
    """
    recency = compute_recency_score(entry_created_at_ms, layer)
    current_importance = compute_importance_score(importance, entry_created_at_ms, layer)
    return recency + current_importance / 100


def should_purge(entry_created_at_ms: int, layer: str, importance: int) -> bool:
    """True if entry should be purged from KB."""
    if importance < PURGE_IMPORTANCE_THRESHOLD:
        return True
    recency = compute_recency_score(entry_created_at_ms, layer)
    if recency < PURGE_RECENCY_THRESHOLD:
        return True
    return False


def check_promotion(entry_created_at_ms: int, layer: str, importance: int) -> str | None:
    """
    Check if an entry meets criteria for upward layer promotion.
    Returns the target layer or None.

    From FinMem jump thresholds:
      shallow → intermediate: I >= 60 AND recency > 0.7
      intermediate → deep:    I >= 80 AND recency > 0.7
    """
    recency = compute_recency_score(entry_created_at_ms, layer)
    current_importance = compute_importance_score(importance, entry_created_at_ms, layer)
    if layer == "shallow" and current_importance >= JUMP_THRESHOLD_UPPER["shallow"] and recency > 0.7:
        return "intermediate"
    if layer == "intermediate" and current_importance >= JUMP_THRESHOLD_UPPER["intermediate"] and recency > 0.7:
        return "deep"
    return None


def check_demotion(entry_created_at_ms: int, layer: str, importance: int) -> str | None:
    """
    Check if an entry meets criteria for downward layer demotion.
    Returns the target layer or None.

    From FinMem jump thresholds:
      deep → intermediate:  I < 80
      intermediate → shallow: I < 60
    Note: shallow cannot demote (no lower layer).
    """
    if layer == "shallow":
        return None  # shallow is the bottom — never demote
    current_importance = compute_importance_score(importance, entry_created_at_ms, layer)
    if layer == "deep" and current_importance < JUMP_THRESHOLD_LOWER["deep"]:
        return "intermediate"
    if layer == "intermediate" and current_importance < JUMP_THRESHOLD_LOWER["intermediate"]:
        return "shallow"
    return None
```

## Step 6.2 — Add KB purge job

**File to modify**: `src/data/knowledge_base.py`

Add to `write_finding` or a new `purge_kb()` function:
```python
def purge_kb(db_path: str) -> int:
    """
    Run after each write_finding call. Purges entries that have
    decayed below importance threshold or recency threshold.
    Returns count of purged entries.
    """
    conn = schema.get_connection(db_path)
    rows = conn.execute("SELECT id, created_at, layer, importance FROM knowledge_base").fetchall()
    purged = 0
    for row in rows:
        if should_purge(row["created_at"], row["layer"], row["importance"]):
            conn.execute("DELETE FROM knowledge_base WHERE id = ?", (row["id"],))
            purged += 1
    conn.commit()
    conn.close()
    return purged
```

## Step 6.3 — Add layer promotion and demotion job

**File to modify**: `src/data/knowledge_base.py`

```python
def promote_memories(db_path: str) -> int:
    """
    Check all KB entries for layer promotion/demotion criteria.
    Runs 2 iterations per call (FinMem pattern — cascading jumps may require
    two passes: e.g., deep→intermediate→shallow in one step).
    Returns count of changed entries.
    """
    from src.data.memory_layers import check_promotion, check_demotion, compute_importance_score

    changed = 0
    for _ in range(2):  # FinMem 2-iteration cascade
        conn = schema.get_connection(db_path)
        rows = conn.execute(
            "SELECT id, created_at, layer, importance FROM knowledge_base"
        ).fetchall()

        for row in rows:
            new_layer = None
            new_importance = None

            # Check promotion first (higher takes priority)
            promo = check_promotion(row["created_at"], row["layer"], row["importance"])
            # Check demotion
            demo = check_demotion(row["created_at"], row["layer"], row["importance"])

            # Priority: promote > demote if both conditions somehow met
            if promo:
                new_layer = promo
                new_importance = 80  # reset importance to 80 on promotion
            elif demo:
                new_layer = demo
                new_importance = 60  # reset importance to 60 on demotion (anchor point)

            if new_layer:
                conn.execute(
                    "UPDATE knowledge_base SET layer = ?, importance = ? WHERE id = ?",
                    (new_layer, new_importance, row["id"])
                )
                changed += 1

        conn.commit()
        conn.close()
    return changed
```

## Step 6.4 — Update `loop1.py` to run purge/promote/demote after each cycle

**File to modify**: `src/loop1.py` / end of `run_loop1()`

After the strategy is saved (or MaxAttemptsExceeded raised), run memory maintenance:
```python
# End of run_loop1(), before returning strategy dict:
try:
    from src.data.knowledge_base import purge_kb, promote_memories
    purged = purge_kb(db_path)
    changed = promote_memories(db_path)  # promotion + demotion combined
    if purged or changed:
        logger.info("Memory maintenance: %d purged, %d layer-changed", purged, changed)
except Exception as e:
    logger.warning("Memory maintenance failed: %s", e)
```

## Step 6.5 — Add Phase 6 tests

**File to modify**: `tests/test_knowledge_base.py`

New tests:
1. `assign_layer_and_importance` for `failure_diagnosis` → layer is `'deep'`.
2. `compute_recency_score` decays correctly over simulated time (e.g. after 7 days shallow recency should be ~0.59).
3. `should_purge` returns True when importance < 5 or recency < 0.05.
4. `check_promotion` returns `'intermediate'` for high-importance shallow entries (I >= 60, recency > 0.7).
5. `check_demotion` returns `'intermediate'` for deep entries with I < 80. Returns `'shallow'` for intermediate entries with I < 60. Returns None for shallow (cannot demote).
6. `purge_kb` actually deletes entries below threshold.
7. `promote_memories` correctly promotes AND demotes within the same run. Cascade: a deep entry with I < 60 demotes to intermediate (I < 60 demotes again to shallow in second iteration).
8. Compound score correctly ranks higher-importance entries above lower ones within the same layer.

## Verification checklist
- [ ] `assign_layer_and_importance` assigns correct layer per category
- [ ] Importance decays over simulated time
- [ ] Low-importance entries are purged by `purge_kb`
- [ ] High-signal entries promote from shallow → intermediate → deep
- [ ] Low-signal entries demote from deep → intermediate → shallow
- [ ] 2-iteration cascade works (deep→shallow in one promote_memories call)
- [ ] Memory maintenance runs without error after each Loop 1 cycle

## Step 6.6 — Feedback-Driven Importance Updates (RL Signal)

**What**: After a trade resolves (win/loss), update the `importance` of KB entries that informed the decision that led to that trade. This is the core RL signal — FinMem models this through `access_counter` + trading feedback. Without this, the KB only learns from failures, never from successes.

**New file**: `src/data/memory_feedback.py`

```python
"""
memory_feedback.py — Feedback-driven importance updates from trading outcomes.

After a trade resolves, the KB entries that were retrieved and used in the
decision that produced that trade have their importance boosted (if profitable)
or left unchanged (if losing — they naturally decay over time).

This is the reinforcement learning signal that makes the memory system adaptive,
not just a passive store. FinMem's access_counter mechanism achieves the same effect.

The feedback loop:
  Trade placed → KB entries retrieved → decision made → trade resolves
  → win: boost importance of contributing KB entries
  → loss: no boost (they decay naturally)
  → importance grows for memories that consistently contribute to good decisions
  → high-importance memories promote to deeper layers
  → low-importance memories demote or get purged
"""

FEEDBACK_IMPORTANCE_BOOST = 5  # per successful access (matches FinMem's +5 per access)


def update_importance_from_feedback(
    kb_entry_ids: list[int],
    outcome: str,  # 'win' | 'loss' | 'open'
    db_path: str,
) -> int:
    """
    Update importance of KB entries based on trading outcome.

    Win:  importance = MIN(importance + FEEDBACK_IMPORTANCE_BOOST, 100)
    Loss: no change (natural decay handles this over time)
    Open: no change (trade not yet resolved)

    Returns count of updated entries.
    """
    if outcome not in ("win",):
        return 0  # only boost on confirmed wins

    conn = schema.get_connection(db_path)
    placeholders = ",".join(["?"] * len(kb_entry_ids))
    updated = conn.execute(f"""
        UPDATE knowledge_base
        SET importance = MIN(importance + ?, 100)
        WHERE id IN ({placeholders})
    """, [FEEDBACK_IMPORTANCE_BOOST] + kb_entry_ids).rowcount
    conn.commit()
    conn.close()
    return updated
```

**New column in `strategy_evolutions` table** — track KB entries used per attempt:

```sql
ALTER TABLE strategy_evolutions ADD COLUMN kb_entries_used TEXT;
-- JSON array of KB entry IDs retrieved and passed to strategy agent during this attempt
-- Used to trace which memories informed each decision for feedback propagation
```

**Where to call `update_importance_from_feedback`**:

1. **When trade resolves (Loop 2)** — after a trade's `outcome` transitions from `'open'` to `'win'` or `'loss'` in the `trades` table, look up which strategy was used and find the corresponding `strategy_evolutions` record for that attempt cycle. Extract `kb_entries_used`, call `update_importance_from_feedback()`.

   In `src/loop2.py` or wherever trade resolution is handled:
   ```python
   from src.data.memory_feedback import update_importance_from_feedback

   # When trade closes:
   if trade["outcome"] in ("win", "loss"):
       evolutions = conn.execute("""
           SELECT kb_entries_used FROM strategy_evolutions
           WHERE strategy_id = ?
           ORDER BY created_at DESC LIMIT 1
       """, (trade["strategy_id"],)).fetchone()
       if evolutions and evolutions["kb_entries_used"]:
           kb_ids = json.loads(evolutions["kb_entries_used"])
           update_importance_from_feedback(kb_ids, trade["outcome"], db_path)
   ```

2. **When degradation triggers (Loop 2)** — the `analyst_agent.reflect()` writes a failure diagnosis. The KB entries used in that reflection cycle should also be tracked and available for feedback if the next attempt produces a winning trade.

## Step 6.7 — Add Phase 6.6 tests

**File to modify**: `tests/test_knowledge_base.py`

New tests:
1. `update_importance_from_feedback` with `outcome='win'` increases importance by exactly 5 for the targeted KB entries.
2. `update_importance_from_feedback` with `outcome='loss'` leaves importance unchanged.
3. `update_importance_from_feedback` with `outcome='open'` returns 0 and makes no changes.
4. Importance cannot exceed 100 (capped at max).
5. Only targeted KB entry IDs are updated — other entries are unaffected.
6. `strategy_evolutions` table stores `kb_entries_used` as a JSON array.

## Verification checklist
- [ ] Win outcome boosts importance of contributing KB entries by exactly 5
- [ ] Loss outcome does not change importance (natural decay only)
- [ ] `kb_entries_used` is recorded in `strategy_evolutions` during each attempt
- [ ] Feedback propagation fires when trade resolves from open → win/loss
- [ ] Importance cap at 100 works correctly


## Related

- MOC: [[_tasks]]
- [[2026-04-15-finmem-layered-memory]]
- [[agents]]
