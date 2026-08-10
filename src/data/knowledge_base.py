"""
knowledge_base.py — CRUD layer over the knowledge_base SQLite table.

Agents write findings here and query it for context before hypothesising.

Categories:
    failure_diagnosis   — why a strategy failed (written by analyst in reflection mode)
    market_regime       — observations about current market conditions
    parameter_insight   — findings about specific indicator parameters
    general             — anything else worth remembering

Layers (FinMem layered memory):
    shallow             — daily regime observations, short-term signals (~14d retention)
    intermediate        — parameter insights, mechanism findings (~90d retention)
    deep                — failure diagnoses, strategic learnings (~365d retention)
"""
import json
import logging
import sqlite3
import time

try:
    import anthropic
except ModuleNotFoundError:  # DB/GUI layer must import without the AI SDK installed
    anthropic = None

from config.settings import CLAUDE_HAIKU_MODEL
from src.data.schema import get_connection
from src.data.memory_layers import (
    assign_layer_and_importance,
    compute_compound_score,
    compute_structural_relevancy,
    should_purge,
    check_promotion,
    check_demotion,
)

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = {"failure_diagnosis", "market_regime", "parameter_insight", "general"}


def write_finding(
    category: str,
    content: str,
    db_path: str,
    strategy_id: int = None,
    regime: str = None,
    mechanism: str = None,
    conditions: dict = None,
    layer: str = None,
    importance: int = None,
) -> int:
    """
    Write a finding to the knowledge base.

    Layer and importance are assigned automatically from category via FinMem rules
    unless explicitly overridden.

    Args:
        category:    One of failure_diagnosis | market_regime | parameter_insight | general
        content:     Plain text finding. One insight per entry.
        db_path:     Path to SQLite DB.
        strategy_id: Optional — link finding to a specific strategy.
        regime:      Market regime when finding was written (e.g. 'trending_bull').
        mechanism:   Strategy mechanism (e.g. 'mean_reversion', 'momentum').
        conditions:  JSON-serialisable dict of market conditions at write time.
        layer:       Override auto-assigned layer ('shallow'|'intermediate'|'deep').
        importance:  Override auto-assigned importance (0–100).

    Returns:
        int: Row ID of the new entry.
    """
    if category not in _VALID_CATEGORIES:
        raise ValueError(f"Invalid category '{category}'. Must be one of: {_VALID_CATEGORIES}")
    if not content or not content.strip():
        raise ValueError("Content cannot be empty")

    # Auto-assign layer and importance from FinMem rules if not overridden
    if layer is None or importance is None:
        auto_layer, auto_importance = assign_layer_and_importance(category)
        layer = layer or auto_layer
        importance = importance if importance is not None else auto_importance

    conditions_json = json.dumps(conditions) if conditions is not None else None

    conn = get_connection(db_path)
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO knowledge_base
                    (category, strategy_id, content, created_at,
                     regime, mechanism, conditions, layer, importance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    category,
                    strategy_id,
                    content.strip(),
                    int(time.time() * 1000),
                    regime,
                    mechanism,
                    conditions_json,
                    layer,
                    importance,
                ),
            )
            row_id = cursor.lastrowid
    finally:
        conn.close()

    logger.debug("KB write [%s] layer=%s importance=%d id=%d", category, layer, importance, row_id)
    return row_id


