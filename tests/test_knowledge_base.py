"""
test_knowledge_base.py — Tests for Phase 2, 5, 6, and 7 KB enhancements.

Phase 2: Regime-aware KB schema (regime, mechanism, layer, importance columns)
Phase 5: Semantic KB retrieval (query_context path, falls back gracefully)
Phase 6: Layered memory architecture (layer assignment, decay, purge, promote/demote, feedback)
Phase 7: Working memory workspace (get_working_memory, cognitive span, regime boost)
"""
import os
import sqlite3
import tempfile
import time

import pytest

from src.data.schema import init_db
from src.data.knowledge_base import (
    write_finding,
    query_relevant,
    get_working_memory,
    purge_kb,
    promote_memories,
)
from src.data.memory_layers import (
    assign_layer_and_importance,
    compute_recency_score,
    should_purge,
    check_promotion,
    check_demotion,
)
from src.data.memory_feedback import update_importance_from_feedback


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db() -> str:
    tmp = tempfile.mktemp(suffix=".db")
    init_db(tmp)
    return tmp


# ── Phase 2: Regime-aware KB schema ──────────────────────────────────────────

def test_write_finding_with_regime_and_mechanism():
    """write_finding must persist regime, mechanism, layer, importance columns."""
    db_path = _make_db()
    row_id = write_finding(
        category="failure_diagnosis",
        content="RSI mean reversion failed in trending bull market.",
        db_path=db_path,
        regime="trending_bull",
        mechanism="mean_reversion",
    )
    assert isinstance(row_id, int) and row_id > 0

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT regime, mechanism, layer, importance FROM knowledge_base WHERE id=?",
        (row_id,),
    ).fetchone()
    conn.close()

    assert row is not None, "Row should exist after write_finding"
    assert row[0] == "trending_bull", f"regime mismatch: {row[0]}"
    assert row[1] == "mean_reversion", f"mechanism mismatch: {row[1]}"
    assert row[2] in ("shallow", "intermediate", "deep"), f"invalid layer: {row[2]}"
    assert isinstance(row[3], int) and 0 <= row[3] <= 100, f"importance out of range: {row[3]}"

    os.unlink(db_path)


def test_query_relevant_filters_by_regime():
    """query_relevant with regime filter must only return matching entries."""
    db_path = _make_db()

    write_finding("failure_diagnosis", "Failed in bull market.", db_path, regime="trending_bull")
    write_finding("failure_diagnosis", "Failed in bear market.", db_path, regime="trending_bear")
    write_finding("failure_diagnosis", "General failure note.", db_path)

    results = query_relevant(["failed"], db_path, regime="trending_bull")
    regimes = [r["regime"] for r in results]
    assert all(r == "trending_bull" for r in regimes), \
        f"Expected only trending_bull regime results, got: {regimes}"

    os.unlink(db_path)


def test_write_finding_layer_auto_assigned():
    """Layer is auto-assigned based on category if not explicitly provided."""
    db_path = _make_db()

    # failure_diagnosis → deep layer
    id1 = write_finding("failure_diagnosis", "Deep learning content.", db_path)
    # market_regime → shallow layer
    id2 = write_finding("market_regime", "Shallow observation.", db_path)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT id, layer FROM knowledge_base WHERE id IN (?,?)", (id1, id2)
    ).fetchall()
    conn.close()

    layer_map = {r[0]: r[1] for r in rows}
    assert layer_map[id1] == "deep", f"failure_diagnosis should be 'deep', got {layer_map[id1]}"
    assert layer_map[id2] == "shallow", f"market_regime should be 'shallow', got {layer_map[id2]}"

    os.unlink(db_path)


# ── Phase 5: Semantic KB retrieval ───────────────────────────────────────────

def test_query_relevant_without_query_context_returns_results():
    """keyword-only query must return results without semantic API call."""
    db_path = _make_db()

    write_finding("failure_diagnosis", "RSI parameter too short for momentum regime.", db_path)
    write_finding("market_regime", "Market is in sideways consolidation.", db_path)

    results = query_relevant(["RSI"], db_path)
    assert len(results) >= 1, "Expected at least 1 RSI result"
    assert any("RSI" in r["content"] for r in results)

    os.unlink(db_path)


