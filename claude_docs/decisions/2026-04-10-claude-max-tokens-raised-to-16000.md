---
tags: [decision, module-3, claude-api, context-budget]
related: ["[[_decisions]]", "[[agents]]", "[[2026-04-11-thinking-block-preservation]]"]
---

# Decision: Raise `CLAUDE_MAX_TOKENS` from 12 000 to 16 000

**Date**: 2026-04-10

## Decision

Increase `CLAUDE_MAX_TOKENS` in `config/settings.py` from `12000` to `16000`.

## Reason

Extended thinking consumes tokens from the same `max_tokens` budget as the visible response. With `THINKING_BUDGET_STRATEGY = 8000`, the old `12000` ceiling left only `4000` tokens for the actual reply — not enough room for a full strategy spec plus tool calls, causing mid-response truncation. Raising to `16000` doubles the visible-response headroom to `8000` while keeping the same thinking budget.

## Alternatives Considered

- **Lower the thinking budget instead** — rejected: 8 000 thinking tokens is what the strategy agent needs to reason about indicator combinations; cutting it produced shallower hypotheses.
- **Stream and stitch multiple responses** — rejected: adds state-management complexity and breaks the single-call-per-turn contract in `claude_client.chat()`.

## Related

- MOC: [[_decisions]]
- Affects: [[agents]] (Module 3)
- Companion rule: [[2026-04-11-thinking-block-preservation]]
