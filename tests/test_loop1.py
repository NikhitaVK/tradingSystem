"""
test_loop1.py — Isolation tests for Module 3 (Loop 1 strategy discovery agents).

All 7 tests use a mock Claude client. Zero real API calls are made.
The mock returns predetermined sequences of tool calls and text responses.

Tests:
  1. Correct tool call order (KB query → run_backtest → text response)
  2. Fail path feeds diagnosis to next strategy agent call
  3. KB updated on failure (failure_diagnosis row written)
  4. Pass path saves strategy (save_validated_strategy called with valid spec)
  5. MaxAttemptsExceeded raised after exactly N failed attempts
  6. Thinking block preservation across turns
  7. Pair screener narrows to top 5 candidates

All tests pass → Module 3 complete.
"""
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Test infrastructure ───────────────────────────────────────────────────────

# Minimal valid strategy spec that the backtest engine accepts
_VALID_SPEC = {
    "name": "Test RSI Mean Reversion",
    "symbol": "BTC/USDT",
    "timeframe": "1h",
    "indicators": [{"type": "RSI", "period": 14}],
    "entry": {
        "logic": "AND",
        "conditions": [{"indicator": "RSI_14", "operator": "<", "value": 30}],
    },
    "exit": {
        "logic": "OR",
        "conditions": [
            {"indicator": "RSI_14", "operator": ">", "value": 70},
            {"type": "stop_loss_pct", "value": 2.0},
            {"type": "take_profit_pct", "value": 4.0},
        ],
    },
}

_VIABLE_BACKTEST = {
    "viable": True,
    "aggregate": {
        "win_rate_mean": 0.55,
        "sharpe_mean": 1.2,
        "max_drawdown_worst": 0.08,
        "total_trades": 45,
        "profit_factor_mean": 1.8,
        "sortino_mean": 1.5,
        "regime_breakdown": {"bull": [1], "bear": [], "sideways": [2, 3]},
    },
    "slices": [
        {"slice_id": 1, "sharpe": 1.1, "win_rate": 0.55, "total_trades": 15, "regime": "bull"},
        {"slice_id": 2, "sharpe": 1.3, "win_rate": 0.57, "total_trades": 16, "regime": "sideways"},
        {"slice_id": 3, "sharpe": 1.2, "win_rate": 0.53, "total_trades": 14, "regime": "sideways"},
    ],
    "calibration": {
        "degradation_threshold": 0.45,
        "walk_forward_efficiency": 0.72,
        "position_sizing": {"method": "atr", "atr_period": 14, "atr_multiplier": 1.5, "risk_per_trade_pct": 0.01},
    },
}

_ANALYST_PASS = {
    "pass": True,
    "diagnosis": "Strategy demonstrates genuine edge with sound economic mechanism.",
    "challenges": [],
}

_ANALYST_FAIL = {
    "pass": False,
    "diagnosis": "WFE below 0.5 indicates overfitting to in-sample noise.",
    "challenges": ["WFE=0.31 < 0.5 threshold", "Only fires in sideways regime"],
}


class MockClaudeClient:
    """
    Mock Claude client for testing. Returns predetermined responses in sequence.

    Each response is a tuple: (response_text, tool_calls, thinking_blocks)
    tool_calls format: [{'name': str, 'input': dict, 'id': str}]
    thinking_blocks: list of thinking block dicts (for testing preservation)
    """

    def __init__(self, responses: list):
        self._responses = list(responses)
        self._idx = 0
        self.calls = []  # record of all chat() calls for assertion

    def chat(self, messages, tools, system_prompt, thinking_budget, agent_name="unknown", strategy_id=None):
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "system_prompt": system_prompt,
            "thinking_budget": thinking_budget,
            "agent_name": agent_name,
        })

        if self._idx >= len(self._responses):
            raise StopIteration(f"MockClaudeClient exhausted after {self._idx} calls")

        resp = self._responses[self._idx]
        self._idx += 1

        response_text, tool_calls_raw, thinking_blocks = resp

        # Build updated_messages: preserve thinking blocks + add assistant turn
        assistant_content = list(thinking_blocks)
        if response_text:
            assistant_content.append({"type": "text", "text": response_text})
        for tc in tool_calls_raw:
            assistant_content.append({
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["name"],
                "input": tc["input"],
            })

        updated_messages = list(messages) + [
            {"role": "assistant", "content": assistant_content}
        ]

        return response_text, tool_calls_raw, updated_messages

    def append_tool_result(self, messages, tool_id, result):
        return list(messages) + [
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": result}]}
        ]


