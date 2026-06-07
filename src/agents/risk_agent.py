"""
risk_agent.py — Deterministic risk gating for Loop 2 trade approval.

Pure arithmetic — no Claude calls, no I/O. Evaluation short-circuits on
the first rejection so later checks are never reached.

Evaluation order (from Freqtrade protection research):
  1. StoplossGuard: N consecutive losses → cooldown
  2. Daily loss circuit breaker
  3. Concurrent position limit
  4. Position size cap (adjust down, don't reject)
"""
from __future__ import annotations

import time
import logging

from config.settings import (
    RISK_MAX_POSITION_PCT,
    RISK_MAX_CONCURRENT,
    RISK_MAX_DAILY_LOSS,
    STOPLOSS_GUARD_CONSECUTIVE,
    STOPLOSS_GUARD_COOLDOWN_MINUTES,
)

logger = logging.getLogger(__name__)


class RiskAgent:
    def __init__(
        self,
        max_position_pct: float = RISK_MAX_POSITION_PCT,
        max_concurrent: int = RISK_MAX_CONCURRENT,
        max_daily_loss: float = RISK_MAX_DAILY_LOSS,
        stoploss_guard_consecutive: int = STOPLOSS_GUARD_CONSECUTIVE,
        stoploss_guard_cooldown_min: int = STOPLOSS_GUARD_COOLDOWN_MINUTES,
    ):
        self.max_position_pct = max_position_pct
        self.max_concurrent = max_concurrent
        self.max_daily_loss = max_daily_loss
        self.stoploss_guard_consecutive = stoploss_guard_consecutive
        self.stoploss_guard_cooldown_min = stoploss_guard_cooldown_min
        self._cooldown_until: float = 0.0  # Unix timestamp when cooldown expires

    def review(
        self,
        proposed_size_usdt: float,
        balance_usdt: float,
        open_positions: int,
        daily_pnl_pct: float,
        recent_outcomes: list | None = None,
    ) -> dict:
        """
        Gate a proposed trade through deterministic risk checks.

        Args:
            proposed_size_usdt:  Desired position size in USDT.
            balance_usdt:        Current free USDT balance.
            open_positions:      Count of currently open trades.
            daily_pnl_pct:       Today's realised PnL as a decimal (e.g. -0.04 = -4%).
            recent_outcomes:     List of recent trade outcomes newest-first,
                                 e.g. ['loss', 'loss', 'win', ...]. Used for StoplossGuard.

        Returns:
            {'approved': bool, 'adjusted_size': float, 'reason': str}
        """
        # Sanity: reject obviously invalid inputs.
        if balance_usdt <= 0:
            return self._reject("Balance is zero or negative")
        if proposed_size_usdt <= 0:
            return self._reject("Proposed size is zero or negative")

        # 1. StoplossGuard — consecutive loss streak detection.
        if recent_outcomes is not None:
            tail = recent_outcomes[: self.stoploss_guard_consecutive]
            if (
                len(tail) >= self.stoploss_guard_consecutive
                and all(o == "loss" for o in tail)
            ):
                self._cooldown_until = (
                    time.time() + self.stoploss_guard_cooldown_min * 60
                )
                return self._reject(
                    f"StoplossGuard: {self.stoploss_guard_consecutive} consecutive "
                    f"losses — cooldown {self.stoploss_guard_cooldown_min}min"
                )

        # StoplossGuard cooldown still active from a prior trigger.
        if time.time() < self._cooldown_until:
            remaining = int((self._cooldown_until - time.time()) / 60) + 1
            return self._reject(
                f"StoplossGuard cooldown active — {remaining}min remaining"
            )

        # 2. Daily loss circuit breaker.
        if daily_pnl_pct < -self.max_daily_loss:
            return self._reject(
                f"Daily loss limit breached: {daily_pnl_pct:.2%} "
                f"< -{self.max_daily_loss:.2%}"
            )

        # 3. Concurrent position limit.
        if open_positions >= self.max_concurrent:
            return self._reject(
                f"Max concurrent positions reached: "
                f"{open_positions} >= {self.max_concurrent}"
            )

        # 4. Position size cap — adjust down rather than reject.
        max_size = balance_usdt * self.max_position_pct
        adjusted = min(proposed_size_usdt, max_size)

        reason = "approved"
        if adjusted < proposed_size_usdt:
            reason = (
                f"Position capped: {proposed_size_usdt:.2f} → {adjusted:.2f} "
                f"({self.max_position_pct:.0%} of {balance_usdt:.2f})"
            )
            logger.info("RiskAgent: %s", reason)

        return {"approved": True, "adjusted_size": adjusted, "reason": reason}

    # ------------------------------------------------------------------
    @staticmethod
    def _reject(reason: str) -> dict:
        logger.info("RiskAgent REJECTED: %s", reason)
        return {"approved": False, "adjusted_size": 0.0, "reason": reason}
