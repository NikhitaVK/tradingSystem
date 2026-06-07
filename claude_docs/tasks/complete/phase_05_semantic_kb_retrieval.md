# Phase 5 — Claude-Mediated Semantic KB Retrieval

**What**: Replace keyword `LIKE` matching with semantic retrieval via a lightweight Claude haiku call. At query time, assemble the candidate KB entries and the retrieval context, pass to haiku, get back the semantically ranked subset. Aligned with the human learning model — retrieval is contextual and reasoning-based, not geometric.

**Why not Option A (Claude embeddings API)**:
- Embeddings are static vectors computed at write time — they cannot adapt their meaning to the current retrieval context
- A human's memory retrieval is dynamic: they reason about *why* something is relevant, not just compute geometric distance
- Two failures with different language but the same underlying mechanism would score poorly on cosine similarity but highly on semantic reasoning
- Option A also has embedding drift risk: stored vectors use a different model version than query-time vectors
- The system already uses Claude API everywhere — adding haiku for semantic retrieval is architecturally consistent, not an outlier

**This choice validated by**: The system's goal is "trading like a human without emotional disadvantages." A human doesn't compute vector similarity — they reason about context and applicability. Option B models this.

## Step 5.1 — Add `query_semantic` function to knowledge_base

**File to modify**: `src/data/knowledge_base.py`

Add a new function that does semantic retrieval via haiku:
```python
def query_semantic(
    query_text: str,
    candidates: list[dict],  # KB entries as dicts
    db_path: str,
    limit: int = 10,
) -> list[dict]:
    """
    Use a lightweight Claude haiku call to rank KB entries by semantic
    relevance to the query context.

    Args:
        query_text:    The retrieval context (e.g. "testing mean reversion in sideways low-vol regime")
        candidates:     KB entries to be ranked (from get_all_findings or raw SQL fetch)
        db_path:       Path to DB (for getting additional context if needed)
        limit:         Max results to return

    Returns:
        Ranked list of KB entry dicts, most relevant first.
    """
    from config.settings import CLAUDE_MODEL

    if not candidates:
        return []

    # Format entries for Claude
    entry_texts = []
    for i, c in enumerate(candidates):
        entry_texts.append(
            f"[{i}] regime={c.get('regime','?')} mechanism={c.get('mechanism','?')} "
            f"layer={c.get('layer','?')} importance={c.get('importance',50)}\n"
            f"    {c.get('content','')}"
        )

    entries_block = "\n".join(entry_texts)

    system_prompt = (
        "You are a retrieval ranking system. Given a query and a list of knowledge base entries, "
        "rank them by semantic relevance to the query. Return a JSON array of entry indices "
        "in order of relevance (most relevant first).\n\n"
        "Consider: regime match, mechanism relevance, layer depth (deep > intermediate > shallow), "
        "and recency. Be precise — don't include entries that are off-topic even if high importance."
    )

    user_prompt = (
        f"Query: {query_text}\n\n"
        f"KB Entries:\n{entries_block}\n\n"
        f"Return JSON: {{\"ranked_indices\": [0, 3, 1, ...]}}"
    )

    try:
        import anthropic
        client = anthropic.Anthropic()
        response = client.messages.create(
            model="claude-3-5-haiku-4-20250514",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        import json as _json
        result = _json.loads(response.content[0].text)
        indices = result.get("ranked_indices", [])
        return [candidates[i] for i in indices if i < len(candidates)][:limit]
    except Exception:
        # On any failure, fall back to importance + recency ranking
        ranked = sorted(
            candidates,
            key=lambda c: (c.get("importance", 50), c.get("created_at", 0)),
            reverse=True
        )
        return ranked[:limit]
```

## Step 5.2 — Update `query_relevant` to use semantic retrieval

**File to modify**: `src/data/knowledge_base.py`

Rename current function logic to `query_keyword` (internal), then make `query_relevant` the public interface that calls semantic for ranked retrieval when the query has sufficient context:

