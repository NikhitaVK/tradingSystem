---
tags: [moc, code-moc, auto]
---

# Code Map

> ⚠️ **AUTO-GENERATED** by `scripts/sync_obsidian_graph.py` — do not edit by hand.

**Up**: [[dashboard]]
**Across**: [[_architecture]] · [[_modules]] · [[_decisions]] · [[_standards]] · [[_tasks]] · [[_issues]] · [[_trials]]

One node per source file. Edges mirror real `import` statements. Open **Graph View** to see how the modules wire together, or use the live table below (sortable / groupable).

![[code-map.base]]

## `config/`
- [[config.settings]] — All configurable parameters in one place.

## `scripts/`
- [[scripts.backfill_kb_from_search_logs]] — Recover empirical findings stranded in reasoning_logs.
- [[scripts.investigate_orphan_trade]] — Read-only diagnostic for a stuck `outcome='open'` trade.
- [[scripts.run_loop2_smoke]] — Smoke test: Run Loop 2 against a fast-firing 1m test strategy using PaperExchange.
- [[scripts.sync_obsidian_graph]] — Generate Obsidian "code note" companions for the graph.

## `scripts/trials/`
- [[scripts.trials.api_cost_ladder]] — Trial 1, rebuilt as a one-change-at-a-time cost ablation.
- [[scripts.trials.api_cost_trial]] — Trial 1 (API cost / model routing).
- [[scripts.trials.kb_ablation_ladder]] — Trial 4, rebuilt as a one-change-at-a-time ablation.
- [[scripts.trials.kb_retrieval_trial]] — Offline retrieval-ranking bake-off for the KB / layered memory.
- [[scripts.trials.kb_structure_trial]] — Measured re-run of Component Trial #4 (Knowledge-Base Structure).
- [[scripts.trials.memory_outcome_trial]] — Does better memory produce better STRATEGIES?
- [[scripts.trials.prompt_version_trial]] — Trial 2 (analyst prompt version: v1 vs v2 vs v3).
- [[scripts.trials.provider_comparison_trial]] — Trial 1: different AI APIs, cost vs success.

## `src/`
- [[src.loop1]] — Full Loop 1 orchestration: strategy discovery and validation.
- [[src.loop2]] — Continuous execution loop orchestrator.
- [[src.main]] — Outer loop: init_db → Loop 1 → Loop 2 → restart on degradation.

## `src/agents/`
- [[src.agents.analyst_agent]] — Adversarial strategy evaluation and degradation reflection.
- [[src.agents.candidate_generator]] — Deterministic strategy spec emitter.
- [[src.agents.claude_client]] — Anthropic API wrapper for all agent calls.
- [[src.agents.empirical_search]] — Run backtest on each candidate spec and rank by composite score.
- [[src.agents.execution_agent]] — Market orders on Binance Testnet, SL/TP polling, trade logging.
- [[src.agents.mcp_client]] — TradingView MCP subprocess wrapper with native TA fallback.
- [[src.agents.risk_agent]] — Deterministic risk gating for Loop 2 trade approval.
- [[src.agents.strategy_agent]] — Empirical search + LLM selector for strategy discovery.
- [[src.agents.tools]] — Anthropic tool schemas and their Python handler functions.

## `src/backtest/`
- [[src.backtest.data_validator]] — Pre-backtest OHLCV data quality checks.
- [[src.backtest.engine]] — Walk-forward backtesting engine.
- [[src.backtest.hmm_regime]] — Hidden Markov Model regime classification.
- [[src.backtest.indicators]] — Pure pandas/numpy indicator functions.
- [[src.backtest.mtf_confirmer]] — Multi-timeframe trend confirmation filter (Phase 9).
- [[src.backtest.strategy_runner]] — Parse a strategy spec and produce a signal Series.

## `src/data/`
- [[src.data.ccxt_feed]] — Poll live OHLCV data from Binance Testnet via CCXT.
- [[src.data.ingestor]] — Ingest BlackBull MT4/MT5 CSV files into the ohlcv_history table.
- [[src.data.knowledge_base]] — CRUD layer over the knowledge_base SQLite table.
- [[src.data.memory_feedback]] — Feedback-driven importance updates from trading outcomes.
- [[src.data.memory_layers]] — FinMem-inspired layered memory management.
- [[src.data.schema]] — SQLite table definitions and database initialisation.
- [[src.data.strategies]] — Repository class for the strategies and trades tables.

## `src/exchange/`
- [[src.exchange.factory]] — Build the correct exchange adapter based on EXECUTION_MODE.
- [[src.exchange.paper_exchange]] — Simulated exchange with CCXT-compatible interface.

## `src/gui/`
- [[src.gui.kb_gui]] — Author: Nikhita Krisson

## `src/monitor/`
- [[src.monitor.degradation_monitor]] — Background daemon thread watching rolling win rate.

## `src/strategy_templates/`
- [[src.strategy_templates]] — _No module docstring._
- [[src.strategy_templates.registry]] — Strategy template library organised by market regime (Phase 8).

## `tests/`
- [[tests.test_backtest]] — Isolation tests for Module 2 (Backtesting Engine).
- [[tests.test_binance_live]] — Live Binance Testnet integration test.
- [[tests.test_candidate_generator]] — Tests for the deterministic strategy spec emitter.
- [[tests.test_data_pipeline]] — Isolation tests for Module 1 (Data Pipeline).
- [[tests.test_empirical_search]] — Tests for the empirical backtest search engine.
- [[tests.test_knowledge_base]] — Tests for Phase 2, 5, 6, and 7 KB enhancements.
- [[tests.test_loop1]] — Isolation tests for Module 3 (Loop 1 strategy discovery agents).
- [[tests.test_loop2]] — Module 4 (Execution Loop) test suite.
- [[tests.test_memory_feedback]] — Tests for memory_feedback — feedback-driven KB importance updates.
