"""
test_loop2.py — Module 4 (Execution Loop) test suite.

14 tests across 6 groups:
  A — Signal detection (2)
  B — Risk agent (3)
  C — Debate CP2 (2)
  D — Execution agent (2)
  E — Degradation monitor (2)
  F — Full Loop 2 integration (3)
"""
import os
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch, call

import numpy as np
import pandas as pd
import pytest

from src.agents.risk_agent import RiskAgent
from src.agents.execution_agent import place_trade
from src.backtest.strategy_runner import build_signals
from src.data.schema import init_db, get_connection
from src.monitor.degradation_monitor import DegradationMonitor
from src.loop2 import run_loop2, StrategyDegradedException


# ── Shared Test Helpers ─────────────────────────────────────────────────────


def _make_db() -> str:
    """Create a fresh temp DB with schema initialised."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    return path


def _seed_strategy(db_path: str, strategy_id: int = 1) -> None:
    """Insert a minimal strategy row."""
    conn = get_connection(db_path)
    conn.execute(
        "INSERT INTO strategies (id, name, spec, status, created_at) "
        "VALUES (?, 'test', '{}', 'active', ?)",
        (strategy_id, int(time.time() * 1000)),
    )
    conn.commit()
    conn.close()


def _seed_trades(db_path: str, strategy_id: int, wins: int, losses: int) -> None:
    """Insert fake completed trades into the DB."""
    conn = get_connection(db_path)
    now_ms = int(time.time() * 1000)
    trade_num = 0
    for _ in range(wins):
        conn.execute(
            "INSERT INTO trades (strategy_id, symbol, side, outcome, entry_at, exit_at) "
            "VALUES (?, 'BTC/USDT', 'buy', 'win', ?, ?)",
            (strategy_id, now_ms - (trade_num + 1) * 60_000, now_ms - trade_num * 60_000),
        )
        trade_num += 1
    for _ in range(losses):
        conn.execute(
            "INSERT INTO trades (strategy_id, symbol, side, outcome, entry_at, exit_at) "
            "VALUES (?, 'BTC/USDT', 'buy', 'loss', ?, ?)",
            (strategy_id, now_ms - (trade_num + 1) * 60_000, now_ms - trade_num * 60_000),
        )
        trade_num += 1
    conn.commit()
    conn.close()


# A simple strategy spec used across tests.
_TEST_SPEC = {
    "name": "Test_RSI_Strategy",
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
            {"type": "stop_loss_pct", "value": 3.0},
            {"type": "take_profit_pct", "value": 9.0},
        ],
    },
}


# ── Group A — Signal Detection ──────────────────────────────────────────────


def _make_synthetic_candles(n: int, dip_at: int = None) -> pd.DataFrame:
    """
    Generate synthetic 1h candles. If dip_at is given, insert a sharp
    drop at that bar so RSI falls below 30.
    """
    np.random.seed(42)
    base = 50000.0
    close = np.full(n, base)

    # Gentle uptrend with noise.
    for i in range(1, n):
        close[i] = close[i - 1] + np.random.normal(10, 50)

    # Sharp drop to trigger RSI < 30.
    if dip_at is not None and dip_at < n:
        for i in range(max(dip_at - 5, 0), min(dip_at + 1, n)):
            close[i] = close[max(dip_at - 6, 0)] - (dip_at - i + 1) * 300

    high = close + np.random.uniform(50, 200, n)
    low = close - np.random.uniform(50, 200, n)
    opn = (close + np.random.normal(0, 30, n))

    now_ms = int(time.time() * 1000)
    timestamps = [now_ms - (n - i) * 3_600_000 for i in range(n)]

    return pd.DataFrame({
        "timestamp": timestamps,
        "open": opn,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.random.uniform(100, 1000, n),
    })


class TestSignalDetectionGroupA:
    """Signal detection using build_signals on synthetic data."""

    def test_signal_fires_on_synthetic_entry(self):
        """Synthetic candles with RSI dip → signal fires after dip."""
        candles = _make_synthetic_candles(100, dip_at=50)
        signals = build_signals(candles, _TEST_SPEC)

        # There should be at least one entry signal (value 1) after bar 50.
        entry_signals = signals[signals == 1]
        assert len(entry_signals) > 0, "Expected at least one entry signal after RSI dip"

        # The first signal should be near the dip (after warm-up shift).
        first_signal_idx = entry_signals.index[0]
        assert first_signal_idx >= 15, "Signal should not fire during warm-up"

    def test_no_signal_when_condition_never_met(self):
        """100 flat candles → no entry signal fires."""
        # Flat candles with tiny noise — RSI stays ~50.
        n = 100
        close = np.full(n, 50000.0)
        high = close + 10
        low = close - 10
        opn = close.copy()
        now_ms = int(time.time() * 1000)
        timestamps = [now_ms - (n - i) * 3_600_000 for i in range(n)]

        df = pd.DataFrame({
            "timestamp": timestamps,
            "open": opn,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 500.0),
        })

        signals = build_signals(df, _TEST_SPEC)
        assert (signals == 1).sum() == 0, "No entry signal should fire on flat candles"


# ── Group B — Risk Agent ────────────────────────────────────────────────────


class TestRiskAgentGroupB:
    """Pure arithmetic checks — no mocks needed."""

    def test_adjusts_oversized_position(self):
        """6% of balance → adjusted to 5%, approved=True."""
        agent = RiskAgent(max_position_pct=0.05)
        result = agent.review(
            proposed_size_usdt=600.0,
            balance_usdt=10_000.0,
            open_positions=0,
            daily_pnl_pct=0.0,
        )
        assert result["approved"] is True
        assert result["adjusted_size"] == pytest.approx(500.0)
        assert "capped" in result["reason"].lower() or "500" in result["reason"]

    def test_rejects_on_daily_loss(self):
        """daily_pnl_pct=-4% with 3% limit → rejected."""
        agent = RiskAgent(max_daily_loss=0.03)
        result = agent.review(
            proposed_size_usdt=100.0,
            balance_usdt=10_000.0,
            open_positions=0,
            daily_pnl_pct=-0.04,
        )
        assert result["approved"] is False
        assert result["adjusted_size"] == 0.0
        assert "daily loss" in result["reason"].lower()

    def test_rejects_on_max_concurrent(self):
        """3 open positions with limit=3 → rejected."""
        agent = RiskAgent(max_concurrent=3)
        result = agent.review(
            proposed_size_usdt=100.0,
            balance_usdt=10_000.0,
            open_positions=3,
            daily_pnl_pct=0.0,
        )
        assert result["approved"] is False
        assert result["adjusted_size"] == 0.0
        assert "concurrent" in result["reason"].lower()

    def test_stoploss_guard_triggers_on_consecutive_losses(self):
        """3 consecutive losses → rejected with cooldown."""
        agent = RiskAgent(stoploss_guard_consecutive=3, stoploss_guard_cooldown_min=60)
        result = agent.review(
            proposed_size_usdt=100.0,
            balance_usdt=10_000.0,
            open_positions=0,
            daily_pnl_pct=0.0,
            recent_outcomes=["loss", "loss", "loss", "win"],
        )
        assert result["approved"] is False
        assert "stoplossguard" in result["reason"].lower()

    def test_stoploss_guard_allows_when_streak_broken(self):
        """2 losses then a win → not triggered (streak broken)."""
        agent = RiskAgent(stoploss_guard_consecutive=3)
        result = agent.review(
            proposed_size_usdt=100.0,
            balance_usdt=10_000.0,
            open_positions=0,
            daily_pnl_pct=0.0,
            recent_outcomes=["loss", "loss", "win", "loss"],
        )
        assert result["approved"] is True

    def test_approves_valid_trade(self):
        """Normal trade within all limits → approved at requested size."""
        agent = RiskAgent()
        result = agent.review(
            proposed_size_usdt=300.0,
            balance_usdt=10_000.0,
            open_positions=1,
            daily_pnl_pct=-0.01,
        )
        assert result["approved"] is True
        assert result["adjusted_size"] == pytest.approx(300.0)

    def test_rejects_zero_balance(self):
        """Zero balance → rejected."""
        agent = RiskAgent()
        result = agent.review(
            proposed_size_usdt=100.0,
            balance_usdt=0.0,
            open_positions=0,
            daily_pnl_pct=0.0,
        )
        assert result["approved"] is False


# ── Group C — Debate CP2 ────────────────────────────────────────────────────


class TestDebateCP2GroupC:
    """Mock analyst + execution to test CP2 gating."""

    def _make_strategy(self, db_path):
        _seed_strategy(db_path, strategy_id=1)
        return {
            "id": 1,
            "name": "Test_Strategy",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "spec": _TEST_SPEC,
            "calibration": {
                "degradation_threshold": 0.45,
                "position_sizing": {
                    "method": "atr", "atr_period": 14,
                    "atr_multiplier": 1.5, "risk_per_trade_pct": 0.01,
                },
            },
            "viable": True,
        }

    @patch("src.loop2.place_trade")
    @patch("src.loop2.evaluate_brief")
    @patch("src.loop2.build_signals")
    @patch("src.loop2._get_balance", return_value=10_000.0)
    @patch("src.loop2.reconcile_open_trades", return_value=0)
    def test_cp2_reject_blocks_execution(
        self, mock_recon, mock_bal, mock_signals, mock_brief, mock_place,
    ):
        """evaluate_brief returns confirm=False → place_trade NOT called."""
        db_path = _make_db()
        strategy = self._make_strategy(db_path)

        # Signal fires on every iteration.
        mock_signals.return_value = pd.Series([0, 0, 1])

        # Analyst rejects.
        mock_brief.return_value = {"confirm": False, "note": "market too volatile"}

        mock_feed = MagicMock()
        mock_feed.get_latest_candles.return_value = _make_synthetic_candles(50)

        mock_exchange = MagicMock()

        with patch("src.loop2.CCXTFeed", return_value=mock_feed), \
             patch("src.loop2.DegradationMonitor") as mock_mon_cls, \
             patch("src.loop2._get_open_position_count", return_value=0), \
             patch("src.loop2._get_daily_pnl_pct", return_value=0.0), \
             patch("src.loop2._get_recent_outcomes", return_value=[]), \
             patch("src.loop2._sleep_until_next_candle"):
            mock_mon = MagicMock()
            mock_mon.flag.is_set.return_value = False
            mock_mon_cls.return_value = mock_mon

            run_loop2(
                strategy=strategy,
                db_path=db_path,
                exchange=mock_exchange,
                client=MagicMock(),
                max_iterations=1,
            )

        mock_place.assert_not_called()

    @patch("src.loop2.place_trade")
    @patch("src.loop2.evaluate_brief")
    @patch("src.loop2.build_signals")
    @patch("src.loop2._get_balance", return_value=10_000.0)
    @patch("src.loop2.reconcile_open_trades", return_value=0)
    def test_cp2_confirm_allows_execution(
        self, mock_recon, mock_bal, mock_signals, mock_brief, mock_place,
    ):
        """evaluate_brief returns confirm=True → place_trade IS called."""
        db_path = _make_db()
        strategy = self._make_strategy(db_path)

        mock_signals.return_value = pd.Series([0, 0, 1])
        mock_brief.return_value = {"confirm": True, "note": "looks good"}
        mock_place.return_value = {"trade_id": 1, "order_id": "x", "entry_price": 50000, "outcome": "open"}

        mock_feed = MagicMock()
        mock_feed.get_latest_candles.return_value = _make_synthetic_candles(50)

        mock_exchange = MagicMock()

        with patch("src.loop2.CCXTFeed", return_value=mock_feed), \
             patch("src.loop2.DegradationMonitor") as mock_mon_cls, \
             patch("src.loop2._get_open_position_count", return_value=0), \
             patch("src.loop2._get_daily_pnl_pct", return_value=0.0), \
             patch("src.loop2._get_recent_outcomes", return_value=[]), \
             patch("src.loop2._sleep_until_next_candle"):
            mock_mon = MagicMock()
            mock_mon.flag.is_set.return_value = False
            mock_mon_cls.return_value = mock_mon

            run_loop2(
                strategy=strategy,
                db_path=db_path,
                exchange=mock_exchange,
                client=MagicMock(),
                max_iterations=1,
            )

        mock_place.assert_called_once()


# ── Group D — Execution Agent ───────────────────────────────────────────────


class TestExecutionAgentGroupD:
    """Mock CCXT exchange for order placement tests."""

    def test_create_order_correct_params(self):
        """create_order called with base currency amount, correct symbol/side."""
        db_path = _make_db()
        _seed_strategy(db_path, strategy_id=1)

        mock_exchange = MagicMock()
        mock_exchange.fetch_ticker.return_value = {"last": 50_000.0}
        mock_exchange.create_order.return_value = {
            "id": "order_123",
            "average": 50_000.0,
            "filled": 0.002,
            "status": "closed",
        }

        stop = threading.Event()
        stop.set()

        place_trade(
            symbol="BTC/USDT",
            side="buy",
            amount_usdt=100.0,
            stop_loss_pct=3.0,
            take_profit_pct=9.0,
            exchange=mock_exchange,
            db_path=db_path,
            strategy_id=1,
            stop_event=stop,
        )

        # First create_order call is the entry market order.
        entry_call = mock_exchange.create_order.call_args_list[0]
        assert entry_call[0][0] == "BTC/USDT"    # symbol
        assert entry_call[0][1] == "market"       # type
        assert entry_call[0][2] == "buy"          # side
        # Amount should be in base currency: 100 / 50000 = 0.002
        assert abs(entry_call[0][3] - 0.002) < 1e-6

    def test_trade_logged_to_db_before_exchange(self):
        """trades table has row with outcome='open' BEFORE exchange order fills."""
        db_path = _make_db()
        _seed_strategy(db_path, strategy_id=1)

        trade_exists_before_order = {}

        def mock_create_order(*args, **kwargs):
            conn = get_connection(db_path)
            row = conn.execute(
                "SELECT outcome FROM trades WHERE strategy_id = 1 ORDER BY id DESC LIMIT 1"
            ).fetchone()
            conn.close()
            trade_exists_before_order["found"] = row is not None
            trade_exists_before_order["outcome"] = row["outcome"] if row else None
            return {
                "id": "order_456",
                "average": 50_000.0,
                "filled": 0.002,
                "status": "closed",
            }

        mock_exchange = MagicMock()
        mock_exchange.fetch_ticker.return_value = {"last": 50_000.0}
        mock_exchange.create_order.side_effect = mock_create_order

        stop = threading.Event()
        stop.set()

        place_trade(
            symbol="BTC/USDT",
            side="buy",
            amount_usdt=100.0,
            stop_loss_pct=3.0,
            take_profit_pct=9.0,
            exchange=mock_exchange,
            db_path=db_path,
            strategy_id=1,
            stop_event=stop,
        )

        assert trade_exists_before_order["found"] is True
        assert trade_exists_before_order["outcome"] == "open"


# ── Group E — Degradation Monitor ───────────────────────────────────────────


class TestDegradationMonitorGroupE:
    """Uses real SQLite DB with inserted trades."""

    def test_degradation_triggers_below_threshold(self):
        """20 trades, 40% win rate, threshold=0.45 → degraded."""
        db_path = _make_db()
        _seed_strategy(db_path, strategy_id=1)
        _seed_trades(db_path, strategy_id=1, wins=8, losses=12)

        monitor = DegradationMonitor(
            strategy_id=1, threshold=0.45, window=20, db_path=db_path,
        )
        assert monitor.check_once() is True

    def test_no_degradation_above_threshold(self):
        """20 trades, 50% win rate, threshold=0.45 → not degraded."""
        db_path = _make_db()
        _seed_strategy(db_path, strategy_id=1)
        _seed_trades(db_path, strategy_id=1, wins=10, losses=10)

        monitor = DegradationMonitor(
            strategy_id=1, threshold=0.45, window=20, db_path=db_path,
        )
        assert monitor.check_once() is False


# ── Group F — Full Loop 2 Integration ───────────────────────────────────────


class TestLoop2IntegrationGroupF:
    """Wire all mocks together for integration tests."""

    def _make_strategy(self, db_path):
        _seed_strategy(db_path, strategy_id=1)
        return {
            "id": 1,
            "name": "Test_Strategy",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "spec": _TEST_SPEC,
            "calibration": {
                "degradation_threshold": 0.45,
                "position_sizing": {
                    "method": "atr", "atr_period": 14,
                    "atr_multiplier": 1.5, "risk_per_trade_pct": 0.01,
                },
            },
            "viable": True,
        }

    @patch("src.loop2.place_trade")
    @patch("src.loop2.evaluate_brief")
    @patch("src.loop2.build_signals")
    @patch("src.loop2._get_balance", return_value=10_000.0)
    @patch("src.loop2.reconcile_open_trades", return_value=0)
    def test_correct_execution_order(
        self, mock_recon, mock_bal, mock_signals, mock_brief, mock_place,
    ):
        """Signal → risk → CP2 → execute (call order tracking)."""
        db_path = _make_db()
        strategy = self._make_strategy(db_path)

        call_order = []

        def track_signals(*args, **kwargs):
            call_order.append("signal")
            return pd.Series([0, 0, 1])

        def track_brief(*args, **kwargs):
            call_order.append("cp2")
            return {"confirm": True, "note": "ok"}

        def track_place(*args, **kwargs):
            call_order.append("execute")
            return {"trade_id": 1, "order_id": "x", "entry_price": 50000, "outcome": "open"}

        mock_signals.side_effect = track_signals
        mock_brief.side_effect = track_brief
        mock_place.side_effect = track_place

        mock_feed = MagicMock()
        mock_feed.get_latest_candles.return_value = _make_synthetic_candles(50)

        with patch("src.loop2.CCXTFeed", return_value=mock_feed), \
             patch("src.loop2.DegradationMonitor") as mock_mon_cls, \
             patch("src.loop2._get_open_position_count", return_value=0), \
             patch("src.loop2._get_daily_pnl_pct", return_value=0.0), \
             patch("src.loop2._get_recent_outcomes", return_value=[]), \
             patch("src.loop2._sleep_until_next_candle"):
            mock_mon = MagicMock()
            mock_mon.flag.is_set.return_value = False
            mock_mon_cls.return_value = mock_mon

            run_loop2(
                strategy=strategy,
                db_path=db_path,
                exchange=MagicMock(),
                client=MagicMock(),
                max_iterations=1,
            )

        assert call_order == ["signal", "cp2", "execute"]

    @patch("src.loop2.reflect")
    @patch("src.loop2.reconcile_open_trades", return_value=0)
    def test_raises_on_degradation(self, mock_recon, mock_reflect):
        """Pre-set monitor flag → StrategyDegradedException raised."""
        db_path = _make_db()
        strategy = self._make_strategy(db_path)

        mock_reflect.return_value = "regime shift detected"

        mock_feed = MagicMock()
        mock_feed.get_latest_candles.return_value = _make_synthetic_candles(50)

        with patch("src.loop2.CCXTFeed", return_value=mock_feed), \
             patch("src.loop2.DegradationMonitor") as mock_mon_cls, \
             patch("src.loop2._sleep_until_next_candle"):
            mock_mon = MagicMock()
            mock_mon.flag.is_set.return_value = True  # Degradation detected!
            mock_mon_cls.return_value = mock_mon

            with pytest.raises(StrategyDegradedException):
                run_loop2(
                    strategy=strategy,
                    db_path=db_path,
                    exchange=MagicMock(),
                    client=MagicMock(),
                    max_iterations=1,
                )

    @patch("src.loop2.reflect")
    @patch("src.loop2.reconcile_open_trades", return_value=0)
    def test_calls_reflect_on_degradation(self, mock_recon, mock_reflect):
        """analyst_agent.reflect() called (not evaluate()) on degradation."""
        db_path = _make_db()
        strategy = self._make_strategy(db_path)

        mock_reflect.return_value = "overfitting detected"

        mock_feed = MagicMock()
        mock_feed.get_latest_candles.return_value = _make_synthetic_candles(50)

        with patch("src.loop2.CCXTFeed", return_value=mock_feed), \
             patch("src.loop2.DegradationMonitor") as mock_mon_cls, \
             patch("src.loop2._sleep_until_next_candle"):
            mock_mon = MagicMock()
            mock_mon.flag.is_set.return_value = True
            mock_mon_cls.return_value = mock_mon

            with pytest.raises(StrategyDegradedException):
                run_loop2(
                    strategy=strategy,
                    db_path=db_path,
                    exchange=MagicMock(),
                    client=MagicMock(),
                    max_iterations=1,
                )

        mock_reflect.assert_called_once()
        # Verify reflect was called with the strategy dict (not evaluate).
        call_args = mock_reflect.call_args
        assert call_args[0][0] == strategy  # first positional arg is strategy


# ── Group G — Probation Tier ────────────────────────────────────────────────


class TestProbationMonitor:
    """DegradationMonitor probation=True should tighten threshold and halve stale_hours."""

    def test_probation_bumps_threshold(self):
        """
        With probation=True, threshold is bumped by PROBATION_THRESHOLD_BUMP
        (subject to DEGRADATION_THRESHOLD_FLOOR).
        """
        from config.settings import PROBATION_THRESHOLD_BUMP

        monitor_normal = DegradationMonitor(
            strategy_id=1, threshold=0.45, window=20, db_path=":memory:",
        )
        monitor_prob = DegradationMonitor(
            strategy_id=1, threshold=0.45, window=20, db_path=":memory:",
            probation=True,
        )
        assert monitor_prob.threshold == monitor_normal.threshold + PROBATION_THRESHOLD_BUMP

    def test_probation_halves_stale_hours(self):
        """Stale timeout is halved under probation."""
        monitor_prob = DegradationMonitor(
            strategy_id=1, threshold=0.45, window=20, db_path=":memory:",
            stale_hours=48, probation=True,
        )
        assert monitor_prob.stale_hours == 24

    def test_probation_triggers_earlier_on_borderline_win_rate(self):
        """
        With 20 trades at 48% win rate:
        - normal threshold 0.45 → NOT degraded (0.48 ≥ 0.45)
        - probation threshold 0.45 + PROBATION_THRESHOLD_BUMP → degraded
        """
        db_path = _make_db()
        _seed_strategy(db_path, strategy_id=1)
        _seed_trades(db_path, strategy_id=1, wins=12, losses=13)  # 12/25 = 0.48

        monitor_normal = DegradationMonitor(
            strategy_id=1, threshold=0.45, window=25, db_path=db_path,
        )
        monitor_prob = DegradationMonitor(
            strategy_id=1, threshold=0.45, window=25, db_path=db_path,
            probation=True,
        )
        assert monitor_normal.check_once() is False
        assert monitor_prob.check_once() is True


class TestProbationCounters:
    """execution_agent._update_probation_counters: auto-promote / auto-demote."""

    def _seed_probation_strategy(self, db_path, wins=0, losses=0, status="probation"):
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO strategies (id, name, spec, status, probation_wins, "
            "probation_losses, created_at) VALUES (1, 'p', '{}', ?, ?, ?, ?)",
            (status, wins, losses, int(time.time() * 1000)),
        )
        conn.commit()
        conn.close()

    def _fetch(self, db_path):
        conn = get_connection(db_path)
        row = conn.execute(
            "SELECT status, probation_wins, probation_losses FROM strategies WHERE id=1"
        ).fetchone()
        conn.close()
        return dict(row)

    def test_win_increments_counter(self):
        from src.agents.execution_agent import _update_probation_counters

        db_path = _make_db()
        self._seed_probation_strategy(db_path, wins=3, losses=1)
        conn = get_connection(db_path)
        _update_probation_counters(conn, 1, "win")
        conn.commit()
        conn.close()

        row = self._fetch(db_path)
        assert row["status"] == "probation"
        assert row["probation_wins"] == 4
        assert row["probation_losses"] == 1

    def test_loss_increments_counter(self):
        from src.agents.execution_agent import _update_probation_counters

        db_path = _make_db()
        self._seed_probation_strategy(db_path, wins=2, losses=2)
        conn = get_connection(db_path)
        _update_probation_counters(conn, 1, "loss")
        conn.commit()
        conn.close()

        row = self._fetch(db_path)
        assert row["probation_wins"] == 2
        assert row["probation_losses"] == 3
        assert row["status"] == "probation"

    def test_auto_promote_after_threshold_wins(self):
        from config.settings import PROBATION_PROMOTE_WINS
        from src.agents.execution_agent import _update_probation_counters

        db_path = _make_db()
        self._seed_probation_strategy(db_path, wins=PROBATION_PROMOTE_WINS - 1, losses=0)
        conn = get_connection(db_path)
        _update_probation_counters(conn, 1, "win")
        conn.commit()
        conn.close()

        row = self._fetch(db_path)
        assert row["status"] == "active"
        assert row["probation_wins"] == 0
        assert row["probation_losses"] == 0

    def test_auto_demote_after_threshold_losses(self):
        from config.settings import PROBATION_DEMOTE_LOSSES
        from src.agents.execution_agent import _update_probation_counters

        db_path = _make_db()
        self._seed_probation_strategy(db_path, wins=0, losses=PROBATION_DEMOTE_LOSSES - 1)
        conn = get_connection(db_path)
        _update_probation_counters(conn, 1, "loss")
        conn.commit()
        conn.close()

        row = self._fetch(db_path)
        assert row["status"] == "degraded"
        assert row["probation_losses"] == PROBATION_DEMOTE_LOSSES

    def test_noop_when_not_on_probation(self):
        """Active strategies don't get counters incremented on trade close."""
        from src.agents.execution_agent import _update_probation_counters

        db_path = _make_db()
        self._seed_probation_strategy(db_path, wins=0, losses=0, status="active")
        conn = get_connection(db_path)
        _update_probation_counters(conn, 1, "win")
        _update_probation_counters(conn, 1, "loss")
        conn.commit()
        conn.close()

        row = self._fetch(db_path)
        assert row["status"] == "active"
        assert row["probation_wins"] == 0
        assert row["probation_losses"] == 0


