"""
test_data_pipeline.py — Isolation tests for Module 1 (Data Pipeline).

All 6 tests must pass before Module 2 work begins.
Run: pytest tests/test_data_pipeline.py -v

Test 4 (CCXT sandbox connection) requires real Binance Testnet API keys in .env.
Set SKIP_LIVE_TESTS=1 in your environment to skip it during offline development.
"""
import os
import tempfile
import textwrap
import time

import pandas as pd
import pytest

from src.data.schema import init_db, get_connection
from src.data.ingestor import parse_csv, ingest_csv, load_ohlcv
from src.data.knowledge_base import write_finding, query_relevant

# Test 4 is skipped when SKIP_LIVE_TESTS=1 (no testnet keys available)
SKIP_LIVE = os.getenv("SKIP_LIVE_TESTS", "0") == "1"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """A fresh, temporary SQLite database for each test."""
    db_path = str(tmp_path / "test.db")
    init_db(db_path)
    return db_path


@pytest.fixture
def blackbull_csv(tmp_path):
    """A small synthetic CSV in exact BlackBull MT4/MT5 export format."""
    content = textwrap.dedent("""\
        Date,Time,Open,High,Low,Close,Volume
        2025.01.01,00:00,50000.00,50500.00,49800.00,50200.00,120
        2025.01.01,01:00,50200.00,50600.00,50100.00,50450.00,95
        2025.01.01,02:00,50450.00,50900.00,50350.00,50750.00,140
        2025.01.01,03:00,50750.00,51000.00,50600.00,50850.00,88
        2025.01.01,04:00,50850.00,51200.00,50700.00,51100.00,112
        2025.01.01,05:00,51100.00,51300.00,50900.00,51050.00,76
        2025.01.01,06:00,51050.00,51400.00,50950.00,51300.00,99
        2025.01.01,07:00,51300.00,51600.00,51100.00,51500.00,130
        2025.01.01,08:00,51500.00,51800.00,51300.00,51650.00,105
        2025.01.01,09:00,51650.00,52000.00,51500.00,51900.00,118
    """)
    csv_path = tmp_path / "BTCUSDT_H1.csv"
    csv_path.write_text(content)
    return str(csv_path)


# ── Test 1: CSV round-trip ────────────────────────────────────────────────────

def test_csv_round_trip(blackbull_csv, tmp_db):
    """
    Ingest 10-row CSV. Assert all 10 rows are in ohlcv_history with correct values.
    """
    inserted = ingest_csv(blackbull_csv, "BTC/USDT", "1h", tmp_db)
    assert inserted == 10, f"Expected 10 inserted rows, got {inserted}"

    df = load_ohlcv("BTC/USDT", "1h", tmp_db)
    assert len(df) == 10, f"Expected 10 rows in DB, got {len(df)}"

    # Spot-check first row values against the CSV source
    first = df.iloc[0]
    assert first["open"] == pytest.approx(50000.00)
    assert first["high"] == pytest.approx(50500.00)
    assert first["low"] == pytest.approx(49800.00)
    assert first["close"] == pytest.approx(50200.00)
    assert first["volume"] == pytest.approx(120.0)

    # Verify timestamp is a valid Unix millisecond value (at or after 2025-01-01 UTC)
    assert first["timestamp"] >= 1_735_689_600_000  # 2025-01-01 00:00:00 UTC in ms


# ── Test 2: Duplicate guard ───────────────────────────────────────────────────

def test_duplicate_guard(blackbull_csv, tmp_db):
    """
    Ingest the same CSV twice. Row count in ohlcv_history must remain 10.
    """
    ingest_csv(blackbull_csv, "BTC/USDT", "1h", tmp_db)
    second_insert = ingest_csv(blackbull_csv, "BTC/USDT", "1h", tmp_db)

    assert second_insert == 0, "Second ingest should insert 0 rows (all duplicates)"

    df = load_ohlcv("BTC/USDT", "1h", tmp_db)
    assert len(df) == 10, "Row count must not change after duplicate ingest"


# ── Test 3: Date parsing edge cases ──────────────────────────────────────────

