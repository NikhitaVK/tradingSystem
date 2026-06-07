# Decision: PaperExchange + exchange factory pattern for clean paper/real switching

**Date**: 2026-04-18

## Decision
Introduce `src/exchange/` with a `PaperExchange` class that is CCXT-shaped (`fetch_ticker`, `fetch_ohlcv`, `fetch_balance`, `create_order`, `fetch_order`, `fetch_open_orders`, `fetch_positions`) and a `build_exchange(db_path)` factory selected by `EXECUTION_MODE` env var. Paper market data comes from real Binance endpoints; order fills are simulated against live price; balances/positions are derived from the SQLite `trades` table.

## Reason
The user required a clean transition path from paper to real money trading. A factory swap-point isolates "what exchange object are we using" from every caller, so switching is a single config change. Keeping `PaperExchange` strictly CCXT-shaped means no caller branches on mode. The real path is left as `NotImplementedError` with a clear message until the user explicitly enables it — preventing accidental live orders.

## Alternatives Considered
- **Boolean `is_paper` checks scattered through the codebase** — rejected: error-prone, easy to miss one and place a real order
- **Mock CCXT for paper mode** — rejected: doesn't persist state; can't drive multi-day paper validation
- **Third-party bot (Freqtrade / 3Commas) for execution** — rejected: would lose strategy-spec-level control and tight integration with the discovery loop


## Related

- MOC: [[execution]]
- [[2026-04-18-usdm-futures-testnet-shorts]]
