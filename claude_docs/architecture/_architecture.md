---
tags: [moc, architecture-moc]
---

# Architecture MOC

Project-level architectural context. Read these for the big picture before drilling into a module.

**Up**: [[dashboard]]
**Across**: [[_modules]] · [[_decisions]] · [[_standards]] · [[_tasks]] · [[_issues]] · [[_trials]] · [[_code]]

---

- [[overview]] — Two-loop architecture, data flow, state, four-module decomposition
- [[decomposition]] — How to read the graph: the Canvas, the Bases, the coloured graph
- [[system-decomposition.canvas|System decomposition (Canvas)]] — the laid-out module + data-flow diagram
- [[db-schema-evolution]] — Original vs current SQLite schema (ER diagrams)

## Shaping decisions

- [[2026-04-13-structured-context-system]] — Why the layered `claude_docs/` exists
- [[2026-04-18-init-skill-claude-md-centralization]] — Why there's a single `CLAUDE.md`
- [[2026-05-18-four-module-decomposition]] — Module 1 → 2 → 3 → 4 build order
- [[2026-04-10-two-loop-debate-checkpoints]] — Loop 1 / Loop 2 split + debate CPs
- [[2026-04-16-llm-as-selector-empirical-search]] — Empirical search replaces tool-use loop
