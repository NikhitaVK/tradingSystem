# Decision: Refactor `knowledge_base.py` from module functions into a Repository class

**Date**: 2026-05-29

## Decision

Introduce `KnowledgeBaseRepository` and `StrategyRepository` classes that wrap all SQL, with the GUI calling only repository methods (no SQL in widgets).

## Reason

The AS91906 SQL student tutorial flags the repository pattern as the single most valuable Excellence habit — keeps SQL out of the interface layer and provides a clean OOP demonstration.

## Alternatives Considered

- **Keep module-level functions** — rejected: tutorial explicitly identifies this as the Excellence differentiator, and the GUI build needs a clean abstraction boundary.


## Related

- MOC: [[data_pipeline]]
- [[2026-05-29-preserve-module-functions-during-refactor]]
- [[2026-05-29-tkinter-gui]]