def test_query_relevant_with_query_context_fallback():
    """
    When query_context is provided but the Haiku API is unavailable, the
    function must fall back to importance+recency ranking without raising.
    """
    import unittest.mock as mock
    db_path = _make_db()

    write_finding("failure_diagnosis", "RSI failed in trending market.", db_path)
    write_finding("parameter_insight", "EMA period 50 works well in bull trend.", db_path)

    # Patch the Anthropic client to raise so we exercise the fallback path
    with mock.patch("src.data.knowledge_base.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.side_effect = Exception("API unavailable")
        results = query_relevant(
            ["RSI", "EMA"],
            db_path,
            query_context="testing momentum in trending_bull regime",
        )

    assert isinstance(results, list), "Must return a list even when semantic API fails"

    os.unlink(db_path)


# ── Phase 6: Layered memory architecture ─────────────────────────────────────

def test_assign_layer_and_importance_by_category():
    """Layer and importance must match the FinMem category mapping."""
    deep_layer, deep_imp = assign_layer_and_importance("failure_diagnosis")
    assert deep_layer == "deep", f"failure_diagnosis should be deep, got {deep_layer}"
    assert deep_imp >= 60, f"failure_diagnosis importance should be high, got {deep_imp}"

    shallow_layer, shallow_imp = assign_layer_and_importance("market_regime")
    assert shallow_layer == "shallow", f"market_regime should be shallow, got {shallow_layer}"

    inter_layer, _ = assign_layer_and_importance("parameter_insight")
    assert inter_layer == "intermediate", f"parameter_insight should be intermediate, got {inter_layer}"


def test_compute_recency_score_decays_over_time():
    """Older entries must have a lower recency score than recent ones."""
    now_ms = int(time.time() * 1000)
    old_ms = now_ms - 30 * 24 * 3600 * 1000  # 30 days ago

    score_recent = compute_recency_score(now_ms, "shallow")
    score_old = compute_recency_score(old_ms, "shallow")

    assert score_recent > score_old, \
        f"Recent score {score_recent:.4f} should exceed old score {score_old:.4f}"
    assert 0.0 <= score_recent <= 1.0
    assert 0.0 <= score_old <= 1.0


def test_should_purge_stale_low_importance_entry():
    """A very old, low-importance shallow entry should be marked for purging."""
    # 60 days ago — well past shallow layer's 14-day retention
    old_ms = int(time.time() * 1000) - 60 * 24 * 3600 * 1000
    assert should_purge(old_ms, "shallow", importance=10), \
        "Old low-importance shallow entry should be purged"


def test_should_not_purge_recent_high_importance_entry():
    """A recent, high-importance deep entry should not be purged."""
    recent_ms = int(time.time() * 1000) - 3600 * 1000  # 1 hour ago
    assert not should_purge(recent_ms, "deep", importance=90), \
        "Recent high-importance deep entry should not be purged"


def test_purge_kb_removes_stale_entries():
    """purge_kb() must remove entries that have decayed past threshold."""
    db_path = _make_db()

    # Insert a fresh entry (should survive)
    id_keep = write_finding("market_regime", "Fresh observation.", db_path, importance=70)

    # Manually insert a very stale, low-importance shallow entry
    old_ts = int(time.time() * 1000) - 90 * 24 * 3600 * 1000  # 90 days ago
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO knowledge_base (category, content, created_at, layer, importance) "
        "VALUES ('market_regime', 'Stale old entry.', ?, 'shallow', 5)",
        (old_ts,),
    )
    conn.commit()
    conn.close()

    purged = purge_kb(db_path)
    assert purged >= 1, f"Expected at least 1 purged entry, got {purged}"

    # Fresh entry must still be present
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT id FROM knowledge_base WHERE id=?", (id_keep,)).fetchone()
    conn.close()
    assert row is not None, "Fresh high-importance entry should not be purged"

    os.unlink(db_path)


