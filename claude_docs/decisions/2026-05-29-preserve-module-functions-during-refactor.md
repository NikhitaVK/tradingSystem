# Decision: Preserve existing module-level functions during repository refactor

**Date**: 2026-05-29

## Decision

Keep the old function-style API working in parallel with the new class-based API so the existing agents continue to work.

## Reason

Agents in Loop 1 already import `write_finding`, `query_relevant`, etc. Breaking them mid-refactor would cascade into Module 3 test failures.

## Alternatives Considered

- **Full cutover to classes only** — rejected: would require touching every agent file and break Module 3 tests with no assessment benefit.


## Related

- MOC: [[data_pipeline]]
- [[2026-05-29-repository-pattern-refactor]]
