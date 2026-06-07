# Decision: GaussianHMM-based regime classifier replaces simple _tag_regime

**Date**: 2026-04-15

## Decision
Introduce `src/backtest/hmm_regime.py` using `hmmlearn.GaussianHMM` with a 4-label schema (replacing the prior 3-label bull/bear/sideways `_tag_regime` heuristic). The HMM output drives `loop1._detect_current_regime()` for KB queries and is injected into the strategy agent prompt as a regime playbook.

## Reason
The prior heuristic regime tag was a simple rolling-return cut. Crypto regimes are better modelled as latent states with serial correlation. BOCPD/HMM are the standard tools in the 2024-2025 literature; GaussianHMM is well-supported via `hmmlearn`. The 4-label schema aligns with the Phase 8 template registry and Phase 2/3 KB schema so a single regime label flows end-to-end.

## Alternatives Considered
- **BOCPD (Bayesian online changepoint detection)** — considered superior but more complex; deferred
- **PELT changepoint** — rejected: offline only, doesn't fit live monitoring
- **Keep heuristic _tag_regime** — rejected: too coarse for regime-conditional KB retrieval and template selection


## Related

- MOC: [[backtesting]]
- [[phase_01_hmm_regime_detection]]
- [[2026-04-15-finmem-layered-memory]]