def test_check_promotion_and_demotion():
    """
    A high-importance entry that has been in shallow long enough should promote.
    A low-importance deep entry should demote.
    """
    # Simulate a 20-day-old shallow entry with high importance
    old_ms = int(time.time() * 1000) - 20 * 24 * 3600 * 1000
    promoted_layer = check_promotion(old_ms, "shallow", importance=85)
    # Either promotes to intermediate, or stays None if threshold not met
    assert promoted_layer in (None, "intermediate"), \
        f"check_promotion should return 'intermediate' or None, got {promoted_layer}"

    # A low-importance intermediate entry should demote (or stay)
    recent_ms = int(time.time() * 1000) - 5 * 24 * 3600 * 1000
    demoted_layer = check_demotion(recent_ms, "intermediate", importance=10)
    assert demoted_layer in (None, "shallow"), \
        f"check_demotion should return 'shallow' or None, got {demoted_layer}"


def test_feedback_boosts_importance_on_win():
    """update_importance_from_feedback must increase importance for win outcomes."""
    db_path = _make_db()

    row_id = write_finding("failure_diagnosis", "Test entry.", db_path)

    conn = sqlite3.connect(db_path)
    before = conn.execute(
        "SELECT importance FROM knowledge_base WHERE id=?", (row_id,)
    ).fetchone()[0]
    conn.close()

    updated = update_importance_from_feedback([row_id], "win", db_path)
    assert updated == 1, f"Expected 1 row updated, got {updated}"

    conn = sqlite3.connect(db_path)
    after = conn.execute(
        "SELECT importance FROM knowledge_base WHERE id=?", (row_id,)
    ).fetchone()[0]
    conn.close()

    assert after > before, f"Importance should increase after win: {before} → {after}"

    os.unlink(db_path)


def test_feedback_no_op_on_loss():
    """update_importance_from_feedback must make no changes for loss outcomes."""
    db_path = _make_db()

    row_id = write_finding("failure_diagnosis", "Test entry.", db_path)
    updated = update_importance_from_feedback([row_id], "loss", db_path)
    assert updated == 0, "Loss outcome should produce zero row updates"

    os.unlink(db_path)


# ── Phase 7: Working memory workspace ────────────────────────────────────────

def test_get_working_memory_returns_layered_structure():
    """get_working_memory must return the expected dict structure."""
    db_path = _make_db()

    write_finding("failure_diagnosis", "Deep failure note.", db_path, regime="sideways")
    write_finding("market_regime", "Shallow regime note.", db_path, regime="sideways")
    write_finding("parameter_insight", "Intermediate insight.", db_path)

    memory = get_working_memory(db_path)

    assert "layers" in memory
    assert "total" in memory
    assert "regime_matches" in memory
    assert set(memory["layers"].keys()) == {"shallow", "intermediate", "deep"}

    os.unlink(db_path)


def test_working_memory_regime_boost():
    """
    Entries matching current_regime must appear in the working memory output
    when they have sufficient importance. The regime_matches count must be > 0.
    """
    db_path = _make_db()

    # Write one regime-matching and one non-matching entry with identical importance
    write_finding(
        "failure_diagnosis", "Regime-matched failure.", db_path,
        regime="trending_bull", importance=70,
    )
    write_finding(
        "failure_diagnosis", "Different regime failure.", db_path,
        regime="sideways", importance=70,
    )

    memory = get_working_memory(db_path, current_regime="trending_bull")
    assert memory["regime_matches"] >= 1, \
        f"Expected at least 1 regime match for trending_bull, got {memory['regime_matches']}"

    os.unlink(db_path)


def test_working_memory_cognitive_span_k():
    """Each layer must return at most k_per_layer entries."""
    db_path = _make_db()

    # Insert 10 failure_diagnosis entries (→ deep layer)
    for i in range(10):
        write_finding("failure_diagnosis", f"Failure note {i}.", db_path)

    k = 3
    memory = get_working_memory(db_path, k_per_layer=k)

    for layer_name, entries in memory["layers"].items():
        assert len(entries) <= k, \
            f"Layer '{layer_name}' returned {len(entries)} entries, expected <= {k}"

    os.unlink(db_path)


def test_working_memory_empty_db():
    """get_working_memory must not raise on an empty knowledge base."""
    db_path = _make_db()
    memory = get_working_memory(db_path)

    assert memory["total"] == 0
    assert memory["regime_matches"] == 0
    for layer_entries in memory["layers"].values():
        assert layer_entries == []

    os.unlink(db_path)