def test_date_parsing_edge_cases(tmp_path):
    """
    Test date parsing for midnight, year-end, and a date that crosses DST in some timezones.
    All must parse without error and produce correct UTC millisecond timestamps.
    """
    content = textwrap.dedent("""\
        Date,Time,Open,High,Low,Close,Volume
        2025.01.01,00:00,100.0,101.0,99.0,100.5,10
        2025.12.31,23:00,200.0,201.0,199.0,200.5,10
        2025.03.30,01:00,300.0,301.0,299.0,300.5,10
    """)
    csv_path = tmp_path / "edge_cases.csv"
    csv_path.write_text(content)

    df = parse_csv(str(csv_path))
    assert len(df) == 3, "All 3 edge-case rows should parse successfully"

    # Midnight 2025-01-01 UTC in milliseconds
    expected_midnight = 1_735_689_600_000
    assert df.iloc[0]["timestamp"] == expected_midnight, (
        f"Midnight UTC timestamp mismatch: {df.iloc[0]['timestamp']} != {expected_midnight}"
    )

    # Year-end: 2025-12-31 23:00 UTC
    # parse_csv sorts by timestamp, so order is: Jan 1 < Mar 30 < Dec 31 → iloc[2]
    expected_yearend = 1_767_222_000_000  # 2025-12-31 23:00:00 UTC
    assert df.iloc[2]["timestamp"] == expected_yearend, (
        f"Year-end UTC timestamp mismatch: {df.iloc[2]['timestamp']} != {expected_yearend}"
    )

    # All timestamps should be positive integers
    assert all(df["timestamp"] > 0)
    # All should be in milliseconds (roughly 2025 era: > 1.7 trillion)
    assert all(df["timestamp"] > 1_700_000_000_000)


# ── Test 4: CCXT sandbox connection ──────────────────────────────────────────

@pytest.mark.skipif(SKIP_LIVE, reason="SKIP_LIVE_TESTS=1 — no testnet keys available")
def test_ccxt_sandbox_connection(tmp_db):
    """
    Connect to Binance Testnet, fetch 5 candles for BTC/USDT 1h.
    Asserts valid OHLCV structure returned without error.
    Requires BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_SECRET in .env.
    """
    from src.data.ccxt_feed import CCXTFeed

    feed = CCXTFeed(symbol="BTC/USDT", timeframe="1h", db_path=tmp_db)

    # fetch_once() fetches from CCXT and writes to DB without starting the thread
    new_rows = feed.fetch_once()
    assert new_rows > 0, "fetch_once() should return at least 1 new candle"

    df = feed.get_latest_candles(n=5)
    assert len(df) >= 1, "get_latest_candles() should return at least 1 row"

    required_cols = {"timestamp", "open", "high", "low", "close"}
    assert required_cols.issubset(df.columns), (
        f"Missing columns: {required_cols - set(df.columns)}"
    )

    # Basic sanity: OHLCV values should be positive numbers
    for col in ["open", "high", "low", "close"]:
        assert (df[col] > 0).all(), f"Column {col} has non-positive values"

    # High must be >= low in every row
    assert (df["high"] >= df["low"]).all(), "high < low found in some rows"


# ── Test 5: KB write and query ───────────────────────────────────────────────

def test_kb_write_and_query(tmp_db):
    """
    Write 3 findings with different categories and keywords.
    Assert query_relevant returns the correct subset in recency order.
    """
    # Write findings at slightly different times to test recency ordering
    write_finding("failure_diagnosis", "RSI period too short caused overfitting", tmp_db)
    time.sleep(0.01)
    write_finding("parameter_insight", "EMA 50 provides strong support on BTC 1h", tmp_db)
    time.sleep(0.01)
    write_finding("general", "Market was in a sideways regime during Q1 2025", tmp_db)

    # Search for "RSI" — should return only the failure_diagnosis entry
    results = query_relevant(["rsi"], tmp_db)
    assert len(results) == 1
    assert results[0]["category"] == "failure_diagnosis"
    assert "rsi" in results[0]["content"].lower()

    # Search for "ema" and "support" — should return only the parameter_insight
    results = query_relevant(["ema", "support"], tmp_db)
    assert len(results) == 1
    assert results[0]["category"] == "parameter_insight"

    # Search for "regime" — should return the general entry
    results = query_relevant(["regime"], tmp_db)
    assert len(results) == 1
    assert results[0]["category"] == "general"

    # Search for a term that appears in none
    results = query_relevant(["bollinger"], tmp_db)
    assert results == []

    # Multiple keywords: "rsi" OR "ema" should return 2 results, most recent first
    results = query_relevant(["rsi", "ema"], tmp_db)
    assert len(results) == 2
    assert results[0]["created_at"] >= results[1]["created_at"]


# ── Test 6: Schema idempotency ───────────────────────────────────────────────

def test_schema_idempotency(tmp_path):
    """
    Call init_db() twice on the same path. Must not raise any error.
    Tables must exist after both calls.
    """
    db_path = str(tmp_path / "idempotent.db")

    # First call creates all tables
    init_db(db_path)

    # Second call must succeed silently (IF NOT EXISTS)
    init_db(db_path)

    # Verify all expected tables exist
    conn = get_connection(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    expected_tables = {
        "ohlcv_history", "live_candles", "strategies",
        "trades", "performance", "knowledge_base", "reasoning_logs"
    }
    assert expected_tables.issubset(tables), (
        f"Missing tables after double init: {expected_tables - tables}"
    )
