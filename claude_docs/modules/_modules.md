---
tags: [moc, modules-moc]
---

# Modules MOC

Working reference for each of the four modules. Source-of-truth specs live in `.claude/rules/modules/` — these files are the human-readable digest with current-state notes.

**Up**: [[dashboard]]
**Across**: [[_architecture]] · [[_decisions]] · [[_standards]] · [[_tasks]] · [[_issues]] · [[_trials]] · [[_code]]

---

- [[data_pipeline]] — Module 1: SQLite schema, BlackBull CSV ingest, CCXT live feed, KB CRUD
- [[backtesting]] — Module 2: walk-forward engine, indicators, strategy runner, cost model
- [[agents]] — Module 3: strategy + analyst agents, empirical search, Loop 1 orchestrator
- [[execution]] — Module 4: risk agent, execution agent, Loop 2, degradation monitor
