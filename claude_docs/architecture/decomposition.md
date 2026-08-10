---
tags: [architecture, decomposition]
---

# System Decomposition — how to read the graph

The vault has three complementary views of how the system is broken into parts. Use the
right one for the job instead of staring at the raw force-directed graph.

## 1. The picture — [[system-decomposition.canvas]]

A hand-laid-out **Canvas** showing the two-loop architecture and the four modules as
coloured groups, left→right along the data pipeline (Data → Backtest → Agents/Loop 1 →
Execution/Loop 2), with the shared SQLite store in the middle and **labelled data-flow
edges** for each seam (`ohlcv_history`, `backtest results`, `validated strategy`,
`degradation → restart`). This is the diagram to **present as decomposition evidence** —
it says *what the parts are and how data crosses between them* at a glance.

> It is curated, not auto-generated: the pre-commit hook never touches it, so any layout
> tweaks you make stick. If the module boundaries change, edit it by hand (the `json-canvas`
> skill in `.claude/skills/` tells the agent how).

## 2. The data — Bases dashboards (`claude_docs/dashboards/`)

Live, queryable tables that never go stale (they read the frontmatter the generator writes):

- **[[code-map]]** — every source file with its `module`, `layer`, outbound `imports`, and
  `← imported by` count; grouped by module, plus a "by layer" card view and a "tests by
  module" coverage view. This is the decomposition *table* + your day-to-day navigator.
- **[[decisions]]** — every dated ADR with its tags and how often it's referenced: the
  design-reasoning log, queryable.

## 3. The map — Graph View (coloured by module)

The native Graph View is now colour-grouped by the `module/*` tags on each code note
(data = cyan, backtest = green, agents = purple, execution = red, infra = grey, MOCs =
gold). The blob is now a legible module map; toggle a colour group to isolate one module.

## How it stays current

`scripts/sync_obsidian_graph.py` (run automatically by the git pre-commit hook) regenerates
the `claude_docs/code/` notes and their `module` / `layer` / `imports_count` frontmatter on
every commit — which keeps the Bases tables and the graph colours correct with no manual
step. Only the Canvas is hand-maintained.

## Related

- MOC: [[_architecture]]
- [[overview]] — the two-loop architecture in prose
- [[_code]] — the auto-generated code-note graph
- [[2026-07-27-obsidian-code-graph-companion-notes]] — why the code graph exists
