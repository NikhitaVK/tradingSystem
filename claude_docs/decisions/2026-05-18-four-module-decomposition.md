# Decision: Decompose the trading system into 4 modules with build-order dependency

**Date**: 2026-05-18

## Decision

Formalise the project decomposition table as Module 1 (Data Pipeline) → Module 2 (Backtest) → Module 3 (Agents/Loop 1) → Module 4 (Execution/Loop 2), with each module's test file gating the next.

## Reason

Provides clear project-management decomposition for the PM standard, and matches the strict isolation-test sequencing rule already established in `.claude/rules/testing/ablation_methodology.md`.

## Alternatives Considered

None recorded.


## Related

- MOC: [[_architecture]]
- [[overview]]
