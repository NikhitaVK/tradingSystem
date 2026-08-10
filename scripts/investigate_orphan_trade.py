"""
investigate_orphan_trade.py — Read-only diagnostic for a stuck `outcome='open'` trade.

Queries the exchange for the actual current state of the position and order
history referenced by a given trade row. Prints a structured report to stdout
and (optionally) appends the same report into the matching issue file's
"Status notes" section.

Does NOT modify the DB. Does NOT close any positions. Does NOT cancel any
orders. The human reviewing the report decides what to do next.

Usage:
    python -m scripts.investigate_orphan_trade --trade-id 1
    python -m scripts.investigate_orphan_trade --trade-id 1 --append-to-issue
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sqlite3
import sys
from pathlib import Path

from config.settings import DB_PATH
from src.exchange.factory import build_exchange

logger = logging.getLogger(__name__)

ISSUE_FILE = (
    Path(__file__).resolve().parent.parent
    / "claude_docs" / "issues"
    / "2026-06-07-stuck-trade-no-server-side-stops.md"
)


def _fmt_ts(ms: int | None) -> str:
    if not ms:
        return "—"
    return dt.datetime.utcfromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M UTC")


def _load_trade_row(db_path: str, trade_id: int) -> dict | None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _safe_call(label, fn, *args, **kwargs):
    """Run an exchange method, catching missing-method and runtime errors."""
    try:
        result = fn(*args, **kwargs)
        return {"ok": True, "data": result}
    except AttributeError:
        return {"ok": False, "data": None, "error": f"{label} not implemented on this exchange"}
    except Exception as e:
        return {"ok": False, "data": None, "error": f"{type(e).__name__}: {e}"}


def investigate(trade_id: int) -> str:
    """
    Build the report for one trade row.
    Returns the markdown report string. Pure read; no side effects.
    """
    trade = _load_trade_row(DB_PATH, trade_id)
    if not trade:
        return f"No trade row with id={trade_id} in {DB_PATH}.\n"

    symbol = trade["symbol"]
    entry_at = trade["entry_at"]
    order_id = trade.get("order_id")

    exchange = build_exchange(DB_PATH)

    positions = _safe_call("fetch_positions", exchange.fetch_positions, [symbol])
    open_orders = _safe_call("fetch_open_orders", exchange.fetch_open_orders, symbol)
    my_trades = _safe_call(
        "fetch_my_trades",
        getattr(exchange, "fetch_my_trades", lambda *a, **k: (_ for _ in ()).throw(AttributeError())),
        symbol,
        entry_at,
    )
    order_status = (
        _safe_call("fetch_order", exchange.fetch_order, order_id, symbol)
        if order_id
        else {"ok": False, "data": None, "error": "no order_id stored on trade row"}
    )

    # Build human-readable matching position (filter for the one on our symbol).
    matching_position = None
    if positions["ok"]:
        for p in positions["data"] or []:
            if p.get("symbol") == symbol and float(p.get("contracts") or p.get("size") or 0) != 0:
                matching_position = p
                break

    report_lines = [
        f"### Investigation — trade id={trade_id} — {dt.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "**DB row**",
        "",
        f"- symbol: `{symbol}`",
        f"- side: `{trade['side']}`",
        f"- entry_at: {_fmt_ts(entry_at)}",
        f"- entry_price: {trade.get('entry_price')}",
        f"- amount_usdt: {trade.get('amount_usdt')}",
        f"- outcome (DB): `{trade.get('outcome')}`",
        f"- order_id: `{order_id or '∅'}`",
        "",
        f"**Exchange mode**: `{type(exchange).__name__}`",
        "",
        "**Position state**",
        "",
    ]
    if not positions["ok"]:
        report_lines.append(f"- ⚠ fetch_positions failed: {positions['error']}")
    elif matching_position:
        size = matching_position.get("contracts") or matching_position.get("size")
        report_lines.append(f"- ✓ Position OPEN on exchange: size={size}, entry={matching_position.get('entryPrice')}, unrealized PnL={matching_position.get('unrealizedPnl')}")
    else:
        report_lines.append("- ✗ No open position on exchange for this symbol")

    report_lines += ["", "**Resting orders on this symbol**", ""]
    if not open_orders["ok"]:
        report_lines.append(f"- ⚠ fetch_open_orders failed: {open_orders['error']}")
    elif not open_orders["data"]:
        report_lines.append("- (none — no resting SL/TP or other orders)")
    else:
        for o in open_orders["data"]:
            report_lines.append(
                f"- {o.get('id')}: {o.get('type')} {o.get('side')} @ {o.get('price')} "
                f"({o.get('status')})"
            )

    report_lines += ["", "**Original entry order status**", ""]
    if not order_status["ok"]:
        report_lines.append(f"- {order_status['error']}")
    else:
        d = order_status["data"]
        report_lines.append(f"- id={d.get('id')} status={d.get('status')} filled={d.get('filled')} avg={d.get('average')}")

    report_lines += ["", "**Fills since entry**", ""]
    if not my_trades["ok"]:
        report_lines.append(f"- fetch_my_trades unavailable: {my_trades['error']}")
    elif not my_trades["data"]:
        report_lines.append("- (no fills returned for this symbol since entry_at)")
    else:
        for t in my_trades["data"]:
            report_lines.append(
                f"- {_fmt_ts(t.get('timestamp'))}  {t.get('side')} {t.get('amount')} @ {t.get('price')}"
            )

    report_lines += [
        "",
        "**Recommendation (human decides)**",
        "",
        "- If position is OPEN and no SL/TP resting → place a stop manually via Binance UI or `exchange.create_order(...)` while you decide on the design fix.",
        "- If position is CLOSED but DB still says open → backfill `exit_price`, `exit_at`, `outcome`, `pnl_pct` from the fills above (run a targeted SQL UPDATE).",
        "- If no position and no fill → mark `outcome='never_filled'` and investigate why the original order never resulted in a position.",
        "",
        "---",
        "",
    ]
    return "\n".join(report_lines)


def _append_to_issue(report: str) -> None:
    if not ISSUE_FILE.exists():
        logger.warning("Issue file not found at %s — printed report only.", ISSUE_FILE)
        return
    text = ISSUE_FILE.read_text()
    marker = "## Status notes"
    if marker not in text:
        logger.warning("Marker '## Status notes' not found in issue file — printed report only.")
        return
    # Insert report immediately after the marker line.
    before, after = text.split(marker, 1)
    new_text = before + marker + "\n\n" + report + after.lstrip("\n")
    ISSUE_FILE.write_text(new_text)
    print(f"Appended report into {ISSUE_FILE.relative_to(ISSUE_FILE.parent.parent.parent)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-id", type=int, required=True)
    parser.add_argument(
        "--append-to-issue",
        action="store_true",
        help="Also append the report to the matching issue file's Status notes section.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    report = investigate(args.trade_id)
    print(report)
    if args.append_to_issue:
        _append_to_issue(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