def query_relevant(
    keywords: list,
    db_path: str,
    limit: int = 10,
    category: str = None,
    regime: str = None,
    layer: str = None,
    min_importance: int = 5,
    query_context: str = None,
) -> list:
    """
    Search the knowledge base for relevant findings.

    Two retrieval modes:
    - If query_context is provided: semantic ranking via Claude Haiku.
    - Otherwise: keyword match + importance/recency ranking.

    Args:
        keywords:      Search terms (at least one must appear in content).
        db_path:       Path to SQLite DB.
        limit:         Max findings to return.
        category:      Optional category filter.
        regime:        Optional regime filter (returns regime matches first when combined
                       with semantic retrieval, or filters strictly in keyword mode).
        layer:         Optional layer filter.
        min_importance: Exclude entries below this importance score.
        query_context: Natural language retrieval context for semantic mode.

    Returns:
        List of finding dicts.
    """
    conn = get_connection(db_path)
    try:
        params = [min_importance]
        where_parts = ["COALESCE(importance, 50) >= ?"]

        if category:
            where_parts.append("category = ?")
            params.append(category)
        if regime:
            where_parts.append("regime = ?")
            params.append(regime)
        if layer:
            where_parts.append("layer = ?")
            params.append(layer)

        if keywords:
            like_clauses = " OR ".join(["LOWER(content) LIKE ?" for _ in keywords])
            where_parts.append(f"({like_clauses})")
            params.extend(f"%{kw.lower()}%" for kw in keywords)

        query = f"""
            SELECT id, category, strategy_id, content, created_at,
                   COALESCE(regime, 'unknown') as regime,
                   COALESCE(mechanism, 'unknown') as mechanism,
                   COALESCE(layer, 'shallow') as layer,
                   COALESCE(importance, 50) as importance
            FROM knowledge_base
            WHERE {' AND '.join(where_parts)}
            ORDER BY created_at DESC
            LIMIT 200
        """
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    candidates = [dict(row) for row in rows]

    if query_context and candidates:
        return query_semantic(query_context, candidates, db_path, limit)

    # Fallback: recency-primary, importance as tiebreaker (preserves expected query order)
    candidates.sort(
        key=lambda c: (
            c.get("created_at", 0),
            COALESCE(c.get("importance"), 50),
        ),
        reverse=True,
    )
    return candidates[:limit]


def COALESCE(val, default):
    """Return val if not None, else default."""
    return val if val is not None else default


def query_semantic(
    query_text: str,
    candidates: list,
    _db_path: str,
    limit: int = 10,
) -> list:
    """
    Use Claude Haiku to rank KB entries by semantic relevance to the query context.

    Args:
        query_text: Retrieval context (e.g. "testing mean reversion in sideways market").
        candidates: KB entry dicts to rank.
        db_path:    Path to DB (unused currently, reserved for future context enrichment).
        limit:      Max results to return.

    Returns:
        Ranked list of KB entry dicts, most relevant first.
        Falls back to importance+recency ranking on any API failure.
    """
    if not candidates:
        return []

    entry_texts = []
    for i, c in enumerate(candidates):
        entry_texts.append(
            f"[{i}] regime={c.get('regime', '?')} mechanism={c.get('mechanism', '?')} "
            f"layer={c.get('layer', '?')} importance={c.get('importance', 50)}\n"
            f"    {c.get('content', '')[:300]}"
        )
    entries_block = "\n".join(entry_texts)

    system_prompt = (
        "You are a retrieval ranking system. Given a query and a list of knowledge base entries, "
        "rank them by semantic relevance to the query. Return a JSON array of entry indices "
        "in order of relevance (most relevant first).\n\n"
        "Consider: regime match, mechanism relevance, layer depth (deep > intermediate > shallow), "
        "and recency. Only include entries that are genuinely relevant."
    )
    user_prompt = (
        f"Query: {query_text}\n\n"
        f"KB Entries:\n{entries_block}\n\n"
        f'Return JSON: {{"ranked_indices": [0, 3, 1, ...]}}'
    )

    try:
        if anthropic is None:
            raise RuntimeError("anthropic SDK not installed — using fallback ranking")
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=CLAUDE_HAIKU_MODEL,
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        import re as _re
        text = response.content[0].text
        # Extract JSON from response
        match = _re.search(r"\{.*\}", text, _re.DOTALL)
        if match:
            result = json.loads(match.group())
            indices = result.get("ranked_indices", [])
            return [candidates[i] for i in indices if 0 <= i < len(candidates)][:limit]
    except Exception as e:
        logger.debug("Semantic retrieval failed (%s), using importance+recency ranking", e)

    # Fallback: importance + recency ranking
    ranked = sorted(
        candidates,
        key=lambda c: (COALESCE(c.get("importance"), 50), c.get("created_at", 0)),
        reverse=True,
    )
    return ranked[:limit]


