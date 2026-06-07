# Decision: Two-loop architecture with debate checkpoints at high-risk decision points

**Date**: 2026-04-10

## Decision
Adopt a two-loop architecture (Loop 1 = strategy discovery, Loop 2 = live execution) with an adversarial "analyst challenge" debate checkpoint inserted only at the two highest-risk decisions: pre-backtest hypothesis quality (CP1) and pre-trade execution conditions (CP2).

## Reason
A pure pipeline is simplest to build and debug; a fully multi-agent debate system has high coordination overhead. Inserting collaborative debate only where the cost of a wrong decision is highest (a bad strategy entering paper trading, or a bad trade hitting the exchange) buys most of the value of multi-agent reasoning without the overhead.

## Alternatives Considered
- **Single monolithic Claude agent driving the whole pipeline** — rejected: no separation of concerns, no internal stress test of decisions
- **Full multi-agent debate at every stage** — rejected: coordination cost outweighs marginal benefit at low-risk stages


## Related

- MOC: [[agents]]
- [[execution]]
- [[2026-04-20-three-way-verdict-composite-score]]
