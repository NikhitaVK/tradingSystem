"""Tests for memory_feedback — feedback-driven KB importance updates."""
import os
import tempfile

import pytest

from src.data.schema import init_db, get_connection
from src.data.memory_feedback import (
    update_importance_from_feedback,
    FEEDBACK_IMPORTANCE_BOOST,
)


@pytest.fixture
def db_with_entries():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    init_db(path)
    conn = get_connection(path)
    ids = []
    for content in ["entry_a", "entry_b", "entry_c"]:
        cur = conn.execute(
            "INSERT INTO knowledge_base (category, content, importance, created_at) "
            "VALUES ('failure_diagnosis', ?, 10, 0)",
            (content,),
        )
        ids.append(cur.lastrowid)
    conn.commit()
    conn.close()
    yield path, ids
    os.unlink(path)


def test_win_boosts_importance(db_with_entries):
    path, ids = db_with_entries
    n = update_importance_from_feedback(ids, "win", path)
    assert n == 3
    conn = get_connection(path)
    rows = conn.execute(
        f"SELECT importance FROM knowledge_base WHERE id IN ({','.join(['?']*len(ids))})",
        ids,
    ).fetchall()
    conn.close()
    for r in rows:
        assert r["importance"] == 10 + FEEDBACK_IMPORTANCE_BOOST


def test_loss_no_boost(db_with_entries):
    path, ids = db_with_entries
    n = update_importance_from_feedback(ids, "loss", path)
    assert n == 0


def test_empty_ids_no_op(db_with_entries):
    path, _ = db_with_entries
    assert update_importance_from_feedback([], "win", path) == 0


def test_importance_capped_at_100(db_with_entries):
    path, ids = db_with_entries
    conn = get_connection(path)
    conn.execute(
        f"UPDATE knowledge_base SET importance = 98 WHERE id IN ({','.join(['?']*len(ids))})",
        ids,
    )
    conn.commit()
    conn.close()
    update_importance_from_feedback(ids, "win", path)
    conn = get_connection(path)
    rows = conn.execute(
        f"SELECT importance FROM knowledge_base WHERE id IN ({','.join(['?']*len(ids))})",
        ids,
    ).fetchall()
    conn.close()
    for r in rows:
        assert r["importance"] == 100
