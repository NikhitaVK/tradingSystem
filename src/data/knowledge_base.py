"""
knowledge_base.py — CRUD layer over the knowledge_base SQLite table.

Agents write findings here and query it for context before hypothesising.

Categories:
    failure_diagnosis   — why a strategy failed (written by analyst in reflection mode)
    market_regime       — observations about current market conditions
    parameter_insight   — findings about specific indicator parameters
    general             — anything else worth remembering
"""
import logging
import time

from src.data.schema import get_connection, init_db

logger = logging.getLogger(__name__)


def write_finding(
    category: str,
    content: str,
    db_path: str,
    strategy_id: int = None,
) -> int:
    """
    Write a finding to the knowledge base.

    Args:
        category:    One of failure_diagnosis | market_regime | parameter_insight | general
        content:     Plain text finding. Keep it focused — one insight per entry.
        db_path:     Path to SQLite DB.
        strategy_id: Optional. Link the finding to a specific strategy.

    Returns:
        int: Row ID of the new entry.
    """
    valid_categories = {
        "failure_diagnosis", "market_regime", "parameter_insight", "general"
    }
    if category not in valid_categories:
        raise ValueError(
            f"Invalid category '{category}'. Must be one of: {valid_categories}"
        )

    if not content or not content.strip():
        raise ValueError("Content cannot be empty")

    conn = get_connection(db_path)
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
    finally:
        conn.close()

    logger.debug("KB write [%s] id=%d", category, row_id)
    return row_id


def query_relevant(
    keywords: list,
    db_path: str,
    limit: int = 10,
    category: str = None,
) -> list:
    """
    Search the knowledge base for findings that contain any of the given keywords.

    Search is case-insensitive. Results are ordered by recency (most recent first).
    Optionally filter by category.

    Args:
        keywords:   List of search terms. At least one must appear in content.
        db_path:    Path to SQLite DB.
        limit:      Maximum number of findings to return.
        category:   Optional category filter.

    Returns:
        List of dicts with keys: id, category, strategy_id, content, created_at
    """
    if not keywords:
        return []

    # Build WHERE clause: content LIKE '%kw1%' OR content LIKE '%kw2%' ...
    like_clauses = " OR ".join(["LOWER(content) LIKE ?" for _ in keywords])
    params = [f"%{kw.lower()}%" for kw in keywords]

    query = f"SELECT id, category, strategy_id, content, created_at FROM knowledge_base WHERE ({like_clauses})"

    if category:
        query += " AND category = ?"
        params.append(category)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    conn = get_connection(db_path)
    try:
        rows = conn.execute(query, params).fetchall()
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
    Used for passing full KB context to agents when no specific keyword search is needed.
    """
    query = "SELECT id, category, strategy_id, content, created_at FROM knowledge_base WHERE 1=1"
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
