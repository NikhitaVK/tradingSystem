"""
Smoke test: Run Loop 2 against a fast-firing 1m test strategy using PaperExchange.

Creates a new active strategy in the DB and runs Loop 2 until a trade opens and closes.
"""
import json
import logging
import os
import sys
import threading
import time

os.environ.setdefault("EXECUTION_MODE", "paper")
os.environ.setdefault("OCO_MAX_WAIT_SECONDS", "180")
os.environ.setdefault("OCO_POLL_INTERVAL_SECONDS", "15")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import DB_PATH
from src.data.schema import init_db, get_connection
from src.loop2 import run_loop2, StrategyDegradedException


def _create_fast_strategy(db_path):
    """Insert a 1m RSI-based strategy that fires frequently."""
    spec = {
        "name": "SmokeTest_RSI_1m_Long",
        "symbol": "ETH/USDT",
        "timeframe": "1m",
        "mechanism": "mean_reversion",
        "direction": "long",
        "indicators": [{"type": "RSI", "period": 14}],
        "entry": {
            "logic": "AND",
            "conditions": [
                {"indicator": "RSI_14", "operator": "<", "value": 60},
            ],
        },
        "exit": {
            "logic": "OR",
            "conditions": [
                {"type": "stop_loss_pct", "value": 0.3},
                {"type": "take_profit_pct", "value": 0.6},
            ],
        },
    }
    calibration = {
        "degradation_threshold": 0.30,
        "position_sizing": {
            "method": "atr",
            "atr_period": 14,
            "atr_multiplier": 1.5,
            "risk_per_trade_pct": 0.01,
        },
    }
    perf = {"win_rate_mean": 0.5, "sharpe_mean": 1.0, "total_trades": 100}

    conn = get_connection(db_path)
    cur = conn.execute(
        "INSERT INTO strategies (name, spec, performance, degradation_threshold, "
        "position_sizing, status, created_at) VALUES (?, ?, ?, ?, ?, 'active', ?)",
        (
            spec["name"], json.dumps(spec), json.dumps(perf),
            calibration["degradation_threshold"],
            json.dumps(calibration["position_sizing"]),
            int(time.time() * 1000),
        ),
    )
    sid = cur.lastrowid
    conn.commit()
    conn.close()

    return {
        "id": sid,
        "name": spec["name"],
        "symbol": spec["symbol"],
        "timeframe": spec["timeframe"],
        "spec": spec,
        "performance": perf,
        "calibration": calibration,
        "viable": True,
    }


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    init_db(DB_PATH)
    strategy = _create_fast_strategy(DB_PATH)
    print(f"Created strategy id={strategy['id']} {strategy['name']}")

    stop_event = threading.Event()
    try:
        run_loop2(strategy, DB_PATH, stop_event=stop_event, max_iterations=200)
    except StrategyDegradedException as e:
        print(f"Strategy degraded: {e}")
    finally:
        conn = get_connection(DB_PATH)
        rows = conn.execute(
            "SELECT id, side, entry_price, exit_price, pnl_pct, outcome "
            "FROM trades WHERE strategy_id = ? ORDER BY id",
            (strategy["id"],),
        ).fetchall()
        conn.close()
        print(f"\n=== {len(rows)} trades for strategy {strategy['id']} ===")
        for r in rows:
            entry = r["entry_price"] or 0
            exit_ = r["exit_price"] or 0
            pnl = r["pnl_pct"] or 0
            print(f"  id={r['id']} {r['side']} entry={entry:.2f} exit={exit_:.2f} pnl={pnl:.4f} outcome={r['outcome']}")


if __name__ == "__main__":
    main()
