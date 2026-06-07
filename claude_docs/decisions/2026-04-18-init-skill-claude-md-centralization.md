---
tags: [decision, project-context, claude-md, onboarding]
related: ["[[_decisions]]", "[[2026-04-13-structured-context-system]]", "[[_architecture]]"]
---

# Decision: Centralise onboarding into `CLAUDE.md` via the `/init` skill

**Date**: 2026-04-18

## Decision

Run the `/init` skill once to produce a single authoritative `CLAUDE.md` at the repo root, replacing the prior scatter of onboarding notes across `.claude/PROJECT_CONTEXT.md`, `.claude/PLANNED_IMPROVEMENTS.md`, and ad-hoc README fragments.

## Reason

Every new session was re-loading several large overlapping documents to reach the same baseline understanding (project purpose, two-loop architecture, module status, key gotchas). One canonical file means lower per-session context cost, no drift between sources, and a stable entry point that pairs cleanly with the [[2026-04-13-structured-context-system|layered claude_docs]] structure for deeper detail.

## Alternatives Considered

- **Keep multiple onboarding files and link from CLAUDE.md** — rejected: drift was the original problem; having one source of truth eliminates the "which version is current" question.
- **Generate CLAUDE.md from `claude_docs/`** — rejected as premature: no automation yet exists, and the hand-curated file is short enough that drift risk is low if updates land alongside architectural changes.

## Related

- MOC: [[_decisions]]
- Builds on: [[2026-04-13-structured-context-system]]
- Architecture: [[_architecture]]
