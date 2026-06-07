# Decision: Adopt 7 production patterns from open-source trading bots in Module 4

**Date**: 2026-04-17

## Decision
Module 4 execution loop locks in: track-before-send (Hummingbot), startup reconciliation (NautilusTrader), `StoplossGuard` inside `RiskAgent` (Freqtrade), per-iteration exchange state refresh (Freqtrade), and poll-per-candle rather than 1-second clock-tick (rejected Hummingbot's clock-tick because the system runs on 1h candles).

## Reason
These patterns are battle-tested in production crypto bots and each addresses a specific class of failure (lost orders, reconciliation after restart, runaway losers, stale exchange state). Adopting them avoids reinventing well-known solutions. Rejecting Hummingbot's 1s tick is justified because at 1h candle granularity the tick adds polling cost with no signal benefit.

## Alternatives Considered
- **1-second clock tick** — rejected: 3600x overhead with no information gain on 1h bars
- **Build all execution safety net from scratch** — rejected: re-derives years of production hardening for no reason


## Related

- MOC: [[execution]]
- [[2026-04-18-exchange-factory-paper-real-switch]]