```python
def query_relevant(
    keywords: list,
    db_path: str,
    limit: int = 10,
    category: str = None,
    regime: str = None,
    layer: str = None,
    min_importance: int = 5,
    query_context: str = None,  # NEW: natural language context for semantic retrieval
) -> list:
    """
    KB retrieval with two modes:
    - If query_context is provided: use Claude-mediated semantic ranking
    - Otherwise: use keyword match + importance/recency ranking

    This is the single public entry point. Internal keyword search is
    handled by _query_keyword().
    """
    conn = schema.get_connection(db_path)

    # Fetch all candidates (broad fetch, let semantic layer rank)
    params = []
    where_parts = [f"importance >= {min_importance}"]
    if category: where_parts.append("category = ?"), params.append(category)
    if regime: where_parts.append("regime = ?"), params.append(regime)
    if layer: where_parts.append("layer = ?"), params.append(layer)

    query = f"""
        SELECT id, category, strategy_id, content, created_at,
               regime, mechanism, layer, importance
        FROM knowledge_base
        WHERE {' AND '.join(where_parts)}
        ORDER BY created_at DESC
        LIMIT 200
    """
    rows = conn.execute(query, params).fetchall()
    conn.close()

    candidates = [dict(row) for row in rows]

    if query_context:
        return query_semantic(query_context, candidates, db_path, limit)

    # Fallback: importance + recency ranking
    candidates.sort(key=lambda c: (c.get("importance", 50), c.get("created_at", 0)), reverse=True)
    return candidates[:limit]
```

## Step 5.3 — Update `handle_query_knowledge_base` in tools.py

**File to modify**: `src/agents/tools.py` / `handle_query_knowledge_base`

The tool schema already accepts `keywords`. Add `query_context` as an optional field so the strategy agent can pass natural language retrieval context:

```python
def handle_query_knowledge_base(args: dict, db_path: str) -> str:
    keywords = args.get("keywords", [])
    limit = args.get("limit", 10)
    query_context = args.get("query_context")  # NEW
    findings = query_relevant(keywords, db_path, limit=limit, query_context=query_context)
    if not findings:
        return json.dumps({"findings": [], "message": "No relevant findings in knowledge base."})
    return json.dumps({"findings": findings})
```

Update the tool schema in `STRATEGY_AGENT_TOOLS`:
```python
{
    "name": "query_knowledge_base",
    ...
    "input_schema": {
        "type": "object",
        "properties": {
            "keywords": {...},
            "limit": {...},
            "query_context": {   # NEW
                "type": "string",
                "description": "Natural language description of the current reasoning context (e.g. 'testing mean reversion strategy in sideways low-vol regime'). When provided, semantic retrieval is used for better ranking.",
            },
        },
    },
}
```

## Step 5.4 — Update strategy agent to pass semantic context

**File to modify**: `src/agents/strategy_agent.py` / `_build_system_prompt`

The strategy agent should pass query_context when calling `query_knowledge_base`. This means the agent's prompt should instruct it to include context about the current hypothesis when querying the KB.

**Update `prompts/strategy_agent_v1.txt`**: Add instruction that `query_knowledge_base` should be called with both `keywords` and `query_context` describing the current hypothesis and regime.

Example: When calling `query_knowledge_base`, include `"query_context": "testing RSI mean reversion in current sideways low-vol regime, mechanism is mean reversion"` alongside the keywords.

## Step 5.5 — Add Phase 5 isolation tests

**File to create**: `tests/test_knowledge_base.py`

```python
def test_query_semantic_returns_ranked_results():
    """Semantic retrieval ranks entries by context relevance, not just recency."""
    # Write two entries: one about mean reversion failure, one about momentum failure
    # Query with "mean reversion strategy failure in sideways market"
    # Assert mean reversion entry ranks first
    pass

def test_query_semantic_falls_back_on_api_failure():
    """If the haiku call fails, falls back to importance+recency ranking."""
    pass

def test_query_relevant_with_query_context_calls_semantic():
    """When query_context is provided, semantic path is taken."""
    pass

def test_query_relevant_without_context_uses_importance_ranking():
    """When no query_context, falls back to keyword + importance sort."""
    pass

def test_handle_query_knowledge_base_passes_query_context():
    """Tool handler passes query_context through to query_relevant."""
    pass
```

## Verification checklist
- [ ] `query_semantic` correctly ranks regime-matching entries higher
- [ ] `query_relevant` calls `query_semantic` when `query_context` is provided
- [ ] Fallback to importance/recency ranking works when haiku is unavailable
- [ ] Strategy agent prompt instructs to use `query_context` in KB queries
- [ ] All existing KB tests pass (regression)


## Related

- MOC: [[_tasks]]
- [[2026-04-15-finmem-layered-memory]]
- [[data_pipeline]]
