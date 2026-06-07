# Decision: Look-ahead bias test uses causal truncation, not bar shifting

**Date**: 2026-04-11

## Decision
The look-ahead bias test in `tests/test_backtest.py` was redesigned: rather than running the engine twice on a dataset shifted by one bar and asserting outputs differ, the test asserts that signals computed on a truncated prefix of the data match the corresponding prefix of signals computed on the full dataset.

## Reason
The shift-then-compare test was producing false positives (results differed for legitimate reasons unrelated to look-ahead). The truncation method directly encodes the property we actually want — that bar `t`'s signal does not depend on data after bar `t` — and produces a definitive pass/fail without spurious sensitivity to data layout.

## Alternatives Considered
- **Shift-and-compare** — original approach; rejected for false positives
- **Manual code review only** — rejected: no automated guarantee against regression


## Related

- MOC: [[backtesting]]
- [[testing_rules]]
