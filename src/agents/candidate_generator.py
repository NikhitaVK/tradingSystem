"""
candidate_generator.py — Deterministic strategy spec emitter.

Produces a mechanism-diverse pool of strategy specs balanced across 4 classes:
momentum, mean_reversion, volatility, breakout. Supports both long and short
directions. All specs use only indicators already supported by
strategy_runner.py (RSI, EMA, MACD, BB, ATR, ADX).

Design principles derived from empirical testing:
  - Crossover entries (crosses_above/crosses_below) produce higher-quality
    signals than state-based entries (>, <) which fire too frequently.
  - Pure SL/TP exits preserve the designed R:R ratio. Indicator-based exits
    truncate winners early, destroying the R:R advantage.
  - Wide stops (3%/9%) outperform tight stops (1.5%/4.5%) on 1h crypto.

Every emitted spec is DSL-valid and backtest-ready — no LLM involvement.
"""
from config.settings import CANDIDATE_POOL_SIZE

MECHANISM_CLASSES = (
    "momentum", "mean_reversion", "volatility", "breakout",
    "momentum_short", "mean_reversion_short", "breakdown",
)

# R:R variants — all satisfy the hard 3:1 contract (TP >= 2× SL).
_RR_TIGHT = (1.5, 4.5)   # tight: 3:1
_RR_WIDE = (3.0, 9.0)    # wide: 3:1 — better for 1h crypto noise
_RR_MID = (2.0, 6.0)     # mid: 3:1 — compromise between tight and wide


def _exit_block(sl: float, tp: float, indicator_conditions: list = None) -> dict:
    """Build a standard exit block with stop/TP + optional indicator exits."""
    conditions = list(indicator_conditions or [])
    conditions.append({"type": "stop_loss_pct", "value": sl})
    conditions.append({"type": "take_profit_pct", "value": tp})
    return {"logic": "OR", "conditions": conditions}


def generate_candidate_pool(
    regime: str,
    pair_dict: dict,
    kb_blacklist: list = None,
) -> list:
    """
    Emit a balanced pool of strategy specs for empirical search.

    Args:
        regime:       Current market regime label (e.g. 'sideways').
        pair_dict:    Dict with at least 'symbol' key (e.g. {"symbol": "BTC/USDT"}).
        kb_blacklist: List of mechanism names OR specific candidate names to
            skip (from prior failure diagnoses / rejected attempts).

    Returns:
        List of strategy spec dicts, each valid for engine.run_backtest().
    """
    symbol = pair_dict.get("symbol", "BTC/USDT")
    timeframe = pair_dict.get("timeframe", "1h")
    blacklist = set(kb_blacklist or [])

    all_candidates = []

    if "momentum" not in blacklist:
        all_candidates.extend(_momentum_candidates(symbol, timeframe))
    if "mean_reversion" not in blacklist:
        all_candidates.extend(_mean_reversion_candidates(symbol, timeframe))
    if "volatility" not in blacklist:
        all_candidates.extend(_volatility_candidates(symbol, timeframe))
    if "breakout" not in blacklist:
        all_candidates.extend(_breakout_candidates(symbol, timeframe))

    # Short-side candidates — mirror of long candidates with inverted signals.
    if "momentum_short" not in blacklist:
        all_candidates.extend(_momentum_short_candidates(symbol, timeframe))
    if "mean_reversion_short" not in blacklist:
        all_candidates.extend(_mean_reversion_short_candidates(symbol, timeframe))
    if "breakdown" not in blacklist:
        all_candidates.extend(_breakdown_candidates(symbol, timeframe))

    # Post-filter by candidate name — allows loop1 to retry with previously
    # rejected specs excluded without dropping their entire mechanism class.
    all_candidates = [c for c in all_candidates if c.get("name") not in blacklist]

    # Balance pool: interleave long and short so both directions get tested
    # even when CANDIDATE_POOL_SIZE caps the list.
    longs = [c for c in all_candidates if c.get("direction") != "short"]
    shorts = [c for c in all_candidates if c.get("direction") == "short"]
    balanced = []
    li, si = 0, 0
    while len(balanced) < len(all_candidates):
        if li < len(longs):
            balanced.append(longs[li]); li += 1
        if si < len(shorts):
            balanced.append(shorts[si]); si += 1
        if li >= len(longs) and si >= len(shorts):
            break

    return balanced[:CANDIDATE_POOL_SIZE]


