"""
strategies.py — Repository class for the strategies and trades tables.

Demonstrates JOIN queries that combine data from two related tables,
and GROUP BY aggregates that summarise trade outcomes per strategy.

All SQL is kept inside StrategyRepository.
The interface layer (src/gui/app.py) never contains SQL — it only calls these methods.
"""
import logging
import sqlite3

from src.data.schema import get_connection

logger = logging.getLogger(__name__)

VALID_STATUSES = {"active", "degraded", "archived", "probation"}


class StrategyRepository:
    """Repository for strategy and trade queries.

    The strategies and trades tables are linked by a foreign key:
        trades.strategy_id  →  strategies.id

    JOIN queries use that link to pull data from both tables at once.
    """

    def __init__(self, db_path: str):
        """Initialise the repository with the path to the SQLite database file."""
        self.db_path = db_path

    # ── READ — single table ───────────────────────────────────────────────────

    def get_all_strategies(self) -> list:
        """Return all strategies ordered by creation date, newest first."""
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT id, name, status, created_at, degradation_threshold
                FROM strategies
                ORDER BY created_at DESC
                """
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    def get_strategy_by_id(self, strategy_id: int) -> dict:
        """Return a single strategy by its id, or None if not found."""
        conn = get_connection(self.db_path)
        try:
            row = conn.execute(
                "SELECT * FROM strategies WHERE id = ?",
                (strategy_id,),
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    # ── READ — JOIN queries ───────────────────────────────────────────────────

    def get_strategies_with_trade_summary(self) -> list:
        """Return every strategy with a summary of its trade outcomes.

        A JOIN query combining strategies with trades on the foreign key, plus
        GROUP BY so COUNT/SUM compute per-strategy totals in one query. LEFT JOIN
        is used so strategies with no trades still appear (with 0 values).

        Returns dicts with keys:
            strategy_id, name, status, total_trades, wins, losses, win_rate
        """
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT
                    s.id    AS strategy_id,
                    s.name  AS name,
                    s.status AS status,
                    COALESCE(COUNT(t.id), 0) AS total_trades,
                    COALESCE(SUM(CASE WHEN t.outcome = 'win'  THEN 1 ELSE 0 END), 0) AS wins,
                    COALESCE(SUM(CASE WHEN t.outcome = 'loss' THEN 1 ELSE 0 END), 0) AS losses
                FROM strategies s
                LEFT JOIN trades t ON t.strategy_id = s.id
                GROUP BY s.id, s.name, s.status
                ORDER BY s.created_at DESC
                """
            ).fetchall()
        finally:
            conn.close()

        results = []
        for row in rows:
            d = dict(row)
            total = d["total_trades"]
            d["win_rate"] = round(d["wins"] / total, 2) if total > 0 else 0.0
            results.append(d)
        return results

    def get_trades_for_strategy(self, strategy_id: int) -> list:
        """Return all trades for one strategy, joined with the strategy name.

        Uses INNER JOIN so each trade row includes the strategy name without a
        second query. Returns dicts with keys: trade_id, strategy_name, symbol,
        side, entry_price, exit_price, amount_usdt, pnl_pct, outcome,
        entry_at, exit_at.
        """
        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(
                """
                SELECT
                    t.id          AS trade_id,
                    s.name        AS strategy_name,
                    t.symbol,
                    t.side,
                    t.entry_price,
                    t.exit_price,
                    t.amount_usdt,
                    t.pnl_pct,
                    t.outcome,
                    t.entry_at,
                    t.exit_at
                FROM trades t
                JOIN strategies s ON t.strategy_id = s.id
                WHERE t.strategy_id = ?
                ORDER BY t.entry_at DESC
                """,
                (strategy_id,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(row) for row in rows]

    # ── UPDATE ────────────────────────────────────────────────────────────────

    def update_strategy_status(self, strategy_id: int, status: str) -> bool:
        """Update a strategy's status by id. Returns False if id not found.

        Raises ValueError for an invalid status or a constraint violation.
        """
        if status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{status}'. Must be one of: {sorted(VALID_STATUSES)}"
            )

        conn = get_connection(self.db_path)
        try:
            with conn:
                cursor = conn.execute(
                    "UPDATE strategies SET status = ? WHERE id = ?",
                    (status, strategy_id),
                )
                updated = cursor.rowcount > 0
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Could not update strategy: {exc}") from exc
        finally:
            conn.close()

        if not updated:
            logger.warning("update_strategy_status: no strategy with id=%d", strategy_id)
        return updated
