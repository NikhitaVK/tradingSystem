# Module 4 — Execution Loop (Loop 2)

**Status**: Not built  
**Isolation test**: `tests/test_loop2.py` — test file not yet written  
**Depends on**: Module 1 (CCXT feed, DB), Module 2 (strategy spec format), Module 3 (validated strategy, analyst)

## Purpose

Continuously poll live OHLCV, detect entry signals per the validated strategy, run them through risk arithmetic (risk agent) and brief analyst check (CP2), then place paper trades on Binance Testnet. Background degradation monitor triggers Loop 1 restart on strategy failure.

## Loop 2 Flow

```
1. DegradationMonitor.start()        → background thread begins watching trades table
2. Loop:
   a. monitor.flag.is_set()?
      YES → analyst_reflect() → KB write → raise StrategyDegradedException
   b. ccxt_feed.get_latest_candles()  → check signal conditions
   c. signal detected?
      NO  → sleep, continue
      YES →
   d. risk_agent.review()            → arithmetic check (reject/approve/adjust size)
      REJECTED → log, continue
   e. analyst.evaluate_trade() [brief, CP2, ~2000 tokens]
      NOT CONFIRMED → log, continue
   f. execution_agent.place_trade()   → Binance Testnet market order
   g. log trade → trades table
   h. sleep until next candle close
```

## Risk Agent (Arithmetic Only)

Deterministic rules — no Claude involved. Reason: execution latency and the rules are entirely formulaic.

```python
class RiskAgent:
    MAX_POSITION_PCT  = 0.05   # max 5% of balance per trade
    MAX_CONCURRENT   = 3      # max 3 open positions simultaneously
    MAX_DAILY_LOSS   = 0.03   # halt if daily realised PnL < -3% of starting balance

    def review(proposed_size_usdt, balance, open_positions, daily_pnl_pct)
        -> {'approved': bool, 'adjusted_size': float, 'reason': str}
```

Hard limits:
- `daily_pnl_pct < -MAX_DAILY_LOSS` → reject all new trades
- `len(open_positions) >= MAX_CONCURRENT` → reject
- `proposed_size_usdt > balance * MAX_POSITION_PCT` → adjust down to limit, approve

## Debate Checkpoint 2 (Analyst Brief)

Lower thinking budget (2000 tokens) for speed. Receives last 20 live candles + proposed trade + strategy spec. Returns `{'confirm': bool, 'note': str}`. Not called if risk agent already rejected. If not confirmed, trade is skipped and signal logged to `reasoning_logs`.

## Degradation Monitor

```python
class DegradationMonitor:
    def __init__(self, strategy_id, threshold, window=20, check_every=5, db_path=None):
        # threshold: from strategy's calibration (mean - std of slice win rates, min 0.30)
        # window: rolling trade count (starting value 20, to be calibrated)
        self.flag = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
```

Reads `trades` table directly — sets `self.flag` only. Main Loop 2 acts on the flag.

**Time-based fallback**: if no trades complete for `STALE_STRATEGY_HOURS` (48h, from `config/settings.py`), monitor triggers regardless of trade count.

## Key Files

### `src/monitor/degradation_monitor.py`
- `DegradationMonitor` class — background thread watching rolling win rate
- `monitor.flag.is_set()` → True when win rate drops below threshold

### `src/agents/risk_agent.py`
- Pure arithmetic `RiskAgent` class — no Claude, no I/O

### `src/agents/execution_agent.py`
- `place_trade(symbol, side, amount_usdt, stop_loss_pct, take_profit_pct, exchange, db_path) -> dict`
- Attempts OCO for stop/TP; falls back to polling if rejected by testnet
- Logs trade to `trades` table

### `src/loop2.py`
- `run_loop2(strategy, db_path)` — runs continuously
- Raises `StrategyDegradedException` when degradation detected
- `main.py` catches this and restarts Loop 1

### `src/main.py`
- Outer loop: `run_loop1()` → `run_loop2()` → on `StrategyDegradedException` restart Loop 1
- Calls `init_db()` once at startup
- Graceful SIGTERM/SIGINT shutdown: marks `outcome='open'` trades as `'interrupted'`

## Binance Testnet Setup

```python
exchange = ccxt.binance({'apiKey': ..., 'secret': ...})
exchange.set_sandbox_mode(True)   # must be called immediately after init
```

Testnet keys: https://testnet.binance.vision/

**Order amount must be in base currency** (BTC for BTC/USDT), not USDT:
```python
ticker = exchange.fetch_ticker(symbol)
amount_base = amount_usdt / ticker["last"]
exchange.create_order(symbol, "market", side, amount_base)
```

## OCO Fallback

When OCO is rejected by testnet, poll every `OCO_POLL_INTERVAL_SECONDS` (default 30s) and force-close after `OCO_MAX_WAIT_SECONDS` (default 24h). Use settings values, never hardcoded.

## Isolation Test Criteria (14 tests across 6 groups)

**Group A — Signal detection**
1. Synthetic candles with RSI < 30 at bar 5 → signal fires at bar 5, not before
2. Condition never met over 100 bars → no signal fires

**Group B — Risk agent**
3. Proposed size 6% of balance → adjusted to 5%, approved
4. Daily PnL < -3% → rejected
5. 3 open positions → rejected

**Group C — Debate CP2**
6. Analyst returns `{confirm: False}` → execution_agent NOT called
7. Analyst returns `{confirm: True}` → execution_agent IS called

**Group D — Execution agent**
8. Mock CCXT → `create_order` called with correct symbol, side, amount
9. Trade logged to `trades` table

**Group E — Degradation monitor**
10. 20 trades (40% win rate), threshold 0.45 → flag.set() within check interval
11. 20 trades (50% win rate), threshold 0.45 → flag remains False

**Group F — Full Loop 2 integration**
12. All mocks wired → correct execution order
13. Injected degradation → `StrategyDegradedException` raised
14. `analyst.reflect()` called on degradation (not `evaluate()`)

## Known Issues

- Not yet built — `src/monitor/` is empty, `src/loop2.py` and `src/main.py` do not exist


## Related

- MOC: [[_modules]]
- [[2026-04-10-module4-init-db-once]]
- [[2026-04-10-module4-graceful-shutdown]]
- [[2026-04-17-module4-patterns-from-oss]]
- [[2026-04-18-exchange-factory-paper-real-switch]]
- [[2026-04-18-usdm-futures-testnet-shorts]]
- [[2026-04-20-probationary-tier]]
