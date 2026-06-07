# Decision: SIGTERM/SIGINT shutdown handler marks open trades as 'interrupted'

**Date**: 2026-04-10

## Decision
`src/main.py` registers SIGTERM and SIGINT handlers that set a stop event; on shutdown, all rows in `trades` with `outcome='open'` are updated to `outcome='interrupted'` so the next startup can distinguish them from genuinely-open positions.

## Reason
Without a distinction, a restarted system has no safe way to tell "this trade is still live on the exchange" from "this trade's process died mid-execution." Tagging interrupted explicitly allows the reconciler to query the exchange and resolve each one deliberately rather than silently treating them as live.

## Alternatives Considered
- **Treat all 'open' as live, reconcile on every startup** — rejected: expensive, and ignores semantic difference
- **Hard-close on shutdown** — rejected: cannot guarantee the close order actually reaches the exchange before process exit


## Related

- MOC: [[execution]]
- [[2026-04-10-module4-init-db-once]]
