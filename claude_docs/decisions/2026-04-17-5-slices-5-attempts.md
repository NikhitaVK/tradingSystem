# Decision: Raise walk-forward slices to 5 and LOOP1_MAX_ATTEMPTS to 5

**Date**: 2026-04-17

## Decision
`BACKTEST_N_SLICES` raised 3 → 5 and `LOOP1_MAX_ATTEMPTS` raised 3 → 5 in `config/settings.py`.

## Reason
A live Loop 1 run had only one candidate (`ADX_EMA_Cross_PureSLTP_Mid`) achieve a calculable WFE (3.98) because the others lacked enough slices to compute in-sample Sharpe. Raising slice count makes WFE computable for more candidates. Raising max attempts gives the candidate-name diversification feedback loop more chances to surface diverse strategies before exhausting.

## Alternatives Considered
- **Raise slices only** — rejected: doesn't help when generator + analyst converge on the same rejected candidate
- **Raise attempts only** — rejected: more attempts don't help if every candidate has uncomputable WFE


## Related

- MOC: [[backtesting]]
- [[2026-04-17-min-trades-per-slice-5]]
- [[agents]]
