---
tags: [moc, project-dashboard]
---

# Project Dashboard

Entry point for the `tradingSystemv0.01` Obsidian vault. Each tile below is a Map of Contents (MOC) — open one to see the files in that category and the decisions that shaped them.

## Categories

- [[_architecture]] — Big-picture system design
- [[_modules]] — Per-module working references (data, backtest, agents, execution)
- [[_decisions]] — Dated ADRs (Decision / Reason / Alternatives)
- [[_standards]] — Coding and testing rules
- [[_tasks]] — Pending and completed task specs + improvement phases
- [[_issues]] — Live problems and open design questions (bugs, design gaps, research)
- [[_trials]] — Improvement & trial backlog (undecided hypotheses to test)
- [[_code]] — Auto-generated code map (one node per source file, import graph)

## Decomposition views

Three ways to see how the system is broken into parts — see [[decomposition]] for how to use them:

- [[system-decomposition.canvas|System decomposition (Canvas)]] — the laid-out module + data-flow diagram (present this)
- [[code-map]] — live table of every source file by module / layer / imports (query this)
- [[decisions]] — the design-reasoning log, queryable
- **Graph View** is now colour-grouped by module (data · backtest · agents · execution · infra)

## Onboarding

- [[CLAUDE]] — Single-source onboarding doc (lives at repo root)

## Out-of-vault references

These are still source-of-truth for module specs and rules but live under `.claude/rules/` so they're harder to backlink. Treat them as authoritative:

- `.claude/rules/modules/module1_data.md` · `module2_backtest.md` · `module3_agents.md` · `module4_execution.md`
- `.claude/rules/testing/calibration_tests.md` · `integration_tests.md` · `ablation_methodology.md`

## How this vault is wired

- Each MOC links **up** to `[[dashboard]]` and **across** to sibling MOCs.
- Each leaf file (decision, module, standard) ends with a `## Related` section linking to its MOC and any genuinely related sibling.
- Open **Graph View** to see the clusters; open the **Backlinks** pane on any file to see what references it.