def _momentum_candidates(symbol: str, tf: str) -> list:
    sl_w, tp_w = _RR_WIDE
    sl_m, tp_m = _RR_MID
    return [
        {
            # Fast crossover — more signals than EMA_50 variant
            "name": "EMA_10_20_Cross_PureSLTP_Wide",
            "symbol": symbol, "timeframe": tf, "mechanism": "momentum", "direction": "long",
            "indicators": [{"type": "EMA", "period": 10}, {"type": "EMA", "period": 20}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "EMA_10", "operator": "crosses_above", "value": "EMA_20"},
            ]},
            "exit": _exit_block(sl_w, tp_w),
        },
        {
            # Medium crossover + trend filter
            "name": "EMA_20_50_Cross_ADX_PureSLTP_Wide",
            "symbol": symbol, "timeframe": tf, "mechanism": "momentum", "direction": "long",
            "indicators": [
                {"type": "EMA", "period": 20}, {"type": "EMA", "period": 50},
                {"type": "ADX", "period": 14},
            ],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "EMA_20", "operator": "crosses_above", "value": "EMA_50"},
                {"indicator": "ADX_14", "operator": ">", "value": 20},
            ]},
            "exit": _exit_block(sl_w, tp_w),
        },
        {
            # MACD signal crossover — event-based, not state-based
            "name": "MACD_Cross_PureSLTP_Mid",
            "symbol": symbol, "timeframe": tf, "mechanism": "momentum", "direction": "long",
            "indicators": [{"type": "MACD"}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "MACD_12_26_9", "operator": "crosses_above", "value": "MACD_12_26_9_signal"},
            ]},
            "exit": _exit_block(sl_m, tp_m),
        },
    ]


def _mean_reversion_candidates(symbol: str, tf: str) -> list:
    sl_w, tp_w = _RR_WIDE
    sl_m, tp_m = _RR_MID
    return [
        {
            # BB lower band touch — pure SL/TP, let bounce play out
            "name": "BB_Lower_PureSLTP_Wide",
            "symbol": symbol, "timeframe": tf, "mechanism": "mean_reversion", "direction": "long",
            "indicators": [{"type": "BB", "period": 20}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "price", "operator": "<", "value": "BB_lower_20"},
            ]},
            "exit": _exit_block(sl_w, tp_w),
        },
        {
            # RSI oversold + EMA trend filter — pure SL/TP
            "name": "RSI_Oversold_EMA_PureSLTP_Wide",
            "symbol": symbol, "timeframe": tf, "mechanism": "mean_reversion", "direction": "long",
            "indicators": [{"type": "RSI", "period": 14}, {"type": "EMA", "period": 50}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "RSI_14", "operator": "<", "value": 30},
                {"indicator": "price", "operator": ">", "value": "EMA_50"},
            ]},
            "exit": _exit_block(sl_w, tp_w),
        },
        {
            # RSI oversold — indicator exit to BB mid, mid R:R
            "name": "RSI_BB_IndExit_Mid",
            "symbol": symbol, "timeframe": tf, "mechanism": "mean_reversion", "direction": "long",
            "indicators": [{"type": "RSI", "period": 14}, {"type": "BB", "period": 20}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "RSI_14", "operator": "<", "value": 30},
            ]},
            "exit": _exit_block(sl_m, tp_m, [
                {"indicator": "RSI_14", "operator": ">", "value": 70},
            ]),
        },
    ]


