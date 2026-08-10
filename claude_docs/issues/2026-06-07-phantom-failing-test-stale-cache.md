---
tags: [issue, bug, resolved]
related: ["[[_issues]]", "[[agents]]"]
---

# Issue: pytest `lastfailed` referenced a deleted test (`test_all_mechanism_classes_present`)

**Discovered**: 2026-06-07
**Status**: resolved (real test added)
**Severity**: low
**Affects**: [[agents]]

## Problem

`.pytest_cache/v/cache/lastfailed` listed `tests/test_candidate_generator.py::test_all_mechanism_classes_present` as failing, but no function with that name existed in the test file. The cache was stale from a previous rename/delete; pytest happily kept reporting a phantom failure.

## Root cause

`lastfailed` is updated only when a test fails or passes. A renamed/deleted test simply leaves a stale entry until something else writes the cache.

## Reproduction

```bash
cat .pytest_cache/v/cache/lastfailed   # shows the phantom test name
grep -n "def test_all_mechanism_classes_present" tests/test_candidate_generator.py
# (no match — function does not exist)
```

## Proposed fix (applied)

Added a real `test_all_mechanism_classes_present` that **probes each entry in `MECHANISM_CLASSES` one at a time** (blacklisting the others) to confirm every declared class is reachable from `generate_candidate_pool`. Catches dead-code mechanism classes.

The new test surfaced an adjacent finding: with all classes enabled and the pool capped at `CANDIDATE_POOL_SIZE = 12`, two classes (`breakout` and `volatility`) are routinely squeezed out of the final pool. They *are* generated, just dropped by the cap. The new test bypasses the cap via blacklist so this isn't a false negative — but if it matters to Loop 1 that those classes appear, a follow-up to either bump the cap or rebalance the helpers might be worth filing.

## Status notes

- 2026-06-07 — test added at [test_candidate_generator.py](../tests/test_candidate_generator.py), all 8 tests in that file pass. `lastfailed` cache deleted so it stops complaining about the phantom name.

## Related

- MOC: [[_issues]]
- Module: [[agents]]