def query_regime_failures(
    regime: str,
    mechanism: str,
    db_path: str,
) -> list:
    """
    Return KB entries where a strategy with the given mechanism failed in the
    given regime. Used for pre-backtest warning in Phase 3.
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, content, strategy_id, created_at,
                   COALESCE(importance, 50) as importance
            FROM knowledge_base
            WHERE COALESCE(regime, '') = ?
              AND COALESCE(mechanism, '') = ?
              AND category = 'failure_diagnosis'
            ORDER BY COALESCE(importance, 50) DESC, created_at DESC
            LIMIT 5
            """,
            (regime, mechanism),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def get_all_findings(
    db_path: str,
    category: str = None,
    strategy_id: int = None,
    limit: int = 50,
) -> list:
    """
    Retrieve findings ordered by recency. Optionally filter by category or strategy_id.
    """
    query = """
        SELECT id, category, strategy_id, content, created_at,
               COALESCE(regime, 'unknown') as regime,
               COALESCE(mechanism, 'unknown') as mechanism,
               COALESCE(layer, 'shallow') as layer,
               COALESCE(importance, 50) as importance
        FROM knowledge_base WHERE 1=1
    """
    params = []

    if category:
        query += " AND category = ?"
        params.append(category)
    if strategy_id is not None:
        query += " AND strategy_id = ?"
        params.append(strategy_id)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def get_working_memory(
    db_path: str,
    current_regime: str = None,
    mechanism: str = None,
    k_per_layer: int = 5,
    min_importance: int = 5,
) -> dict:
    """
    FinMem-style working memory retrieval. Returns Top-K findings from each layer.

    Entries are scored by importance × recency, with a 50% boost for regime matches
    and a 30% boost for mechanism matches.

    Args:
        db_path:        Path to SQLite DB.
        current_regime: Current regime label for weighted retrieval.
        mechanism:      Strategy mechanism for weighted retrieval.
        k_per_layer:    How many entries to pull from each layer.
        min_importance: Minimum importance to consider.

    Returns:
        {"layers": {"shallow": [...], "intermediate": [...], "deep": [...]},
         "total": int, "regime_matches": int}
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT id, category, strategy_id, content, created_at,
                   COALESCE(regime, 'unknown') as regime,
                   COALESCE(mechanism, 'unknown') as mechanism,
                   COALESCE(layer, 'shallow') as layer,
                   COALESCE(importance, 50) as importance
            FROM knowledge_base
            WHERE COALESCE(importance, 50) >= ?
            ORDER BY COALESCE(importance, 50) DESC, created_at DESC
            """,
            (min_importance,),
        ).fetchall()
    finally:
        conn.close()

    by_layer: dict = {"shallow": [], "intermediate": [], "deep": []}
    regime_matches = 0
    scored_entries = []

    for row in rows:
        finding = dict(row)
        layer = finding.get("layer", "shallow")
        if layer not in by_layer:
            layer = "shallow"

        # FinMem compound score: S_recency + S_relevancy + S_importance, each in [0,1].
        # Summed, not multiplied — a product lets a decayed recency annihilate a
        # high-importance memory, which is what made a 119-day finding score 0.0002
        # and put the whole KB one purge away from deletion.
        relevancy = compute_structural_relevancy(finding, current_regime, mechanism)
        layer_score = compute_compound_score(
            finding["created_at"], layer, COALESCE(finding.get("importance"), 50)
        ) + relevancy

        if current_regime and finding.get("regime") == current_regime:
            regime_matches += 1

        finding["_layer_score"] = layer_score
        finding["_layer_key"] = layer
        scored_entries.append(finding)

    # Sort all entries by score, then pick top-K per layer
    scored_entries.sort(key=lambda f: f["_layer_score"], reverse=True)
    for finding in scored_entries:
        layer = finding["_layer_key"]
        if len(by_layer[layer]) < k_per_layer:
            by_layer[layer].append(finding)

    total = sum(len(v) for v in by_layer.values())
    return {"layers": by_layer, "total": total, "regime_matches": regime_matches}