def _volatility_candidates(symbol: str, tf: str) -> list:
    sl_w, tp_w = _RR_WIDE
    sl_m, tp_m = _RR_MID
    return [
        {
            # EMA fast cross during trending — pure SL/TP
            "name": "EMA_10_50_Cross_PureSLTP_Wide",
            "symbol": symbol, "timeframe": tf, "mechanism": "volatility", "direction": "long",
            "indicators": [
                {"type": "EMA", "period": 10}, {"type": "EMA", "period": 50},
                {"type": "ATR", "period": 14},
            ],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "EMA_10", "operator": "crosses_above", "value": "EMA_50"},
            ]},
            "exit": _exit_block(sl_w, tp_w),
        },
        {
            # ADX rising + EMA alignment — pure SL/TP, mid R:R
            "name": "ADX_EMA_Cross_PureSLTP_Mid",
            "symbol": symbol, "timeframe": tf, "mechanism": "volatility", "direction": "long",
            "indicators": [
                {"type": "ADX", "period": 14},
                {"type": "EMA", "period": 20}, {"type": "EMA", "period": 50},
            ],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "ADX_14", "operator": ">", "value": 25},
                {"indicator": "EMA_20", "operator": "crosses_above", "value": "EMA_50"},
            ]},
            "exit": _exit_block(sl_m, tp_m),
        },
        {
            # MACD crossover + RSI momentum filter — pure SL/TP
            "name": "MACD_RSI_Cross_PureSLTP_Wide",
            "symbol": symbol, "timeframe": tf, "mechanism": "volatility", "direction": "long",
            "indicators": [{"type": "MACD"}, {"type": "RSI", "period": 14}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "MACD_12_26_9", "operator": "crosses_above", "value": "MACD_12_26_9_signal"},
                {"indicator": "RSI_14", "operator": ">", "value": 45},
            ]},
            "exit": _exit_block(sl_w, tp_w),
        },
    ]


def _breakout_candidates(symbol: str, tf: str) -> list:
    sl_w, tp_w = _RR_WIDE
    sl_m, tp_m = _RR_MID
    return [
        {
            # Proven best performer: EMA50 crossover + ADX confirmation
            "name": "EMA50_ADX_PureSLTP_Wide",
            "symbol": symbol, "timeframe": tf, "mechanism": "breakout", "direction": "long",
            "indicators": [{"type": "EMA", "period": 50}, {"type": "ADX", "period": 14}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "price", "operator": "crosses_above", "value": "EMA_50"},
                {"indicator": "ADX_14", "operator": ">", "value": 20},
            ]},
            "exit": _exit_block(sl_w, tp_w),
        },
        {
            # EMA50 crossover without ADX filter — more trades
            "name": "EMA50_Cross_PureSLTP_Mid",
            "symbol": symbol, "timeframe": tf, "mechanism": "breakout", "direction": "long",
            "indicators": [{"type": "EMA", "period": 50}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "price", "operator": "crosses_above", "value": "EMA_50"},
            ]},
            "exit": _exit_block(sl_m, tp_m),
        },
        {
            # MACD crossover + EMA breakout — pure SL/TP
            "name": "MACD_EMA_Cross_PureSLTP_Wide",
            "symbol": symbol, "timeframe": tf, "mechanism": "breakout", "direction": "long",
            "indicators": [{"type": "MACD"}, {"type": "EMA", "period": 20}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "MACD_12_26_9", "operator": "crosses_above", "value": "MACD_12_26_9_signal"},
                {"indicator": "price", "operator": ">", "value": "EMA_20"},
            ]},
            "exit": _exit_block(sl_w, tp_w),
        },
    ]


# ── Short-Side Candidates ───────────────────────────────────────────────────


