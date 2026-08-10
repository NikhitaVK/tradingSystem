---
tags: [moc, standards-moc]
---

# Standards MOC

Coding and testing rules that apply across the project. Calibration / integration / ablation rule files live in `.claude/rules/testing/`.

**Up**: [[dashboard]]
**Across**: [[_architecture]] · [[_modules]] · [[_decisions]] · [[_tasks]] · [[_issues]] · [[_trials]] · [[_code]]

---

- [[coding_rules]] — Config over hardcoding, no circular imports, parameter conventions
- [[testing_rules]] — Isolation-first sequencing, mock strategy, synthetic data strategy

## Shaping decisions

- [[2026-05-11-parameterised-queries-only]] — SQL injection prevention rule
- [[2026-05-29-friendly-integrityerror-handling]] — User-friendly error handling
- [[2026-04-11-look-ahead-causal-truncation-test]] — Empirical look-ahead check
