"""
registry.py — Strategy template library organised by market regime (Phase 8).

Templates are concise mechanism descriptions — NOT full strategy specs.
They give the strategy agent a validated starting point for each regime,
grounded in known market mechanisms, rather than requiring it to reason
from scratch every cycle.

The agent reads the playbook for the current regime and uses it to generate
a hypothesis. It is not obliged to follow a template — it can deviate when
the KB evidence suggests a different mechanism is more promising.
"""

# ── Regime template playbook ──────────────────────────────────────────────────
#
# Each entry describes:
#   mechanism   — the economic inefficiency being exploited
#   indicators  — which indicators most directly measure this mechanism
#   entry_hint  — the directional signal
#   exit_hint   — when the mechanism is exhausted
#   failure_modes — known conditions under which this template breaks down
#
REGIME_TEMPLATES = {
    "trending_bull": [
        {
            "name": "EMA Momentum Breakout",
            "mechanism": "momentum",
            "description": (
                "In a sustained bull trend, dips to the short EMA attract buyers "
                "because institutional participants accumulate on weakness. "
                "The signal: price crossing back above EMA_20 after a pullback, "
                "confirmed by ADX > 20 (trend is real, not a chop)."
            ),
            "indicators": ["EMA_20", "EMA_50", "ADX_14"],
            "entry_hint": "price crosses above EMA_20 with ADX > 20",
            "exit_hint": "price closes below EMA_50 or RSI > 75",
            "failure_modes": ["regime shifts to sideways (ADX drops < 20)", "macro shock reversal"],
        },
        {
            "name": "MACD Trend Continuation",
            "mechanism": "momentum",
            "description": (
                "MACD histogram sign change from negative to positive signals "
                "that the short-term momentum has re-aligned with the primary uptrend. "
                "Works best when price is above EMA_200, confirming primary trend."
            ),
            "indicators": ["MACD_12_26_9", "EMA_50"],
            "entry_hint": "MACD histogram crosses above zero while price > EMA_50",
            "exit_hint": "MACD histogram crosses below zero",
            "failure_modes": ["bull trap at resistance", "low volume breakout that fades"],
        },
    ],

    "trending_bear": [
        {
            "name": "RSI Overbought Short",
            "mechanism": "mean_reversion",
            "description": (
                "In a downtrend, relief rallies to RSI > 65 are selling opportunities. "
                "Overleveraged longs who bought the dip are forced to exit when price "
                "fails to hold, triggering cascading stop-loss sells."
            ),
            "indicators": ["RSI_14", "EMA_20"],
            "entry_hint": "RSI crosses below 65 from above while price < EMA_20",
            "exit_hint": "RSI < 35 or price breaks above EMA_20",
            "failure_modes": ["genuine trend reversal (bear trap)", "macro catalyst (news)"],
        },
        {
            "name": "Bollinger Band Upper Touch",
            "mechanism": "mean_reversion",
            "description": (
                "In a bear trend, touches of the upper Bollinger Band are exhaustion "
                "signals — buyers have temporarily overwhelmed sellers, but the structural "
                "imbalance (more sellers) reasserts. Trade the return to mid-band."
            ),
            "indicators": ["BB_20", "ATR_14"],
            "entry_hint": "close touches BB upper band with declining volume",
            "exit_hint": "close reaches BB mid band or ATR-based stop",
            "failure_modes": ["BB squeeze breakout (trending, not mean-reverting)", "news catalyst"],
        },
    ],

    "sideways": [
        {
            "name": "RSI Mean Reversion",
            "mechanism": "mean_reversion",
            "description": (
                "In range-bound conditions, price oscillates between support and resistance. "
                "RSI < 30 identifies oversold retail panic sellers who are overreacting to "
                "normal volatility. The mechanism: market makers absorb supply and push "
                "price back to fair value (mid-range)."
            ),
            "indicators": ["RSI_14", "BB_20"],
            "entry_hint": "RSI < 30 near BB lower band",
            "exit_hint": "RSI > 65 or close above BB mid band",
            "failure_modes": ["range breaks down into trend (RSI can stay < 30 for many bars)", "low liquidity"],
        },
        {
            "name": "Bollinger Band Squeeze Fade",
            "mechanism": "mean_reversion",
            "description": (
                "Bollinger Band width narrows during low-volatility consolidation. "
                "Price oscillates inside the bands. Trade fades at the bands, "
                "targeting the midline. Exit before a potential squeeze breakout."
            ),
            "indicators": ["BB_20", "RSI_14"],
            "entry_hint": "close touches BB lower with RSI < 40 (long) or upper with RSI > 60 (short)",
            "exit_hint": "close reaches BB midline or stop at band breach + 0.5 ATR",
            "failure_modes": ["BB squeeze breakout invalidates the range", "macro event"],
        },
    ],

    "high_vol": [
        {
            "name": "ATR Volatility Reversion",
            "mechanism": "volatility",
            "description": (
                "During high-volatility regimes, large single-bar moves are frequently "
                "caused by liquidation cascades and are partially mean-reverting within "
                "1-3 bars. ATR expands sharply; fading the move captures the return "
                "to the mean. Requires tight stops and small position sizing."
            ),
            "indicators": ["ATR_14", "RSI_14"],
            "entry_hint": "bar range > 2.5x ATR_14 with RSI at extreme (< 25 or > 75)",
            "exit_hint": "RSI returns to 45-55 range or 1.5x ATR stop from entry",
            "failure_modes": ["genuine breakdown (liquidation cascade, not a fade)", "low-liquidity gap continuation"],
        },
    ],
}

# Fallback for unknown regimes
_FALLBACK_TEMPLATES = [
    {
        "name": "RSI Mean Reversion (General)",
        "mechanism": "mean_reversion",
        "description": "Generic RSI oversold/overbought strategy as a baseline.",
        "indicators": ["RSI_14"],
        "entry_hint": "RSI < 30",
        "exit_hint": "RSI > 65 or stop-loss",
        "failure_modes": ["trending markets", "liquidity gaps"],
    }
]


def get_templates_for_regime(regime: str) -> list:
    """
    Return the list of strategy templates for the given regime label.
    Falls back to a generic template if the regime is unknown.

    Args:
        regime: Regime label, e.g. 'trending_bull', 'sideways', 'high_vol'.

    Returns:
        List of template dicts.
    """
    return REGIME_TEMPLATES.get(regime, _FALLBACK_TEMPLATES)


def get_templates_by_mechanism(mechanism: str) -> list:
    """
    Return all templates across all regimes that match the given mechanism.

    Args:
        mechanism: e.g. 'mean_reversion', 'momentum', 'volatility'.

    Returns:
        List of (regime, template) tuples.
    """
    results = []
    for regime, templates in REGIME_TEMPLATES.items():
        for t in templates:
            if t.get("mechanism") == mechanism:
                results.append((regime, t))
    return results
