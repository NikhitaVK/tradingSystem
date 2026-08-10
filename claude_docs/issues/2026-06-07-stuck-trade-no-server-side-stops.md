---
tags: [issue, design, open]
related: ["[[_issues]]", "[[execution]]", "[[2026-04-18-usdm-futures-testnet-shorts]]"]
---

# Issue: Trades have no server-side stop and get orphaned when the process dies

**Discovered**: 2026-06-07 (root cause traced to trade id=1 placed 2026-04-18)
**Status**: open — fix designed, implementation pending
**Severity**: high (an unprotected live position is a real-money risk once paper mode is left)
**Affects**: [[execution]]

## Problem

Trade `id=1` in `trading_system.db` is sitting at `outcome='open'` since 2026-04-18 03:09 UTC — 50+ days. ETH/USDT long, entry 2418, no exit data. The DB row is orphaned: the controlling Loop 2 process has been down for most of that window, and nothing on the exchange or in our codebase has been protecting that position.

This isn't an isolated mistake — it's the *expected* outcome of the current design, and would happen again on the next live run.

## Root cause

Two structural gaps in `src/agents/execution_agent.py`:

1. **No server-side stop-loss exists when OCO falls back.**
   - `place_trade()` ([execution_agent.py:195-205](../src/agents/execution_agent.py)) tries an OCO order first. On Binance Testnet OCO is rejected most of the time.
   - On rejection, the code falls back to `_poll_sl_tp()` — an **in-process** loop that calls `fetch_ticker()` every 30 s and issues a market close when SL or TP is breached.
   - That polling protects the position *only while our Python process is alive*. If the process crashes, exits, or is killed by the OS, polling stops instantly and the position has zero downside protection on the exchange.

2. **`reconcile_open_trades()` doesn't actually reconcile.**
   - On Loop 2 startup ([execution_agent.py:422](../src/agents/execution_agent.py)) it iterates `outcome='open'` rows and only marks them `'failed'` (if `order_id` is missing) or `'interrupted'` (if the order status looks closed).
   - It never queries the *position* state, never re-places missing stops, never backfills exit data from `fetch_my_trades()`, and never resumes monitoring for trades whose position is still live.
   - So a process restart cannot recover an open trade; it just stamps it `'interrupted'` and moves on.

The strategy spec also has no `max_holding_bars` field. A strategy can in principle hold indefinitely — which is correct trading behaviour, but it means infrastructure cannot use "the trade is too old" as a safe heuristic.

## Reproduction

1. Start Loop 2 with a strategy that places at least one position
2. Confirm a trade row is written with `outcome='open'`
3. Confirm OCO order was rejected (typical on testnet) — check logs for "OCO rejected"
4. `kill -9` the Loop 2 process (or unplug network for 24+ h)
5. Inspect Binance Testnet: position is open with **no resting SL/TP order** protecting it
6. Restart Loop 2 with the same strategy → reconcile marks the row `'interrupted'`, but the actual position on the exchange is still open and still unprotected

## Why a wall-clock force-close is **not** the answer

A first instinct is "auto-close any trade older than 72 h". That's wrong for trading:

- **Swing strategies legitimately hold for days or weeks.** A 72-h cap silently sabotages any strategy with longer-than-72-h holding periods.
- **Closing on time, not on signal, costs money.** If the position is up 8 % at the 72-h mark and would have hit a 9 % TP next day, the auto-close hands away the gain.
- **Time-based closes don't actually solve the safety problem.** Between hour 0 and hour 72, the position is still unprotected against a flash crash. The real risk is "no stop on the exchange", not "the trade has been open a while".

The correct solution targets the actual unsafe state: a position on the exchange with no protective order behind it.

## Proposed fix (5 layers)

Each layer addresses a different failure mode. The core principle: **the trade is alive on the exchange, not in our DB. The exchange must safely manage the position when our controller is down.**

### Layer 1 — Server-side SL/TP, always

After every successful entry fill, immediately place two separate server-side conditional orders on Binance USDM futures (per [[2026-04-18-usdm-futures-testnet-shorts]] we are on USDM futures testnet, where these are supported):

- `STOP_MARKET` with `stopPrice = sl_price`, `closePosition=true`, `reduceOnly=true`
- `TAKE_PROFIT_MARKET` with `stopPrice = tp_price`, `closePosition=true`, `reduceOnly=true`

These persist on the exchange regardless of our process state. When one fires, the position is flat; the other becomes a no-op (or can be cancelled by a brief post-fill poll).

