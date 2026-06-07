# Phase 7 — Working Memory Central Workspace

**What**: Implement FinMem's central working memory that retrieves Top-K entries from each layer, merges them, and presents a unified context to the strategy agent. This is the "cognitive span" hyperparameter tuned to prevent the 7±2 human working memory limit from being a bottleneck.

## Step 7.1 — Add `get_working_memory` function

**File to modify**: `src/data/knowledge_base.py`

```python
def get_working_memory(
    db_path: str,
    current_regime: str = None,
    mechanism: str = None,
    k_per_layer: int = 5,  # cognitive span hyperparameter
    min_importance: int = 5,
) -> dict:
    """
    FinMem-style working memory retrieval.
    Returns Top-K findings from each layer, merged into a single context.

    Args:
        db_path:           Path to SQLite DB
        current_regime:    Current regime label (for regime-weighted retrieval)
        mechanism:        Strategy mechanism (for mechanism-weighted retrieval)
        k_per_layer:      How many entries to pull from each layer (default 5)
        min_importance:   Minimum importance to consider

    Returns:
        {
            "layers": {
                "shallow":       [finding, ...],   # max k_per_layer
                "intermediate":  [finding, ...],
                "deep":          [finding, ...],
            },
            "total": int,
            "regime_matches": int,  # how many entries match current regime
        }
    """
    conn = schema.get_connection(db_path)
    all_findings = conn.execute("""
        SELECT id, category, strategy_id, content, created_at,
               regime, mechanism, layer, importance
        FROM knowledge_base
        WHERE importance >= ?
        ORDER BY layer, importance DESC, created_at DESC
    """, (min_importance,)).fetchall()
    conn.close()

    # Group by layer
    by_layer = {"shallow": [], "intermediate": [], "deep": []}
    regime_matches = 0

    for row in all_findings:
        finding = dict(row)
        layer = finding.get("layer", "shallow")
        if layer in by_layer and len(by_layer[layer]) < k_per_layer:
            # Score for layer ordering (importance + recency + regime bonus)
            recency = compute_recency_score(finding["created_at"], layer)
            layer_score = finding["importance"] * recency
            if current_regime and finding.get("regime") == current_regime:
                layer_score *= 1.5  # 50% boost for regime match
                regime_matches += 1
            if mechanism and finding.get("mechanism") == mechanism:
                layer_score *= 1.3  # 30% boost for mechanism match
            finding["_layer_score"] = layer_score
            by_layer[layer].append(finding)

    # Sort each layer by layer_score
    for layer in by_layer:
        by_layer[layer].sort(key=lambda f: f["_layer_score"], reverse=True)
        by_layer[layer] = by_layer[layer][:k_per_layer]

    total = sum(len(v) for v in by_layer.values())
    return {"layers": by_layer, "total": total, "regime_matches": regime_matches}
```

## Step 7.2 — Update `loop1.py` to use working memory

**File to modify**: `src/loop1.py`

Replace the simple `query_relevant` call with `get_working_memory`:
```python
from src.data.knowledge_base import get_working_memory

# Step 2: load KB context via working memory
current_regime = _detect_current_regime(db_path)
memory = get_working_memory(
    db_path,
    current_regime=current_regime,
    mechanism=None,  # set after strategy agent generates hypothesis
    k_per_layer=5,   # cognitive span hyperparameter
)
kb_context = _flatten_working_memory(memory)  # list of finding dicts
```

Add `_flatten_working_memory()` helper:
```python
def _flatten_working_memory(memory: dict) -> list:
    """Flatten working memory layers into a single list for prompt injection."""
    flat = []
    for layer_name, findings in memory["layers"].items():
        for f in findings:
            flat.append({
                "layer": layer_name,
                "regime": f.get("regime"),
                "mechanism": f.get("mechanism"),
                "content": f["content"],
                "importance": f["importance"],
            })
    return flat
```

## Step 7.3 — Make cognitive span a tuneable setting

**File to modify**: `config/settings.py`

Add:
```python
COGNITIVE_SPAN_K = 5  # Top-K memories per layer. FinMem found K=5 optimal for risk-adjusted returns
```

Update `get_working_memory` call to use `COGNITIVE_SPAN_K` from settings.

## Step 7.4 — Update strategy agent prompt to show layer context

**File to modify**: `src/agents/strategy_agent.py` / `_build_system_prompt`

Format the KB context with layer annotations so Claude knows the "depth" of each insight:
```python
kb_lines = []
for f in kb_context:
    layer_tag = f"[{f['layer'][:3].upper()}]"  # [sha], [int], [dee]
    regime_tag = f"[{f.get('regime', '?')}]" if f.get('regime') else ""
    kb_lines.append(f"{layer_tag}{regime_tag} {f['content']}")

kb_str = "\n".join(kb_lines) if kb_lines else "No prior findings."
```

Update `prompts/strategy_agent_v1.txt`: Add a legend explaining the layer tags (sha=shallow daily, int=intermediate quarterly, dee=deep strategic).

## Step 7.5 — Add Phase 7 tests

**File to modify**: `tests/test_knowledge_base.py`

New tests:
1. `get_working_memory` with K=3 returns exactly 3 findings per layer (when available).
2. When current regime matches some KB entries, `regime_matches` count is > 0.
3. Entries from the wrong regime but higher importance still appear (regime is a boost, not a filter).
4. `kb_context` in strategy agent prompt contains layer tags.
5. K=10 produces more results than K=3 (different cognitive spans).

## Verification checklist
- [ ] Working memory returns Top-K per layer with correct total
- [ ] Regime-matched entries score higher within their layer
- [ ] Strategy agent prompt shows `[sha]`/`[int]`/`[dee]` layer tags
- [ ] Cognitive span is configurable via `COGNITIVE_SPAN_K` setting
- [ ] All existing tests pass (regression — no other modules changed)


## Related

- MOC: [[_tasks]]
- [[2026-04-15-finmem-layered-memory]]
- [[agents]]
