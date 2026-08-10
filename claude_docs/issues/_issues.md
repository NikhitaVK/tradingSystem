---
tags: [moc, issues-moc]
---

# Issues MOC

Live problems discovered in the project that aren't closed architectural decisions or planned tasks. Each entry below is dated by discovery, not resolution. Once resolved, the file stays — the status field flips to `resolved`.

**Up**: [[dashboard]]
**Across**: [[_architecture]] · [[_modules]] · [[_decisions]] · [[_standards]] · [[_tasks]] · [[_trials]] · [[_code]]

---

## Design problems

- [[2026-06-07-stuck-trade-no-server-side-stops]] — **open**. Trades have no protection when our process is down; in-process polling orphans positions on crash. Five-layer fix designed; implementation pending.
- [[2026-06-07-open-ended-polling-api-cost]] — **resolved-pending-impl**. Same root cause as above; resolved by Layer 1 (server-side stops) + Layer 2 (candle-close monitoring).

## Bugs

- [[2026-06-07-phantom-failing-test-stale-cache]] — **resolved**. Pytest `lastfailed` referenced a deleted test; real test added (and surfaced a separate dead-code concern — see Status notes).
- [[2026-06-07-binance-percent-price-filter]] — **resolved**. Live test placed an order beyond Binance's ±5 % price filter; multiplier tightened.

## Research

*(none yet — add open research questions here as they appear)*

---

## File template

When opening a new issue, copy this front-matter + skeleton:

```markdown
---
tags: [issue, <bug|design|research>, <open|resolved-pending-impl|resolved|wontfix>]
related: ["[[_issues]]", "[[<module>]]"]
---

# Issue: <short title>
**Discovered**: YYYY-MM-DD
**Status**: open
**Severity**: low | medium | high
**Affects**: [[<module>]]

## Problem
## Root cause
## Reproduction
## Proposed fix
## Status notes

## Related
- MOC: [[_issues]]
```
