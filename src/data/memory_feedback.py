"""
memory_feedback.py — Feedback-driven importance updates from trading outcomes.

After a trade resolves, KB entries that contributed to the decision that produced
the trade have their importance boosted (win) or left unchanged (loss — natural
decay handles that over time).

This is the RL signal that makes the memory system adaptive, not just a passive
store. FinMem's access_counter mechanism achieves the same effect.

The feedback loop:
  Trade placed → KB entries retrieved → decision made → trade resolves
  → win:  boost importance of contributing KB entries (+5 per win)
  → loss: no boost (entries decay naturally over time)
  → importance grows for memories that consistently contribute to good decisions
  → high-importance memories promote to deeper layers
  → low-importance memories demote or get purged
"""
import logging

from src.data.schema import get_connection

logger = logging.getLogger(__name__)

# Importance boost per successful trade (matches FinMem's +5 per access)
FEEDBACK_IMPORTANCE_BOOST = 5


def update_importance_from_feedback(
    kb_entry_ids: list,
    outcome: str,
    db_path: str,
) -> int:
    """
    Update importance of KB entries based on resolved trade outcome.

    Args:
        kb_entry_ids: List of KB row IDs that contributed to the decision.
        outcome:      'win' | 'loss' | 'open'
        db_path:      Path to SQLite DB.

    Returns:
        Count of rows updated. Zero for non-win outcomes.
    """
    if not kb_entry_ids or outcome != "win":
        return 0

    conn = get_connection(db_path)
    try:
        placeholders = ",".join(["?"] * len(kb_entry_ids))
        updated = conn.execute(
            f"""
            UPDATE knowledge_base
            SET importance = MIN(importance + ?, 100)
            WHERE id IN ({placeholders})
            """,
            [FEEDBACK_IMPORTANCE_BOOST] + list(kb_entry_ids),
        ).rowcount
        conn.commit()
        logger.debug(
            "Feedback boost: %d KB entries updated for %s outcome",
            updated, outcome,
        )
        return updated
    finally:
        conn.close()
