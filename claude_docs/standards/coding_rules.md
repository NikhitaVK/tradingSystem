# Coding Rules

## Configuration Over Hardcoding

All tuneable parameters live in `config/settings.py`. Import from there — never hardcode values elsewhere.

```python
# Bad
SLIPPAGE = 0.001

# Good
from config.settings import SLIPPAGE_PER_SIDE as SLIPPAGE
```

## No Circular Imports

Data flow is strictly one-directional:

```
data → backtest → agents → loops
```

If you find yourself needing `from src.agents import X` inside `src/data/`, the design is wrong. Restructure.

## Strategy Spec is the Contract

The strategy spec JSON dict is the immutable contract between the backtest engine and all agents. Never change its shape without updating both sides.

## Thinking Blocks Are Immutable

Claude extended thinking responses include cryptographically signed blocks. Pass them back to the API exactly as received. Never summarise, truncate, or strip them. Storage and restoration must preserve the full block including signature:

```python
# Storing
thinking_json = json.dumps([b.model_dump() for b in response.content if b.type == "thinking"])
# Restoring
thinking_blocks = json.loads(thinking_json)  # pass directly in messages
```

## Prompts Are Versioned Files

Never embed prompt strings inline in Python. Keep them in `prompts/` as versioned `.txt` files:

```
prompts/strategy_agent_v1.txt
prompts/analyst_eval_v1.txt
prompts/analyst_reflect_v1.txt
```

Load at module import time, not at call time:

```python
# At module level — fails immediately on startup if file missing
_STRATEGY_PROMPT = Path("prompts/strategy_agent_v1.txt").read_text()
```

## Database

- `init_db()` called **once** at startup in `main.py` only
- All timestamps are Unix milliseconds UTC
- `volume` in `ohlcv_history` is MT4 tick volume, not real exchange volume
- Use `get_connection(db_path)` from `schema.py` for all DB access
- WAL mode + foreign keys ON in `get_connection()`

## Agent Call Logging

Every Claude API call logs its full thinking + response to `reasoning_logs` table. Use `ClaudeClient.chat()` which handles this automatically.

## Error Handling

- Raise clear, specific exceptions (not generic `Exception`)
- `ValueError` for bad input data
- `RuntimeError("Database not initialised — call init_db() first")` if DB accessed before init
- Never let SQLite produce cryptic errors

## Sharpe Annualisation

Use timeframe-dependent annualisation factor:

```python
PERIODS_PER_YEAR = {
    "1m": 252*24*60, "5m": 252*24*12, "15m": 252*24*4,
    "1h": 252*24, "4h": 252*6, "1d": 252
}
```

Do NOT use `sqrt(252)` on non-daily data — it gives ~8.8x too low Sharpe for 1h bars, causing good strategies to be rejected.

## Look-Ahead Bias

All indicators computed causally. Zero out signals during indicator warm-up period:

```python
min_valid_bar = max(lookback for each indicator in spec)
signals.iloc[:min_valid_bar] = 0
```

Reset mask at start of each out-of-sample window in walk-forward.

## Graceful Shutdown

In `main.py`, handle SIGTERM and SIGINT. On shutdown, mark all `outcome='open'` trades as `'interrupted'` so next startup can distinguish interrupted positions from genuinely open ones.


## Related

- MOC: [[_standards]]
- [[2026-05-11-parameterised-queries-only]]
- [[2026-04-11-prompt-files-load-at-import]]
