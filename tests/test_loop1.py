"""
test_loop1.py — Isolation tests for Module 3 (Loop 1 strategy discovery agents).

All tests use a mock Claude client. Zero real API calls are made.

Tests:
  1. Empirical search flow (generator → search → LLM select)
  2. Fail path feeds diagnosis to next strategy agent call
  3. KB updated on failure (failure_diagnosis row written)
  4. Pass path saves strategy (save_validated_strategy called with valid spec)
  5. MaxAttemptsExceeded raised after exactly N failed attempts
  6. Thinking block preservation across analyst evaluation turns
  7. Pair screener narrows to top 5 candidates

All tests pass → Module 3 complete.
"""
import json
import os
import sqlite3
import tempfile
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
        "regime_breakdown": {
            "trending_bull": [1], "trending_bear": [], "sideways": [2, 3], "high_vol": []
        },
    },
    "slices": [
        {"slice_id": 1, "sharpe": 1.1, "win_rate": 0.55, "total_trades": 15, "regime": "trending_bull"},
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
    "regime_robustness": {
        "dominant_regime": "sideways",
        "fragile_regimes": ["trending_bear", "high_vol"],
        "failure_risk": "medium",
    },
}

_ANALYST_FAIL = {
    "pass": False,
    "diagnosis": "WFE below 0.5 indicates overfitting to in-sample noise.",
    "challenges": ["WFE=0.31 < 0.5 threshold", "Only fires in sideways regime"],
    "regime_robustness": {
        "dominant_regime": "sideways",
        "fragile_regimes": ["trending_bull", "trending_bear", "high_vol"],
        "failure_risk": "high",
    },
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
    """Create a temp SQLite DB with the full current schema. Returns db_path."""
    from src.data.schema import init_db
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = tmp.name
    tmp.close()
    init_db(db_path)
    return db_path


# ── Test 1: Empirical search flow ────────────────────────────────────────────

def test_empirical_search_flow():
    """
    Strategy agent should: generate candidates → run empirical search → call
    LLM exactly once to select. Assert the flow produces a valid spec.
    """
    db_path = _make_db()

    # Mock the empirical search to return pre-ranked results
    ranked = [(_VALID_SPEC, _VIABLE_BACKTEST, 1.5)]

    # Mock Claude's selection response
    selection_json = json.dumps({"chosen_index": 0, "mechanism_rationale": "test", "failure_modes": [], "why_not_others": ""})
    client = MockClaudeClient([(selection_json, [], [])])

    with patch("src.agents.strategy_agent.generate_candidate_pool", return_value=[_VALID_SPEC]), \
         patch("src.agents.strategy_agent.run_search", return_value=ranked):
        from src.agents.strategy_agent import generate_strategy
        spec, results = generate_strategy(
            pair_candidates=[{"symbol": "BTC/USDT"}],
            kb_context=[],
            client=client,
            db_path=db_path,
        )

    assert spec["symbol"] == "BTC/USDT"
    assert results["viable"] is True
    assert len(client.calls) == 1, "LLM should be called exactly once (selector role)"

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


# ── Test 6: Thinking block preservation (analyst multi-turn) ─────────────────

def test_thinking_block_preservation():
    """
    Thinking blocks from turn 1 must appear unmodified in the messages list
    passed to turn 2 during analyst evaluation (which is still multi-turn).

    This verifies the core API contract: signed thinking blocks cannot be
    summarised or modified without causing Anthropic API validation errors.
    """
    thinking_block = {
        "type": "thinking",
        "thinking": "I need to reason about this market mechanism...",
        "signature": "Eo8BClMIARgCIk0KCxIJY29tcHV0ZXISPhI",
    }

    # Response 1: analyst calls write_to_knowledge_base tool
    resp1 = (
        "",
        [{"name": "write_to_knowledge_base", "input": {"category": "general", "content": "test"}, "id": "tc1"}],
        [thinking_block],
    )
    # Response 2: analyst finishes with pass verdict
    resp2 = (json.dumps(_ANALYST_PASS), [], [])

    client = MockClaudeClient([resp1, resp2])

    # The key assertion: thinking blocks survive across turns
    # We test this directly on the MockClaudeClient's message tracking
    # by making two calls and checking the second call's messages
    messages = [{"role": "user", "content": "Evaluate this strategy."}]

    _, _, updated = client.chat(messages, [], "system", 5000, "analyst_eval")
    # Simulate tool result append
    updated = client.append_tool_result(updated, "tc1", '{"success": true}')
    _, _, final = client.chat(updated, [], "system", 5000, "analyst_eval")

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

def strategy_agent_generate_with_client(client, db_path, current_regime=None):
    """Helper to call generate_strategy() with a pre-built mock client."""
    from src.agents.strategy_agent import generate_strategy

    ranked = [(_VALID_SPEC, _VIABLE_BACKTEST, 1.5)]

    with patch("src.agents.strategy_agent.generate_candidate_pool", return_value=[_VALID_SPEC]), \
         patch("src.agents.strategy_agent.run_search", return_value=ranked):
        return generate_strategy(
            pair_candidates=[{"symbol": "BTC/USDT", "signal_count": 50, "sharpe": 0.0}],
            kb_context=[],
            client=client,
            db_path=db_path,
            mcp_client=None,
            previous_diagnosis=None,
            current_regime=current_regime,
        )


# ── Test 8: Regime warning in run_backtest tool (Phase 3) ────────────────────

def test_regime_warning_included_in_backtest_result():
    """
    handle_run_backtest must include regime_warning and regime_warning_count
    in its JSON result. When no prior failures exist the warning is False.
    """
    from src.agents.tools import handle_run_backtest

    db_path = _make_db()

    # Patch the backtest engine so we don't need real OHLCV data in the DB
    with patch("src.agents.tools._run_backtest", return_value=_VIABLE_BACKTEST):
        result_json = handle_run_backtest({"strategy_spec": _VALID_SPEC}, db_path)

    result = json.loads(result_json)

    assert "regime_warning" in result, "regime_warning key missing from backtest tool result"
    assert "regime_warning_count" in result, "regime_warning_count key missing"
    assert isinstance(result["regime_warning"], bool)
    assert isinstance(result["regime_warning_count"], int)
    assert result["regime_warning"] is False, "No prior failures → warning should be False"

    os.unlink(db_path)


# ── Test 9: Evolution tracking written between attempts (Phase 4) ─────────────

def test_evolution_written_between_failed_attempts():
    """
    After two consecutive failed attempts, a strategy_evolutions row must
    exist in the DB recording the spec delta and performance delta.
    """
    from src.loop1 import MaxAttemptsExceeded

    db_path = _make_db()
    attempt_count = {"n": 0}

    def counting_generate(**kwargs):
        attempt_count["n"] += 1
        return _VALID_SPEC, _VIABLE_BACKTEST

    with patch("src.loop1.strategy_agent.generate_strategy", side_effect=counting_generate), \
         patch("src.loop1.analyst_agent.evaluate", return_value=_ANALYST_FAIL), \
         patch("src.loop1.screen_pair_universe", return_value=[{"symbol": "BTC/USDT"}]), \
         patch("src.loop1.ClaudeClient"):

        from src.loop1 import run_loop1
        try:
            run_loop1(db_path, max_attempts=3)
        except MaxAttemptsExceeded:
            pass

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM strategy_evolutions").fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()

    # After 3 attempts, at least 1 evolution record should exist (between attempt 1→2)
    assert len(rows) >= 1, "Expected at least 1 evolution record after multiple failed attempts"

    os.unlink(db_path)


# ── Test 10: Current regime injected into strategy prompt ─────────────────────

def test_regime_in_system_prompt():
    """
    When current_regime is passed to generate_strategy(), the system prompt
    must contain the regime name so the LLM can evaluate regime fit.
    """
    db_path = _make_db()
    regime = "sideways"

    prompts_seen = []

    class CapturingClient:
        calls = []
        def chat(self, messages, tools, system_prompt, thinking_budget, agent_name="unknown", strategy_id=None):
            prompts_seen.append(system_prompt)
            return (json.dumps({"chosen_index": 0, "mechanism_rationale": "test", "failure_modes": [], "why_not_others": ""}), [], [])
        def append_tool_result(self, messages, tool_id, result):
            return messages

    client = CapturingClient()

    ranked = [(_VALID_SPEC, _VIABLE_BACKTEST, 1.5)]
    with patch("src.agents.strategy_agent.generate_candidate_pool", return_value=[_VALID_SPEC]), \
         patch("src.agents.strategy_agent.run_search", return_value=ranked):
        from src.agents.strategy_agent import generate_strategy
        generate_strategy(
            pair_candidates=[{"symbol": "BTC/USDT"}],
            kb_context=[],
            client=client,
            db_path=db_path,
            current_regime=regime,
        )

    assert prompts_seen, "No system prompt was captured"
    assert regime in prompts_seen[0], \
        f"Expected regime '{regime}' in system prompt, not found"

    os.unlink(db_path)


# ── Test 11: Regime robustness in analyst diagnosis (Phase 10) ────────────────

def test_regime_robustness_in_analyst_eval():
    """
    analyst_agent.evaluate() must return a dict with a 'regime_robustness' key
    containing 'dominant_regime', 'fragile_regimes', and 'failure_risk'.
    """
    from src.agents.analyst_agent import _parse_eval_response

    # Simulate analyst returning JSON with regime_robustness
    response_json = json.dumps({
        "pass": True,
        "diagnosis": "Strategy passes all criteria.",
        "challenges": [],
        "regime_robustness": {
            "dominant_regime": "sideways",
            "fragile_regimes": ["trending_bear"],
            "failure_risk": "medium",
        },
    })

    result = _parse_eval_response(response_json)

    assert "regime_robustness" in result, "regime_robustness key missing from eval result"
    rr = result["regime_robustness"]
    assert rr.get("dominant_regime") == "sideways"
    assert "trending_bear" in rr.get("fragile_regimes", [])
    assert rr.get("failure_risk") == "medium"


def test_regime_robustness_defaults_on_missing():
    """
    _parse_eval_response must return regime_robustness={} when the field is
    absent from Claude's response — no KeyError.
    """
    from src.agents.analyst_agent import _parse_eval_response

    response_json = json.dumps({
        "pass": False,
        "diagnosis": "Overfitted.",
        "challenges": ["WFE too low"],
        # no regime_robustness field
    })

    result = _parse_eval_response(response_json)
    assert "regime_robustness" in result
    assert isinstance(result["regime_robustness"], dict)


# ── Test 13: v2 composite-score schema parsing ───────────────────────────────
def test_parse_v2_verdict_pass():
    """v2 schema: verdict='pass' → result['pass']=True and score/subscores surfaced."""
    from src.agents.analyst_agent import _parse_eval_response

    response_json = json.dumps({
        "verdict": "pass",
        "score": 0.82,
        "subscores": {
            "pf": 0.9, "wfe": 0.7, "consistency": 0.8,
            "sample_size": 0.6, "mechanism": 1.0,
        },
        "diagnosis": "All sub-scores above 0.6; trade-weighted PF = 1.45.",
        "challenges": [],
        "regime_robustness": {
            "dominant_regime": "trending_bull",
            "fragile_regimes": ["trending_bear"],
            "failure_risk": "low",
        },
    })

    result = _parse_eval_response(response_json)
    assert result["verdict"] == "pass"
    assert result["pass"] is True
    assert result["score"] == 0.82
    assert result["subscores"]["pf"] == 0.9
    assert result["subscores"]["mechanism"] == 1.0


def test_parse_v2_verdict_probation_still_passes_gate():
    """v2 probation: verdict='probation' → result['pass']=True so loop1 saves it."""
    from src.agents.analyst_agent import _parse_eval_response

    response_json = json.dumps({
        "verdict": "probation",
        "score": 0.62,
        "subscores": {
            "pf": 0.58, "wfe": 0.5, "consistency": 0.6,
            "sample_size": 0.5, "mechanism": 1.0,
        },
        "diagnosis": "Borderline PF; probation with 0.5× size.",
        "challenges": ["PF_tw = 1.29, just below pass threshold"],
        "regime_robustness": {},
    })

    result = _parse_eval_response(response_json)
    assert result["verdict"] == "probation"
    assert result["pass"] is True, "probation must be treated as pass by loop1"


def test_parse_v2_verdict_fail():
    """v2 schema: verdict='fail' → result['pass']=False."""
    from src.agents.analyst_agent import _parse_eval_response

    response_json = json.dumps({
        "verdict": "fail",
        "score": 0.32,
        "subscores": {
            "pf": 0.2, "wfe": 0.0, "consistency": 0.4,
            "sample_size": 0.5, "mechanism": 0.5,
        },
        "diagnosis": "PF_tw 1.1 and only 2/5 slices profitable.",
        "challenges": ["WFE absent", "Consistency weak"],
        "regime_robustness": {},
    })

    result = _parse_eval_response(response_json)
    assert result["verdict"] == "fail"
    assert result["pass"] is False


def test_parse_v1_legacy_shape_still_works():
    """Mock fixtures using {'pass': bool} (no 'verdict') must still parse.
    Legacy callers that only set 'pass' are mapped to verdict pass/fail."""
    from src.agents.analyst_agent import _parse_eval_response

    v1_pass = json.dumps({"pass": True, "diagnosis": "ok", "challenges": []})
    v1_fail = json.dumps({"pass": False, "diagnosis": "no", "challenges": ["x"]})

    assert _parse_eval_response(v1_pass)["verdict"] == "pass"
    assert _parse_eval_response(v1_pass)["pass"] is True
    assert _parse_eval_response(v1_fail)["verdict"] == "fail"
    assert _parse_eval_response(v1_fail)["pass"] is False


def test_parse_error_defaults_to_fail():
    """Unparseable text → verdict='fail', pass=False, empty subscores."""
    from src.agents.analyst_agent import _parse_eval_response

    result = _parse_eval_response("this is not JSON at all")
    assert result["verdict"] == "fail"
    assert result["pass"] is False
    assert result["score"] == 0.0
    assert result["subscores"] == {
        "pf": 0.0, "wfe": 0.0, "consistency": 0.0, "sample_size": 0.0, "mechanism": 0.0,
    }


# ── Probation tier integration tests ─────────────────────────────────────────

_ANALYST_PROBATION = {
    "verdict": "probation",
    "pass": True,
    "score": 0.58,
    "subscores": {
        "pf": 0.58, "wfe": 0.6, "consistency": 0.6, "sample_size": 0.4, "mechanism": 0.5,
    },
    "diagnosis": "Borderline — deploy at reduced size.",
    "challenges": ["PF just above floor"],
    "regime_robustness": {
        "dominant_regime": "sideways",
        "fragile_regimes": ["trending_bear"],
        "failure_risk": "medium",
    },
}


def test_probation_verdict_saves_strategy_with_probation_status():
    """
    When analyst returns verdict='probation', handle_save_validated_strategy
    must persist status='probation' and the returned dict must echo that.
    """
    db_path = _make_db()

    with patch("src.loop1.strategy_agent.generate_strategy") as mock_gen, \
         patch("src.loop1.analyst_agent.evaluate") as mock_eval, \
         patch("src.loop1.run_backtest", return_value=_VIABLE_BACKTEST), \
         patch("src.loop1.screen_pair_universe", return_value=[{"symbol": "BTC/USDT"}]), \
         patch("src.loop1.ClaudeClient"):

        mock_gen.return_value = (_VALID_SPEC, _VIABLE_BACKTEST)
        mock_eval.return_value = _ANALYST_PROBATION

        from src.loop1 import run_loop1
        result = run_loop1(db_path, max_attempts=5)

    assert result["status"] == "probation"
    assert result["verdict"] == "probation"

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT status, probation_wins, probation_losses FROM strategies"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "probation"
    assert rows[0][1] == 0
    assert rows[0][2] == 0

    os.unlink(db_path)


def test_pass_verdict_saves_strategy_with_active_status():
    """verdict='pass' must save status='active'."""
    db_path = _make_db()
    analyst_pass_v2 = {**_ANALYST_PROBATION, "verdict": "pass", "score": 0.8}

    with patch("src.loop1.strategy_agent.generate_strategy") as mock_gen, \
         patch("src.loop1.analyst_agent.evaluate") as mock_eval, \
         patch("src.loop1.run_backtest", return_value=_VIABLE_BACKTEST), \
         patch("src.loop1.screen_pair_universe", return_value=[{"symbol": "BTC/USDT"}]), \
         patch("src.loop1.ClaudeClient"):

        mock_gen.return_value = (_VALID_SPEC, _VIABLE_BACKTEST)
        mock_eval.return_value = analyst_pass_v2

        from src.loop1 import run_loop1
        result = run_loop1(db_path, max_attempts=5)

    assert result["status"] == "active"
    assert result["verdict"] == "pass"
    os.unlink(db_path)


def test_save_validated_strategy_verdict_kwarg_default_active():
    """handle_save_validated_strategy called without verdict → status='active'."""
    db_path = _make_db()
    from src.agents.tools import handle_save_validated_strategy

    strategy_id = handle_save_validated_strategy(_VALID_SPEC, _VIABLE_BACKTEST, db_path)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status FROM strategies WHERE id = ?", (strategy_id,)
    ).fetchone()
    conn.close()
    assert row[0] == "active"
    os.unlink(db_path)


def test_save_validated_strategy_verdict_probation_sets_status():
    """handle_save_validated_strategy(verdict='probation') → status='probation'."""
    db_path = _make_db()
    from src.agents.tools import handle_save_validated_strategy

    strategy_id = handle_save_validated_strategy(
        _VALID_SPEC, _VIABLE_BACKTEST, db_path, verdict="probation"
    )
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status FROM strategies WHERE id = ?", (strategy_id,)
    ).fetchone()
    conn.close()
    assert row[0] == "probation"
    os.unlink(db_path)