def _make_db() -> str:
    """Create a temp SQLite DB with the required schema. Returns db_path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()

    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ohlcv_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, volume REAL,
            UNIQUE(symbol, timeframe, timestamp)
        );
        CREATE TABLE IF NOT EXISTS strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, spec TEXT NOT NULL, performance TEXT,
            degradation_threshold REAL, position_sizing TEXT,
            status TEXT DEFAULT 'active', created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS knowledge_base (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT, strategy_id INTEGER, content TEXT, created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS reasoning_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT, strategy_id INTEGER, thinking TEXT, response TEXT, created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER, symbol TEXT, side TEXT,
            entry_price REAL, exit_price REAL, amount_usdt REAL,
            pnl_pct REAL, outcome TEXT, entry_at INTEGER, exit_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER, timestamp INTEGER,
            rolling_win_rate REAL, rolling_trades INTEGER
        );
    """)
    conn.commit()
    conn.close()
    return db_path


# ── Test 1: Correct tool call order ──────────────────────────────────────────

def test_tool_call_order():
    """
    Strategy agent should call query_knowledge_base before run_backtest.
    The mock sequences: KB query → run_backtest → text response (finalise).
    Assert calls happened in that order.
    """
    db_path = _make_db()

    # Response 1: agent calls query_knowledge_base
    resp1 = ("", [{"name": "query_knowledge_base", "input": {"keywords": ["RSI", "BTC"]}, "id": "tc1"}], [])
    # Response 2: after KB result, agent calls run_backtest
    resp2 = ("", [{"name": "run_backtest", "input": {"strategy_spec": _VALID_SPEC}, "id": "tc2"}], [])
    # Response 3: after backtest result, agent finishes
    resp3 = (json.dumps(_VALID_SPEC), [], [])

    client = MockClaudeClient([resp1, resp2, resp3])

    # Patch run_backtest handler to return viable result without touching DB
    with patch("src.agents.tools.handle_run_backtest", return_value=json.dumps(_VIABLE_BACKTEST)):
        spec, results = strategy_agent_generate_with_client(client, db_path)

    # Verify order: first call has no tool_calls (no tool yet), tool call names in sequence
    tool_names = []
    for call in client.calls:
        # Tool call names are implicit — check the responses we set up were used in order
        pass

    # The key assertion: KB was queried before backtest
    # We verify this by checking that tc1 (KB) comes before tc2 (backtest) in message history
    # The updated_messages from call 2 should contain the tool_result for tc1
    call2_messages = client.calls[1]["messages"]
    tool_result_ids = [
        block.get("tool_use_id")
        for msg in call2_messages
        if msg["role"] == "user"
        for block in (msg["content"] if isinstance(msg["content"], list) else [])
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert "tc1" in tool_result_ids, "KB query result should be in messages before run_backtest is called"

    os.unlink(db_path)


# ── Test 2: Fail path feeds diagnosis ────────────────────────────────────────

def test_fail_path_feeds_diagnosis():
    """
    When analyst returns fail, the diagnosis must appear in the next call
    to the strategy agent's system prompt.
    """
    db_path = _make_db()

    with patch("src.loop1.strategy_agent.generate_strategy") as mock_gen, \
         patch("src.loop1.analyst_agent.evaluate") as mock_eval, \
         patch("src.loop1.run_backtest", return_value=_VIABLE_BACKTEST), \
         patch("src.loop1.handle_save_validated_strategy", return_value=1), \
         patch("src.loop1.screen_pair_universe", return_value=[{"symbol": "BTC/USDT"}]), \
         patch("src.loop1.ClaudeClient"):

        # Attempt 1: generate succeeds, analyst fails
        # Attempt 2: generate succeeds, analyst passes
        mock_gen.side_effect = [
            (_VALID_SPEC, _VIABLE_BACKTEST),
            (_VALID_SPEC, _VIABLE_BACKTEST),
        ]
        mock_eval.side_effect = [
            _ANALYST_FAIL,
            _ANALYST_PASS,
        ]

        from src.loop1 import run_loop1
        result = run_loop1(db_path, max_attempts=5)

    # Assert: the second generate_strategy call received the diagnosis from attempt 1
    second_call_kwargs = mock_gen.call_args_list[1][1]  # keyword args of second call
    assert "previous_diagnosis" in second_call_kwargs, "previous_diagnosis should be passed on retry"
    assert "WFE below 0.5" in second_call_kwargs["previous_diagnosis"], \
        "Diagnosis from failed attempt should appear in next call"

    os.unlink(db_path)


# ── Test 3: KB updated on failure ─────────────────────────────────────────────

def test_kb_updated_on_failure():
    """
    After a failed analyst evaluation, a row with category='failure_diagnosis'
    must exist in the knowledge_base table.
    """
    db_path = _make_db()

    with patch("src.loop1.strategy_agent.generate_strategy") as mock_gen, \
         patch("src.loop1.analyst_agent.evaluate") as mock_eval, \
         patch("src.loop1.run_backtest", return_value=_VIABLE_BACKTEST), \
         patch("src.loop1.handle_save_validated_strategy", return_value=1), \
         patch("src.loop1.screen_pair_universe", return_value=[{"symbol": "BTC/USDT"}]), \
         patch("src.loop1.ClaudeClient"):

        mock_gen.side_effect = [
            (_VALID_SPEC, _VIABLE_BACKTEST),
            (_VALID_SPEC, _VIABLE_BACKTEST),
        ]
        mock_eval.side_effect = [_ANALYST_FAIL, _ANALYST_PASS]

        from src.loop1 import run_loop1
        run_loop1(db_path, max_attempts=5)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT category, content FROM knowledge_base WHERE category='failure_diagnosis'"
    ).fetchall()
    conn.close()

    assert len(rows) >= 1, "Expected at least 1 failure_diagnosis KB entry after a failed attempt"
    assert "WFE below 0.5" in rows[0][1], "KB entry should contain the analyst's diagnosis"

    os.unlink(db_path)


