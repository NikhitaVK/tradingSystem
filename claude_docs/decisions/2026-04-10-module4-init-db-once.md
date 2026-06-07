# Decision: init_db() is called exactly once in main(), never in module constructors

**Date**: 2026-04-10

## Decision
`init_db()` is called once at the top of `src/main.py`. All other modules (`CCXTFeed`, agents, monitors) that call `get_connection()` before the DB exists must raise a clear `RuntimeError("Database not initialised — call init_db() first")` rather than allow SQLite to produce a cryptic file-not-found error.

## Reason
Repeated `init_db()` calls from multiple constructors makes initialisation order brittle and lets schema migrations re-run unnecessarily. Centralising it in `main()` makes startup deterministic and surfaces missing-DB errors with an actionable message instead of an opaque SQLite error.

## Alternatives Considered
- **Call init_db() in every constructor that touches DB** — rejected: order-dependent, hard to test in isolation
- **Lazy init on first connection** — rejected: hides ordering bugs and complicates teardown / fixture lifecycle


## Related

- MOC: [[execution]]
- [[data_pipeline]]