def detect_current_regime(db_path: str, symbol: str = "BTC/USDT", timeframe: str = "1h") -> str:
    """
    Detect the current market regime from the most recent candles in the DB.

    Reads from live_candles first (real-time), falls back to ohlcv_history.
    Returns a regime label: 'trending_bull' | 'trending_bear' | 'sideways' | 'high_vol'.
    """
    import pandas as pd
    from src.backtest.hmm_regime import detect_regimes

    conn = get_connection(db_path)
    try:
        # Try live_candles first
        rows = conn.execute(
            """
            SELECT timestamp, open, high, low, close, volume
            FROM live_candles
            WHERE symbol = ? AND timeframe = ?
            ORDER BY timestamp DESC LIMIT 600
            """,
            (symbol, timeframe),
        ).fetchall()

        if len(rows) < 50:
            # Fall back to ohlcv_history
            rows = conn.execute(
                """
                SELECT timestamp, open, high, low, close, volume
                FROM ohlcv_history
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp DESC LIMIT 600
                """,
                (symbol, timeframe),
            ).fetchall()
    finally:
        conn.close()

    if len(rows) < 20:
        return "sideways"

    df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.sort_values("timestamp").reset_index(drop=True)

    try:
        regimes = detect_regimes(df)
        valid = regimes.dropna()
        if valid.empty:
            return "sideways"
        return str(valid.mode().iloc[0])
    except Exception:
        return "sideways"


def reclassify_layers(db_path: str) -> int:
    """
    Repair layer labels that came from the migration default rather than a real
    classification, and return the number of rows changed.

    The ``layer`` column was added by ALTER TABLE with DEFAULT 'shallow'
    (schema.py), so every finding written before FinMem layering shipped carries
    'shallow' regardless of its category. That is not cosmetic: layer selects the
    decay constants, and shallow's Q=14 against deep's Q=365 makes a 119-day
    failure diagnosis score recency 0.0002 instead of 0.72 — far below the 0.05
    purge floor. Left unrepaired, the next ``purge_kb()`` deletes most of the KB.

    Idempotent. Only rewrites rows whose stored layer disagrees with the layer
    their category implies; explicit non-default assignments are preserved
    because they already agree with their category.
    """
    conn = get_connection(db_path)
    changed = 0
    try:
        rows = conn.execute(
            "SELECT id, category, COALESCE(layer, 'shallow') AS layer FROM knowledge_base"
        ).fetchall()
        with conn:
            for row in rows:
                correct_layer, _ = assign_layer_and_importance(row["category"])
                if row["layer"] != correct_layer:
                    conn.execute(
                        "UPDATE knowledge_base SET layer = ? WHERE id = ?",
                        (correct_layer, row["id"]),
                    )
                    changed += 1
    finally:
        conn.close()
    if changed:
        logger.info("KB reclassify: corrected layer on %d entries", changed)
    return changed


def purge_kb(db_path: str) -> int:
    """
    Delete KB entries that have decayed below importance + recency thresholds.
    Returns count of purged entries.
    """
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, created_at, COALESCE(layer, 'shallow') as layer, "
            "COALESCE(importance, 50) as importance FROM knowledge_base"
        ).fetchall()
        purged = 0
        for row in rows:
            if should_purge(row["created_at"], row["layer"], row["importance"]):
                conn.execute("DELETE FROM knowledge_base WHERE id = ?", (row["id"],))
                purged += 1
        conn.commit()
    finally:
        conn.close()
    if purged:
        logger.info("KB purge: removed %d stale entries", purged)
    return purged


def promote_memories(db_path: str) -> int:
    """
    Check all KB entries for layer promotion and demotion criteria.
    Runs 2 passes (FinMem pattern — cascading jumps may need two passes).
    Returns count of changed entries.
    """
    changed_total = 0
    for _ in range(2):
        conn = get_connection(db_path)
        try:
            rows = conn.execute(
                "SELECT id, created_at, COALESCE(layer, 'shallow') as layer, "
                "COALESCE(importance, 50) as importance FROM knowledge_base"
            ).fetchall()
            changed = 0
            for row in rows:
                new_layer = check_promotion(row["created_at"], row["layer"], row["importance"])
                if new_layer:
                    conn.execute(
                        "UPDATE knowledge_base SET layer = ?, importance = 80 WHERE id = ?",
                        (new_layer, row["id"]),
                    )
                    changed += 1
                    continue
                new_layer = check_demotion(row["created_at"], row["layer"], row["importance"])
                if new_layer:
                    conn.execute(
                        "UPDATE knowledge_base SET layer = ?, importance = 60 WHERE id = ?",
                        (new_layer, row["id"]),
                    )
                    changed += 1
            conn.commit()
            changed_total += changed
        finally:
            conn.close()
    return changed_total


