---
tags: [adr, risk, execution, retroactive]
related: ["[[_decisions]]", "[[2026-04-10-two-loop-debate-checkpoints]]", "[[execution]]", "[[2026-04-20-probationary-tier]]"]
---

# Decision: The risk agent is deterministic arithmetic — no LLM in the risk path

**Date**: 2026-08-09 (retroactive — decision made during Module 4 design, previously
recorded only in `.claude/rules/modules/module4_execution.md`)

## Decision

`RiskAgent.review()` is a pure arithmetic class. Claude is not called. Position
sizing, concurrency limits, and the daily-loss halt are formulaic:

```
MAX_POSITION_PCT = 0.05   # adjust oversized positions down, approve
MAX_CONCURRENT   = 3      # reject beyond
MAX_DAILY_LOSS   = 0.03   # reject all new trades below this
```

This is scoped narrowly: it excludes an LLM from the **risk arithmetic**, not from
the trade path. Debate CP2 calls `analyst.evaluate_brief()` immediately before
execution at a 2000-token thinking budget. The analyst is not called if the risk
agent already rejected the trade.

## Reason

1. **Latency.** The risk check sits between signal detection and order placement.
   An API round trip there is seconds of slippage on every trade.
2. **There is no judgment to add.** "Max 5% of balance", "max 3 concurrent",
   "halt below −3% daily" have one correct answer. An LLM can only introduce
   variance into a calculation that is not uncertain.
3. **Auditability.** A limit that provably always holds beats one that usually
   holds. A risk agent that occasionally approves 7% is worse than one that always
   clamps to 5%, even if it is right more often on average.

This follows the same principle as [[2026-04-10-two-loop-debate-checkpoints]]:
spend LLM reasoning only where the cost of a wrong decision is high *and* the
decision is genuinely uncertain. Position-size arithmetic fails the second test.

## Alternatives Considered

- **LLM risk agent with full context** — rejected on all three grounds above.
- **LLM as an advisory layer over the arithmetic** — rejected: this is what CP2
  already is, one step later, where the analyst sees the proposed trade and can
  veto it. Adding a second advisory call inside the risk agent duplicates it.
- **LLM only for edge cases (near a limit)** — rejected: creates a latency cliff
  exactly at the moments that matter most, and a bimodal system that is harder to
  audit than either pure option.

## Note on FinMem

FinMem's profiling module (risk inclination: self-adaptive / risk-seeking /
risk-averse) is sometimes read as an LLM in the risk path. It is not a risk
*calculator* — it is a disposition that shapes which memories are retrieved and
how the investment decision is framed. In our architecture that maps onto **Loop 1
strategy selection**, which is already an LLM call, not onto the Loop 2 risk agent.
Adopting it would not conflict with this decision.

## Related

- MOC: [[_decisions]] · [[execution]]
- Sibling: [[2026-04-10-two-loop-debate-checkpoints]] (where LLM reasoning *is* spent)
- Interacts with [[2026-04-20-probationary-tier]] (size multiplier applied before review)
- Spec: `.claude/rules/modules/module4_execution.md`
