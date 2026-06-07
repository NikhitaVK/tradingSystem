# Decision: Migrate from Binance Spot Testnet to USDM Futures Testnet to support shorts

**Date**: 2026-04-18

## Decision
Switch the execution layer's testnet target from Binance Spot Testnet to Binance USDM Futures Testnet. Strategy specs now carry a `"direction": "long" | "short"` field and the backtest `_simulate_trades`, `loop2` order routing, and exchange factory are all bidirectional.

## Reason
Binance Spot Testnet has no margin facility, so short trades are impossible — that removes mean-reversion shorts, momentum-short, and pairs from the strategy universe. USDM Futures Testnet is free, supports both directions, and CCXT works with it (with manual endpoint URL override since `setSandboxMode` alone is unreliable, plus `:USDT` symbol suffix).

## Alternatives Considered
- **Synthetic short via inverse-spot logic** — rejected: doesn't match live execution semantics; backtest-live divergence
- **Long-only system** — rejected: explicitly disallowed by user; removes a large class of strategies
- **Alpaca / IBKR paper** — rejected: not crypto-native, separate API surface


## Related

- MOC: [[execution]]
- [[2026-04-18-exchange-factory-paper-real-switch]]