# ── Test 4: Pass path saves strategy ─────────────────────────────────────────

def test_pass_path_saves_strategy():
    """
    When analyst passes, save_validated_strategy must be called with a valid spec,
    and a row should exist in the strategies table.
    """
    db_path = _make_db()

    with patch("src.loop1.strategy_agent.generate_strategy") as mock_gen, \
         patch("src.loop1.analyst_agent.evaluate") as mock_eval, \
         patch("src.loop1.run_backtest", return_value=_VIABLE_BACKTEST), \
         patch("src.loop1.screen_pair_universe", return_value=[{"symbol": "BTC/USDT"}]), \
         patch("src.loop1.ClaudeClient"):

        mock_gen.return_value = (_VALID_SPEC, _VIABLE_BACKTEST)
        mock_eval.return_value = _ANALYST_PASS

        from src.loop1 import run_loop1
        result = run_loop1(db_path, max_attempts=5)

    # Strategy returned from run_loop1
    assert result["viable"] is True
    assert result["spec"]["symbol"] == "BTC/USDT"
    assert result["id"] is not None

    # Row in strategies table
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT id, name, status FROM strategies").fetchall()
    conn.close()
    assert len(rows) == 1, "Expected exactly 1 strategy saved"
    assert rows[0][2] == "active"

    os.unlink(db_path)


# ── Test 5: MaxAttemptsExceeded after N fails ─────────────────────────────────

def test_max_attempts_exceeded():
    """
    If analyst always returns fail, MaxAttemptsExceeded must be raised after
    exactly max_attempts iterations (not before, not after).
    """
    from src.loop1 import MaxAttemptsExceeded

    db_path = _make_db()
    max_attempts = 5

    call_count = {"generate": 0, "evaluate": 0}

    def counting_generate(**kwargs):
        call_count["generate"] += 1
        return _VALID_SPEC, _VIABLE_BACKTEST

    def counting_evaluate(spec, results, client):
        call_count["evaluate"] += 1
        return _ANALYST_FAIL

    with patch("src.loop1.strategy_agent.generate_strategy", side_effect=counting_generate), \
         patch("src.loop1.analyst_agent.evaluate", side_effect=counting_evaluate), \
         patch("src.loop1.screen_pair_universe", return_value=[{"symbol": "BTC/USDT"}]), \
         patch("src.loop1.ClaudeClient"):

        from src.loop1 import run_loop1
        with pytest.raises(MaxAttemptsExceeded):
            run_loop1(db_path, max_attempts=max_attempts)

    assert call_count["generate"] == max_attempts, \
        f"Expected {max_attempts} generate calls, got {call_count['generate']}"
    assert call_count["evaluate"] == max_attempts, \
        f"Expected {max_attempts} evaluate calls, got {call_count['evaluate']}"

    os.unlink(db_path)


# ── Test 6: Thinking block preservation ──────────────────────────────────────

