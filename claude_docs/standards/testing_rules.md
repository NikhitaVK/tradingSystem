# Testing Rules

## Ablation Methodology

**One change per test run.** When something breaks after a change, you know exactly which change caused it.

### Module Isolation (strict sequencing)

```
Module 1 tests pass?  → Start Module 2
Module 2 tests pass?  → Start Module 3
Module 3 tests pass?  → Start Module 4
Module 4 tests pass?  → Run integration tests
```

Never connect modules until each has passed its own isolation tests. If you connect Module 2 before Module 1 tests pass, you will debug Module 2 only to discover the bug was in Module 1's data format.

### Mocks, Not Real Dependencies

During isolation tests:
- Mock Claude client (return predetermined responses — `MockClaudeClient`)
- Mock CCXT (return synthetic candles with known properties)
- Generate synthetic OHLCV with known indicator values so you can assert specific signals fire at specific bars

### Validating Integration Points (in order)

1. **Module 1 → 2**: `engine.run_backtest()` with real data from `ohlcv_history` — assert results match Module 2 isolation test on same synthetic data
2. **Module 2 → 3**: Strategy spec produced by strategy agent passes into `engine.run_backtest()` without KeyError or ValidationError
3. **Module 1 live feed → 4**: CCXT candle format matches what Module 2's strategy runner expects (column names, timestamp units)
4. **Full end-to-end**: `main.py` with all real components — verify per-module metrics match integrated system metrics

If integrated system produces different results than isolation tests:
1. Identify which integration step divergence first appears at
2. Revert to last known-good state of that module
3. Verify tests pass after revert (confirms revert worked)
4. Re-apply the change in the smallest possible increment
5. If it breaks again: the problem is in that specific increment

## Isolation Test Requirements

| Module | Test File | Test Count | Must Pass Before |
|---|---|---|---|
| 1 — Data Pipeline | `tests/test_data_pipeline.py` | 6 | Module 2 starts |
| 2 — Backtest Engine | `tests/test_backtest.py` | 13 | Module 3 starts |
| 3 — Strategy Agents | `tests/test_loop1.py` | 7 | Module 4 starts |
| 4 — Execution Loop | `tests/test_loop2.py` | 14 | Integration tests |

## Prompt Versioning Protocol

When testing a prompt change:
1. Save current prompt version as baseline (e.g. `analyst_eval_v1.txt`)
2. Make exactly one change, save as `v2`
3. Run test cases from calibration_tests.md against both versions
4. Keep the version that passes more test cases
5. Never deploy a new prompt version unless it equals or exceeds the previous on all test cases

## Calibration Tests (run during module build)

Five parameters that cannot be set in advance — run during the build phase of the relevant module:
1. **Train/Test split ratio** — 70/30 vs 80/20 vs 90/10 (starting: 80/20)
2. **Slippage and fee assumptions** — 0.0% vs 0.2% vs 0.4% vs 0.6% round-trip (starting: 0.4%)
3. **Degradation threshold** — computed dynamically per strategy (floor: 0.30)
4. **Statistical significance threshold** — 30 vs 50 vs 100 trades (starting: 50)
5. **Prompt quality tests** — per-agent test cases defined in calibration_tests.md

## Regression Testing After Changes

After any module change post-integration:
1. Re-run that module's isolation tests
2. Re-run the integration step involving that module
3. Re-run full end-to-end if the change touches strategy spec format or DB schema

## Look-Ahead Bias Prevention

All indicator calculations must use only data up to and including bar `t` when making a decision at bar `t`:
- `.shift(1)` applied to signal arrays before comparing to price — signal generated on bar `t` acted on at open of bar `t+1`
- Indicators computed on full slice first, then indexed (safe as long as no future data used in indicator itself)
- Empirical verification: run backtest on data X, run again on X shifted forward by 1 bar, assert results differ


## Related

- MOC: [[_standards]]
- [[2026-04-11-look-ahead-causal-truncation-test]]
- [[2026-04-17-min-trades-per-slice-5]]
