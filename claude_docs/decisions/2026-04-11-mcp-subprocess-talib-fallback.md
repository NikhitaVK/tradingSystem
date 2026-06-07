# Decision: TradingView MCP runs as subprocess with native TA fallback

**Date**: 2026-04-11

## Decision
`MCPClient` launches the TradingView MCP server as a JSON-RPC stdio subprocess (the correct MCP transport), and on timeout or subprocess failure it falls back automatically to `_native_ta_fallback()` which computes indicators from the local `live_candles` table via `src.backtest.indicators` functions. The fallback returns the same response shape as the MCP server, making it invisible to callers.

## Reason
MCP is designed as a subprocess protocol, not a Python import — fighting that wastes engineering time. But the MCP server is an external dependency that can hang or crash; the system must remain functional without it. Routing fallback through the same `compute_rsi` / `compute_macd` etc. used by the backtest engine guarantees indicator values are identical to what the engine sees.

## Alternatives Considered
- **Import MCP code directly** — rejected: violates MCP protocol design, ties us to specific implementation
- **Hard fail on MCP unavailable** — rejected: external dependency outage halts the whole system
- **TA-Lib (C library) fallback** — rejected: native pandas indicators already exist and stay consistent with backtest engine


## Related

- MOC: [[agents]]
- [[backtesting]]