def test_thinking_block_preservation():
    """
    Thinking blocks from turn 1 must appear unmodified in the messages list
    passed to turn 2.

    This verifies the core API contract: signed thinking blocks cannot be
    summarised or modified without causing Anthropic API validation errors.
    """
    # Simulate a thinking block as Anthropic returns it
    thinking_block = {
        "type": "thinking",
        "thinking": "I need to reason about this market mechanism...",
        "signature": "Eo8BClMIARgCIk0KCxIJY29tcHV0ZXISPhI",  # mock signature
    }

    db_path = _make_db()

    # Response 1: includes a thinking block + a tool call
    resp1 = (
        "",
        [{"name": "query_knowledge_base", "input": {"keywords": ["test"]}, "id": "tc1"}],
        [thinking_block],
    )
    # Response 2: no more tool calls — finish
    resp2 = (json.dumps(_VALID_SPEC), [], [])

    client = MockClaudeClient([resp1, resp2])

    with patch("src.agents.tools.handle_run_backtest", return_value=json.dumps(_VIABLE_BACKTEST)):
        strategy_agent_generate_with_client(client, db_path)

    # The second call's messages should contain the thinking block from turn 1
    call2_messages = client.calls[1]["messages"]

    found_thinking = False
    for msg in call2_messages:
        if msg["role"] == "assistant":
            for block in (msg["content"] if isinstance(msg["content"], list) else []):
                if isinstance(block, dict) and block.get("type") == "thinking":
                    assert block["signature"] == thinking_block["signature"], \
                        "Thinking block signature must be preserved exactly"
                    assert block["thinking"] == thinking_block["thinking"], \
                        "Thinking block content must be preserved exactly"
                    found_thinking = True

    assert found_thinking, "Thinking block from turn 1 must appear in turn 2 messages"
    os.unlink(db_path)


# ── Test 7: Pair screener narrows to 5 ────────────────────────────────────────

def test_pair_screener_narrows_to_5():
    """
    When CCXT returns 30+ pairs above the volume threshold,
    screen_pair_universe() must return exactly UNIVERSE_TOP_N (5) candidates.
    The strategy agent must receive exactly 5 candidates.
    """
    from config.settings import UNIVERSE_TOP_N

    db_path = _make_db()

    # Mock CCXT returning 30 pairs all above threshold
    mock_tickers = {}
    for i in range(30):
        sym = f"COIN{i:02d}/USDT"
        mock_tickers[sym] = {"quoteVolume": 100_000_000 + i * 1_000_000}

    candidates_seen = {}

    def capturing_generate(pair_candidates, **kwargs):
        candidates_seen["count"] = len(pair_candidates)
        candidates_seen["symbols"] = [c["symbol"] for c in pair_candidates]
        return _VALID_SPEC, _VIABLE_BACKTEST

    with patch("src.loop1.analyst_agent.evaluate", return_value=_ANALYST_PASS), \
         patch("src.loop1.strategy_agent.generate_strategy", side_effect=capturing_generate), \
         patch("src.loop1.run_backtest", return_value=_VIABLE_BACKTEST), \
         patch("src.loop1.handle_save_validated_strategy", return_value=1), \
         patch("src.loop1.ClaudeClient"), \
         patch("ccxt.binance") as mock_ccxt_cls:

        mock_exchange = MagicMock()
        mock_exchange.fetch_tickers.return_value = mock_tickers
        mock_ccxt_cls.return_value = mock_exchange

        # Patch _score_candidates to return sorted list without needing DB data
        with patch("src.loop1._score_candidates", side_effect=lambda cands, db: [
            {**c, "signal_count": 50 - i, "sharpe": 0.0}
            for i, c in enumerate(cands)
        ]):
            from src.loop1 import run_loop1
            run_loop1(db_path, max_attempts=1)

    assert candidates_seen.get("count") == UNIVERSE_TOP_N, \
        f"Expected {UNIVERSE_TOP_N} candidates passed to strategy agent, got {candidates_seen.get('count')}"

    os.unlink(db_path)


# ── Helper: call strategy_agent directly with mock client ────────────────────

def strategy_agent_generate_with_client(client, db_path):
    """Helper to call generate_strategy() with a pre-built mock client."""
    from src.agents.strategy_agent import generate_strategy
    return generate_strategy(
        pair_candidates=[{"symbol": "BTC/USDT", "signal_count": 50, "sharpe": 0.0}],
        kb_context=[],
        client=client,
        db_path=db_path,
        mcp_client=None,
        previous_diagnosis=None,
    )
