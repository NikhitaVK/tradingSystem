# Decision: Pair screener degrades gracefully to BTC/ETH when live data is unavailable

**Date**: 2026-06-27

## Decision

`screen_pair_universe()` and `_score_candidates()` in `src/loop1.py` wrap every CCXT and
per-symbol DB call in try/except. On any failure (no network, CCXT error, a symbol with
<100 bars of history) the screener falls back to `_fallback_candidates()` — BTC/USDT and
ETH/USDT — instead of raising. Loop 1 therefore always starts with at least two pairs.

## Reason

Discovered during testing that Loop 1 would crash at the very first step whenever the
machine was offline or Binance rate-limited the ticker fetch — wasting the whole run before
any strategy work happened. Pair screening is the *least* important step (it only narrows
the universe); it should never be the thing that stops a discovery cycle. BTC/USDT and
ETH/USDT are the only pairs with ingested history anyway, so they are a safe default.

## Alternatives Considered

- **Raise and abort the run on screener failure** — rejected: makes the system fragile to a
  transient network blip and throws away an otherwise-runnable cycle.
- **Retry the CCXT call with backoff** — deferred: useful later, but doesn't help the
  offline / no-history case, which the fallback covers directly.

## Related

- MOC: [[agents]]
- [[2026-04-11-pair-screener-single-slice]]
