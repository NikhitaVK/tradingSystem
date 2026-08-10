---
tags: [issue, bug, resolved]
related: ["[[_issues]]", "[[execution]]"]
---

# Issue: Live order test rejected by Binance `PERCENT_PRICE_BY_SIDE` filter

**Discovered**: 2026-06-07 (failing since at least 2026-04-20)
**Status**: resolved
**Severity**: low (test-only)
**Affects**: [[execution]]

## Problem

`tests/test_binance_live.py::TestOrderPlacement::test_place_and_cancel_limit_order` placed a limit buy 50 % below the current BTC price as an "unreachable" price the order would never fill at. Binance Testnet rejected it with `binance Filter failure: PERCENT_PRICE_BY_SIDE`.

## Root cause

Binance enforces a `PERCENT_PRICE_BY_SIDE` filter on every symbol that prevents limit prices more than ±5 % from the last traded price. A 50 % discount is far outside the allowed window.

## Reproduction

```bash
SKIP_LIVE_TESTS=  pytest tests/test_binance_live.py::TestOrderPlacement::test_place_and_cancel_limit_order -v
# binance {"code":-1013,"msg":"Filter failure: PERCENT_PRICE_BY_SIDE"}
```

## Proposed fix (applied)

Changed `0.50` to `0.97` (3 % below current price) at [test_binance_live.py:304](../tests/test_binance_live.py). 3 % is well inside the ±5 % filter and the order is still safely unreachable for the test's purpose (we want it to *not* fill, then cancel it).

## Status notes

- 2026-06-07 — multiplier updated, comment in the test now references the filter so future-readers know why 3 % specifically.

## Related

- MOC: [[_issues]]
- Module: [[execution]]
