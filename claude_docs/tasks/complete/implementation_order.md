# Implementation Order

```
Phase 1 (HMM Regime Detection)  → engine.py, new hmm_regime.py, indicators.py, test_backtest.py
        ↓
Phase 2 (Regime-Aware KB)      → schema.py, knowledge_base.py, loop1.py, analyst_agent.py
        ↓
Phase 3 (Regime Match Pre-Check) → tools.py, strategy_agent.py, prompts/, test_loop1.py
        ↓
Phase 4 (Evolution Tracking)    → schema.py, loop1.py, test_loop1.py
        ↓
Phase 5 (Semantic KB Retrieval)  → knowledge_base.py, tools.py, test_knowledge_base.py
        ↓
Phase 6 (Layered Memory)        → memory_layers.py (new), knowledge_base.py, loop1.py, test_knowledge_base.py
        ↓
Phase 7 (Working Memory)        → knowledge_base.py, loop1.py, strategy_agent.py, prompts/, test_knowledge_base.py
        ↓
Phase 8 (Strategy Template Library) → new template file, strategy_agent.py, prompts/, test_loop1.py
        ↓
Phase 9 (Multi-Timeframe Confirmation) → new mtf module, strategy_runner.py, test_backtest.py
        ↓
Phase 10 (Adversarial Regime Robustness in CP1) → analyst prompt, test_loop1.py
```


## Related

- MOC: [[_tasks]]
- [[phase_00_documentation_discovery]]
