# Decision: Load versioned prompt files at module import time, never inline

**Date**: 2026-04-11

## Decision
Prompts live in `prompts/*.txt` and are loaded into module-level constants at import time via `Path(...).read_text()`. They are never embedded inline as Python string literals and never loaded inside the agent function.

## Reason
Loading at import time fails fast on startup if a prompt file is missing or unreadable — surfacing the problem before any pair screening or backtesting has been done. Versioned `.txt` files also enable controlled prompt A/B testing (v1 vs v2) and clean diffing in git.

## Alternatives Considered
- **Inline prompt strings** — rejected: no versioning, no separate diff history, encourages drift
- **Lazy-load inside function** — rejected: missing-file errors only surface mid-run after expensive setup
- **Database-backed prompts** — rejected: overkill for a single-developer system, breaks reproducibility from git checkout


## Related

- MOC: [[agents]]
- [[coding_rules]]
