# Decision: Use tkinter for the GUI layer

**Date**: 2026-05-29

## Decision

Build the GUI as `src/gui/app.py` using tkinter, with three tabs (Knowledge Base, Strategies, Summary).

## Reason

tkinter ships with Python so no extra dependency, satisfies the assessment's "user input + output" requirement, and the three-tab structure cleanly demonstrates SELECT/INSERT/UPDATE/DELETE + JOIN + GROUP BY in separate visible surfaces.

## Alternatives Considered

- **PyQt / web frontend** — rejected by omission: extra install burden, and tkinter is sufficient for the assessment.


## Related

- MOC: [[data_pipeline]]
- [[2026-05-29-repository-pattern-refactor]]