class TestLoop2ProbationSizing:
    """run_loop2 should halve position size when strategy status is 'probation'."""

    def _probation_strategy(self, db_path):
        conn = get_connection(db_path)
        conn.execute(
            "INSERT INTO strategies (id, name, spec, status, created_at) "
            "VALUES (1, 'p', '{}', 'probation', ?)",
            (int(time.time() * 1000),),
        )
        conn.commit()
        conn.close()
        return {
            "id": 1,
            "name": "p",
            "symbol": "BTC/USDT",
            "timeframe": "1h",
            "spec": _TEST_SPEC,
            "calibration": {
                "degradation_threshold": 0.45,
                "position_sizing": {
                    "method": "atr", "atr_period": 14,
                    "atr_multiplier": 1.5, "risk_per_trade_pct": 0.01,
                },
            },
            "viable": True,
            "status": "probation",
            "verdict": "probation",
        }

    @patch("src.loop2.place_trade")
    @patch("src.loop2.evaluate_brief")
    @patch("src.loop2.build_signals")
    @patch("src.loop2._get_balance", return_value=10_000.0)
    @patch("src.loop2.reconcile_open_trades", return_value=0)
    @patch("src.loop2.compute_position_size", return_value=200.0)
    def test_probation_halves_position_size(
        self, mock_size, mock_recon, mock_bal, mock_signals, mock_brief, mock_place,
    ):
        from config.settings import PROBATION_SIZE_MULTIPLIER

        db_path = _make_db()
        strategy = self._probation_strategy(db_path)

        mock_signals.return_value = pd.Series([1] * 30)
        mock_brief.return_value = {"confirm": True, "note": ""}
        mock_place.return_value = {"trade_id": 1, "outcome": "win"}

        with patch("src.loop2.CCXTFeed") as mock_feed_cls, \
             patch("src.loop2.DegradationMonitor") as mock_mon_cls, \
             patch("src.loop2._sleep_until_next_candle"), \
             patch("src.loop2._drop_incomplete_candle", side_effect=lambda df, tf: df):

            mock_mon_cls.return_value.flag.is_set.return_value = False
            mock_feed = mock_feed_cls.return_value
            mock_feed.get_latest_candles.return_value = _make_synthetic_candles(50)

            from src.loop2 import run_loop2
            run_loop2(
                strategy, db_path, exchange=MagicMock(),
                client=MagicMock(), max_iterations=1,
            )

        # place_trade must be called with reduced amount_usdt
        assert mock_place.called
        called_kwargs = mock_place.call_args.kwargs
        expected = 200.0 * PROBATION_SIZE_MULTIPLIER
        # RiskAgent may further adjust; ensure it is at or below the multiplied size.
        assert called_kwargs["amount_usdt"] <= expected + 1e-6

    @patch("src.loop2.evaluate_brief")
    @patch("src.loop2.build_signals")
    @patch("src.loop2._get_balance", return_value=10_000.0)
    @patch("src.loop2.reconcile_open_trades", return_value=0)
    @patch("src.loop2.reflect", return_value="demoted diagnosis")
    def test_status_flip_to_degraded_raises(
        self, mock_reflect, mock_recon, mock_bal, mock_signals, mock_brief,
    ):
        """
        If a concurrent process flips strategies.status = 'degraded' (e.g. auto-demote
        from probation losses), the next loop iteration must raise
        StrategyDegradedException regardless of monitor.flag state.
        """
        db_path = _make_db()
        strategy = self._probation_strategy(db_path)

        # Flip to degraded before running loop.
        conn = get_connection(db_path)
        conn.execute("UPDATE strategies SET status = 'degraded' WHERE id = 1")
        conn.commit()
        conn.close()

        mock_signals.return_value = pd.Series([0] * 30)
        mock_brief.return_value = {"confirm": False, "note": ""}

        with patch("src.loop2.CCXTFeed") as mock_feed_cls, \
             patch("src.loop2.DegradationMonitor") as mock_mon_cls, \
             patch("src.loop2._sleep_until_next_candle"), \
             patch("src.loop2._drop_incomplete_candle", side_effect=lambda df, tf: df):

            mock_mon_cls.return_value.flag.is_set.return_value = False
            mock_feed = mock_feed_cls.return_value
            mock_feed.get_latest_candles.return_value = _make_synthetic_candles(50)

            from src.loop2 import run_loop2, StrategyDegradedException
            import pytest as _pt
            with _pt.raises(StrategyDegradedException):
                run_loop2(
                    strategy, db_path, exchange=MagicMock(),
                    client=MagicMock(), max_iterations=3,
                )
