# Decision: Candidate pool prefers crossover entries and pure SL/TP exits

**Date**: 2026-04-17

## Decision
`candidate_generator.py` was refactored so the 12-strategy pool defaults to (a) `crosses_above` / `crosses_below` event-style entries and (b) pure stop-loss / take-profit exits, with a single `RSI_BB_IndExit_Mid` mean-reversion candidate retained as a control to test indicator-based exits. Mid and Wide R:R variants are generated via a shared `_exit_block` helper.

## Reason
Empirical observation: indicator-based exits (e.g. exit when RSI > 70) truncate winning trades early and destroy the designed risk:reward ratio. Pure SL/TP exits preserve R:R. Crossover entries fire once per event and produce higher-quality, sparser signals than state-based entries (`price > EMA`) that fire every bar the condition holds true.

## Alternatives Considered
- **All indicator-based exits** — rejected: degrades R:R, what motivated the refactor
- **All state-based entries** — rejected: signal floods, low per-trade quality
- **Single fixed R:R** — rejected: Mid and Wide variants give the analyst a sensitivity check across stop distance


## Related

- MOC: [[backtesting]]
- [[2026-04-16-llm-as-selector-empirical-search]]
