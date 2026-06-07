# Decision: Probationary deployment tier with halved sizing and tighter monitoring

**Date**: 2026-04-20

## Decision
Strategies that receive an analyst verdict of `probation` (composite score 0.50-0.70) are deployed live with `PROBATION_SIZE_MULTIPLIER = 0.5` (halved position size), `PROBATION_THRESHOLD_BUMP = +0.05` (tighter degradation trigger), halved stale-strategy timeout, and explicit promote/demote counters (`PROBATION_PROMOTE_WINS`, `PROBATION_DEMOTE_LOSSES`) that auto-transition status.

## Reason
Pattern borrowed from QuantConnect Alpha Streams: marginal strategies should be live-validated at reduced risk rather than discarded. Halved size caps downside while still generating real-money evidence; tighter threshold + faster stale check ensures probation strategies that don't justify themselves are removed promptly. Status is re-queried per trade so promotion takes effect mid-session.

## Alternatives Considered
- **Binary deploy / discard** — rejected: throws away borderline strategies that may be genuinely viable
- **Full-size deployment with tighter monitoring only** — rejected: doesn't cap downside if the analyst is wrong


## Related

- MOC: [[agents]]
- [[execution]]
- [[2026-04-20-three-way-verdict-composite-score]]