# ── Repository class (used by the GUI layer) ──────────────────────────────────
# The tkinter GUI (src/gui/app.py) must contain no SQL — all database access goes
# through this class. The module-level functions above remain for the agents.

class KnowledgeBaseRepository:
    """Repository for all knowledge_base table operations used by the GUI.

    All SQL for the GUI lives inside this class. The interface layer only ever
    calls these methods — it never writes SQL itself. This keeps the program
    well-structured and is the key requirement for Excellence in AS91906.
    """

    def __init__(self, db_path: str):
        """Initialise the repository with the path to the SQLite database file."""
        self.db_path = db_path

    # ── CREATE ────────────────────────────────────────────────────────────────

    def write_finding(self, category: str, content: str, strategy_id: int = None) -> int:
        """Insert a new finding. Returns the new row id.

        Raises ValueError if the category is invalid, the content is blank, or a
        database constraint is violated (so the GUI can show a friendly message
        instead of crashing).
        """
        if category not in _VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category '{category}'. Must be one of: {sorted(_VALID_CATEGORIES)}"
            )
        if not content or not content.strip():
            raise ValueError("Content cannot be empty.")

        conn = get_connection(self.db_path)
        try:
            with conn:
                cursor = conn.execute(
                    """
                    INSERT INTO knowledge_base (category, strategy_id, content, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (category, strategy_id, content.strip(), int(time.time() * 1000)),
                )
                row_id = cursor.lastrowid
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Could not save finding — constraint violated: {exc}") from exc
        finally:
            conn.close()

        logger.debug("KB write [%s] id=%d", category, row_id)
        return row_id

    # ── READ ──────────────────────────────────────────────────────────────────

    def get_all_findings(self, category: str = None, strategy_id: int = None, limit: int = 50) -> list:
        """Return findings ordered by recency (newest first), optionally filtered."""
        query = (
            "SELECT id, category, strategy_id, content, created_at "
            "FROM knowledge_base WHERE 1=1"
        )
        params = []
        if category:
            query += " AND category = ?"
            params.append(category)
        if strategy_id is not None:
            query += " AND strategy_id = ?"
            params.append(strategy_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    # ── UPDATE ────────────────────────────────────────────────────────────────

    def update_finding(self, finding_id: int, new_content: str) -> bool:
        """Update one finding's content by id. Returns False if id not found.

        The WHERE clause on id ensures only one row changes — without it, every
        row in the table would be updated.
        """
        if not new_content or not new_content.strip():
            raise ValueError("Content cannot be empty.")

        conn = get_connection(self.db_path)
        try:
            with conn:
                cursor = conn.execute(
                    "UPDATE knowledge_base SET content = ? WHERE id = ?",
                    (new_content.strip(), finding_id),
                )
                updated = cursor.rowcount > 0
        finally:
            conn.close()
        if not updated:
            logger.warning("update_finding: no row with id=%d", finding_id)
        return updated

    # ── DELETE ────────────────────────────────────────────────────────────────

    def delete_finding(self, finding_id: int) -> bool:
        """Delete one finding by id. Returns False if id not found."""
        conn = get_connection(self.db_path)
        try:
            with conn:
                cursor = conn.execute(
                    "DELETE FROM knowledge_base WHERE id = ?",
                    (finding_id,),
                )
                deleted = cursor.rowcount > 0
        finally:
            conn.close()
        if not deleted:
            logger.warning("delete_finding: no row with id=%d", finding_id)
        return deleted

    # ── AGGREGATE ─────────────────────────────────────────────────────────────

    def count_by_category(self) -> list:
        """Return [{category, total}, ...] using GROUP BY + COUNT(*).

        Handles the empty-table boundary case by simply returning an empty list.
        """
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT category, COUNT(*) AS total
                FROM knowledge_base
                GROUP BY category
                ORDER BY total DESC
                """
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]