If both order placements fail: **immediately market-close the entry and refuse the trade.** Better to miss a trade than leave one unprotected.

Schema change: add `sl_order_id` and `tp_order_id` TEXT columns to `trades`.

### Layer 2 — Process-side monitoring for *signal-based* exits only

Strategies can also exit on indicator signals (e.g. RSI > 70). These can only be checked while our process is running — there is no way to express "wait for RSI > 70" as a server-side order. That's fine: SL/TP server-side orders are the safety net, signal-based exits are best-effort on top.

Replace the synchronous `_poll_sl_tp()` with a per-trade **daemon thread** that wakes on candle close (e.g. once per hour on a 1h strategy), evaluates the strategy's signal-based exit conditions, and market-closes the position when the signal fires (cancelling the resting SL/TP).

This drops API cost from ~120 ticker calls per hour per open trade to ~1 candle-check per hour — bounded by the strategy's timeframe.

### Layer 3 — Real reconciliation on every startup

Rewrite `reconcile_open_trades()`. For each `outcome='open'` row:

1. Query `exchange.fetch_position(symbol)`. **If the position is still open**:
   - Re-query its resting `STOP_MARKET` and `TAKE_PROFIT_MARKET` orders. If either is missing, re-place it.
   - Spawn the Layer 2 signal-exit watcher daemon. The trade is back under management.
2. **If the position is closed**: call `exchange.fetch_my_trades(symbol, since=entry_at)` and find the closing fill. Backfill `exit_price`, `exit_at`, `pnl_pct`, set `outcome='win'/'loss'` based on PnL sign.
3. **If no position and no fill record**: mark `outcome='never_filled'` for review (testnet quirks, partial fills, etc).

This means a crash, restart, or strategy degradation never orphans a trade again.

### Layer 4 — Heartbeat + human alert (NO auto-close)

Loop 2 writes a `process_heartbeat` row every 60 s (new table or upsert). A tiny external watchdog (cron, or the parent process) reads the timestamp; if older than 5 min, sends an alert (Slack/email/whatever you wire). The human decides whether to intervene.

**No wall-clock force-close anywhere.** Time is never a reason to exit a trade unless the strategy spec asked for it.

### Layer 5 — Optional `max_holding_bars` in the strategy spec

If a strategy spec declares `max_holding_bars: N` inside its `exit` block, the Layer 2 daemon checks holding duration against that and closes when exceeded. Default: undefined → no time-based exit. The decision belongs to the strategy author, not to infrastructure.

## What to do about trade #1 right now

Do **not** force-close it. Run the read-only investigation script:

```bash
python -m scripts.investigate_orphan_trade --trade-id 1
```

It queries the exchange for the actual current state of that position and its order history, prints a structured report, and appends findings into the "Status notes" section of this file. You then decide whether to manually close it from the Binance UI, attach a stop, or backfill the DB based on whatever the exchange tells us.

## Status notes

### Investigation — trade id=1 — 2026-06-07 04:18 UTC

**DB row**

- symbol: `ETH/USDT`
- side: `buy`
- entry_at: 2026-04-18 03:09 UTC
- entry_price: 2418.0
- amount_usdt: 50.0
- outcome (DB): `open`
- order_id: `3360927`

**Exchange mode**: `PaperExchange`

**Position state**

- ✓ Position OPEN on exchange: size=50.0, entry=2418.0, unrealized PnL=None

**Resting orders on this symbol**

- (none — no resting SL/TP or other orders)

**Original entry order status**

- OrderNotFound: Order 3360927 not found

**Fills since entry**

- fetch_my_trades unavailable: fetch_my_trades not implemented on this exchange

**Recommendation (human decides)**

- If position is OPEN and no SL/TP resting → place a stop manually via Binance UI or `exchange.create_order(...)` while you decide on the design fix.
- If position is CLOSED but DB still says open → backfill `exit_price`, `exit_at`, `outcome`, `pnl_pct` from the fills above (run a targeted SQL UPDATE).
- If no position and no fill → mark `outcome='never_filled'` and investigate why the original order never resulted in a position.

---
*(Populated by `scripts/investigate_orphan_trade.py` when run. Most-recent report first.)*

---

## Related

- MOC: [[_issues]]
- Paired root cause: [[2026-06-07-open-ended-polling-api-cost]]
- Module: [[execution]]
- Drives: probable [[2026-04-10-module4-init-db-once]] follow-up
- Venue context: [[2026-04-18-usdm-futures-testnet-shorts]]
