# Decision: Scope AS91906 coding standard submission to the database layer (+ GUI)

**Date**: 2026-05-11

## Decision

Use only `schema.py`, `knowledge_base.py`, `ingestor.py`, plus a new GUI as the AS91906 deliverable. Keep the full trading system as the Project Management standard's scope.

## Reason

The user must personally defend every line under NZQA moderation. The database layer is ~500 lines of explainable code; the full AI-built system is too large and risky to defend at moderation.

## Alternatives Considered

- **Submit the whole trading system for AS91906** — rejected: NZQA moderation exposure if the student cannot justify architectural choices (e.g. why WAL mode, why recency-ordered KB).
- **Submit only database with no GUI** — rejected: assessment requires a "program" not a "library"; needs an interactive layer.


## Related

- MOC: [[data_pipeline]]
- [[2026-05-11-pathway-a-plus-c]]
- [[2026-05-11-db-layer-update-delete-join]]
