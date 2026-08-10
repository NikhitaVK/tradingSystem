---
tags: [issue, design, resolved-pending-impl]
related: ["[[_issues]]", "[[execution]]", "[[2026-06-07-stuck-trade-no-server-side-stops]]"]
---

# Issue: API cost of open positions is structurally unbounded

**Discovered**: 2026-06-07 (paired with the stuck-trade analysis)
**Status**: resolved-pending-impl — same fix as the stuck-trade issue resolves this
**Severity**: medium — bounded by Binance rate limits but wasteful and brittle at scale
**Affects**: [[execution]]

## Problem

`_poll_sl_tp()` in [execution_agent.py](../src/agents/execution_agent.py) calls `exchange.fetch_ticker(symbol)` every `OCO_POLL_INTERVAL_SECONDS = 30` seconds for the entire lifetime of a position. The position's lifetime is not known in advance — a strategy can hold for minutes, hours, days, or weeks. So:

- 1 open trade × 30 s polling × 1 day = **2 880 ticker calls/day**
- 3 concurrent open trades × 30 days = **259 200 ticker calls**
- The 50-day-stuck trade #1 would have cost **~144 000 ticker calls** if polling had survived

Binance rate limits (1 200 requests/min/IP) absorb a single bot, but the design has no upper bound and scales linearly with number of open positions × holding duration.

There is no event-driven exit detection (no websocket). All exit information is pulled, not pushed.

## Root cause

Same as [[2026-06-07-stuck-trade-no-server-side-stops]]: SL/TP enforcement was put into an in-process polling loop instead of into server-side conditional orders. Once that decision was made, *the price has to be checked every interval forever* because nothing else is watching.

## Reproduction

1. Start Loop 2 with any strategy that opens at least one position
2. Tail the logs — observe `fetch_ticker` calls firing at the configured 30 s cadence
3. Multiply by holding duration to see the scaling problem

## Proposed fix

Identical to layers 1–2 of the [[2026-06-07-stuck-trade-no-server-side-stops]] solution. Restated for clarity:

- **Layer 1** — Server-side `STOP_MARKET` + `TAKE_PROFIT_MARKET`. The exchange enforces SL/TP. Our process does not need to poll the ticker to know when SL/TP fired.
- **Layer 2** — The remaining check (strategy's signal-based exit, e.g. RSI > 70) only needs to run **once per candle close**, not every 30 s. On 1h timeframe that's 24 calls/day, not 2 880.

API cost per open trade per hour drops from ~120 calls to ~1. Reconciliation on startup adds 1–2 calls per open trade, but that's per restart, not per hour.

## Status notes

Marked resolved-pending-impl because the fix is fully designed in the stuck-trade issue. Will close when the execution-agent rewrite ships.

## Related

- MOC: [[_issues]]
- Same root cause: [[2026-06-07-stuck-trade-no-server-side-stops]]
- Module: [[execution]]