def _momentum_short_candidates(symbol: str, tf: str) -> list:
    sl_w, tp_w = _RR_WIDE
    sl_m, tp_m = _RR_MID
    return [
        {
            # Fast EMA death cross — short when fast crosses below slow
            "name": "EMA_10_20_DeathCross_Short_Wide",
            "symbol": symbol, "timeframe": tf, "mechanism": "momentum_short", "direction": "short",
            "indicators": [{"type": "EMA", "period": 10}, {"type": "EMA", "period": 20}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "EMA_10", "operator": "crosses_below", "value": "EMA_20"},
            ]},
            "exit": _exit_block(sl_w, tp_w),
        },
        {
            # MACD signal death cross — short on bearish crossover
            "name": "MACD_DeathCross_Short_Mid",
            "symbol": symbol, "timeframe": tf, "mechanism": "momentum_short", "direction": "short",
            "indicators": [{"type": "MACD"}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "MACD_12_26_9", "operator": "crosses_below", "value": "MACD_12_26_9_signal"},
            ]},
            "exit": _exit_block(sl_m, tp_m),
        },
        {
            # EMA 20/50 death cross + ADX trending filter
            "name": "EMA_20_50_DeathCross_ADX_Short_Wide",
            "symbol": symbol, "timeframe": tf, "mechanism": "momentum_short", "direction": "short",
            "indicators": [
                {"type": "EMA", "period": 20}, {"type": "EMA", "period": 50},
                {"type": "ADX", "period": 14},
            ],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "EMA_20", "operator": "crosses_below", "value": "EMA_50"},
                {"indicator": "ADX_14", "operator": ">", "value": 20},
            ]},
            "exit": _exit_block(sl_w, tp_w),
        },
    ]


def _mean_reversion_short_candidates(symbol: str, tf: str) -> list:
    sl_w, tp_w = _RR_WIDE
    sl_m, tp_m = _RR_MID
    return [
        {
            # BB upper band touch — overbought, expect reversion down
            "name": "BB_Upper_Short_Wide",
            "symbol": symbol, "timeframe": tf, "mechanism": "mean_reversion_short", "direction": "short",
            "indicators": [{"type": "BB", "period": 20}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "price", "operator": ">", "value": "BB_upper_20"},
            ]},
            "exit": _exit_block(sl_w, tp_w),
        },
        {
            # RSI overbought + below EMA — bearish divergence
            "name": "RSI_Overbought_EMA_Short_Wide",
            "symbol": symbol, "timeframe": tf, "mechanism": "mean_reversion_short", "direction": "short",
            "indicators": [{"type": "RSI", "period": 14}, {"type": "EMA", "period": 50}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "RSI_14", "operator": ">", "value": 70},
                {"indicator": "price", "operator": "<", "value": "EMA_50"},
            ]},
            "exit": _exit_block(sl_w, tp_w),
        },
    ]


def _breakdown_candidates(symbol: str, tf: str) -> list:
    sl_w, tp_w = _RR_WIDE
    sl_m, tp_m = _RR_MID
    return [
        {
            # Price breaks below EMA50 + ADX confirms trend
            "name": "EMA50_ADX_Breakdown_Short_Wide",
            "symbol": symbol, "timeframe": tf, "mechanism": "breakdown", "direction": "short",
            "indicators": [{"type": "EMA", "period": 50}, {"type": "ADX", "period": 14}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "price", "operator": "crosses_below", "value": "EMA_50"},
                {"indicator": "ADX_14", "operator": ">", "value": 20},
            ]},
            "exit": _exit_block(sl_w, tp_w),
        },
        {
            # MACD death cross + price below EMA — confirmed breakdown
            "name": "MACD_EMA_Breakdown_Short_Wide",
            "symbol": symbol, "timeframe": tf, "mechanism": "breakdown", "direction": "short",
            "indicators": [{"type": "MACD"}, {"type": "EMA", "period": 20}],
            "entry": {"logic": "AND", "conditions": [
                {"indicator": "MACD_12_26_9", "operator": "crosses_below", "value": "MACD_12_26_9_signal"},
                {"indicator": "price", "operator": "<", "value": "EMA_20"},
            ]},
            "exit": _exit_block(sl_w, tp_w),
        },
    ]
