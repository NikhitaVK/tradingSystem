# Phase 0 — Documentation Discovery

**Status**: Already complete (this document IS the output).

**APIs confirmed available**:
- `knowledge_base.write_finding(category, content, db_path, strategy_id=None) → int`
- `knowledge_base.query_relevant(keywords, db_path, limit=10, category=None) → list[dict]`
- `knowledge_base.get_all_findings(db_path, category=None, strategy_id=None, limit=50) → list[dict]`
- `schema.init_db(db_path) → None`
- `schema.get_connection(db_path) → sqlite3.Connection`
- `engine.run_backtest(strategy_spec, db_path, n_slices) → dict` — returns `{"slices": [{..., "regime": str}, ...], "aggregate": {"regime_breakdown": {...}}, "calibration": {...}, "viable": bool}`
- `indicators.compute_atr(high, low, close, period=14) → pd.Series`
- `tools._normalise_spec(spec) → dict` — canonical injection point before backtest
- `tools.handle_run_backtest(args, db_path) → str` — calls engine.run_backtest
- `tools.handle_query_knowledge_base(args, db_path) → str`
- `tools.handle_write_to_knowledge_base(args, db_path) → str`
- `tools.handle_save_validated_strategy(spec, results, db_path) → int`

**Existing backtest result structure** (confirmed):
```python
# slices[i] already has:
{
    "slice_id": int,
    "start_date": str,
    "end_date": str,
    "regime": "sideways",  # ← already present, but simple (% change only)
    "win_rate": float,
    "sharpe": float,
    "sortino": float,
    "max_drawdown": float,
    "total_trades": int,
    "pnl_pct": float,
    "profit_factor": float,
    "expectancy": float,
    "avg_win_loss_ratio": float,
    "max_consecutive_losses": int,
    "degenerate": bool,
}
# aggregate already has:
{
    "win_rate_mean": float,
    "sharpe_mean": float,
    "sortino_mean": float,
    "max_drawdown_worst": float,
    "total_trades": int,
    "profit_factor_mean": float,
    "regime_breakdown": {"bull": [1,2], "bear": [], "sideways": [3]},
}
```

**Injection point confirmed**: `loop1.py:65-68` — keyword list is where regime context is retrieved from KB before strategy generation.


## Related

- MOC: [[_tasks]]
- [[implementation_order]]
