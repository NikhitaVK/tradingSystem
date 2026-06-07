# Decision: Analyst v2 — composite score with three-way verdict (pass / probation / fail)

**Date**: 2026-04-20

## Decision
Replace the binary pass/fail analyst evaluator with `analyst_eval_v2.txt`: a weighted composite score (0.0-1.0) across five criteria and a three-tier verdict — `pass` (>= 0.70), `probation` (0.50-0.70), `fail` (< 0.50).

## Reason
The v1 prompt had hard AND-gated thresholds and adversarial framing; it rejected 100% of Loop 1 candidates in practice. A weighted composite lets a strategy that is strong on 4 of 5 criteria still pass, and the probation tier gives borderline strategies a path to live-market validation at reduced size instead of being thrown away.

## Alternatives Considered
- **Soften individual v1 thresholds** — rejected: doesn't fix the AND-gate fundamental problem
- **Remove adversarial framing entirely** — rejected: lose the stress-testing benefit; v2 keeps adversarial framing but reframes it as honest stress-testing


## Related

- MOC: [[agents]]
- [[2026-04-20-probationary-tier]]
