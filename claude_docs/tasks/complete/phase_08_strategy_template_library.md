# Phase 8 — Strategy Template Library by Regime

**What**: Replace "strategy agent hypothesises from scratch each cycle" with a structured playbook of strategy templates organised by regime. The agent retrieves the relevant template family, then adapts it rather than inventing it. This mirrors how a human trader operates: observe the market → classify the regime → retrieve the playbook → execute.

**Why this matters**: A human trader does not invent trend-following on a whim. They have a playbook ("in a trending market, use momentum oscillators and MA crossovers"). Your system asks the agent to derive this from first principles every cycle, which wastes token budget and produces inconsistent results. A template library gives the agent a head start aligned with how real traders think.

## Step 8.1 — Create `src/strategy_templates/registry.py`

**File to create**: `src/strategy_templates/registry.py`

**Reference**: Pattern from `src/backtest/indicators.py` for pure functions with no side effects.

```python
"""
registry.py — Strategy template library organised by market regime.

Human traders maintain a mental playbook. This module makes it explicit
and queryable by the strategy agent.

Templates are NOT strategy specs — they are structural patterns with
indicator families and entry logic described in natural language.
The agent adapts them to the specific pair and current conditions.
"""

REGIME_TEMPLATES = {
    "trending_bull": [
        {
            "id": "momentum_breakout",
            "name": "Momentum Breakout",
            "mechanism": "momentum",
            "description": (
                "Enter when price breaks above a recent swing high AND a momentum "
                "oscillator (RSI or MACD histogram) confirms the move. "
                "Exit on RSI overbought or trailing ATR stop. "
                "Works when price is in a clear uptrend with higher highs and higher lows."
            ),
            "indicator_families": ["RSI", "MACD", "EMA", "ATR"],
            "entry_conditions_template": "price breaks above {swing_high_lookback} bar high AND RSI > {rsi_buy_threshold}",
            "regime_tags": ["trending_bull", "trending_bear"],  # works in both directions
        },
        {
            "id": "ema_cloud_trend",
            "name": "EMA Cloud Trend Following",
            "mechanism": "momentum",
            "description": (
                "Enter long when fast EMA (e.g. 9) crosses above slow EMA (e.g. 21) "
                "AND price is above the 50 EMA. "
                "Exit when fast EMA crosses below slow EMA or price closes below 50 EMA. "
                "Effective in sustained trending markets."
            ),
            "indicator_families": ["EMA"],
            "entry_conditions_template": "EMA_{fast} crosses above EMA_{slow} AND price > EMA_50",
            "regime_tags": ["trending_bull"],
        },
    ],
    "trending_bear": [
        {
            "id": "momentum_breakout_short",
            "name": "Momentum Breakdown",
            "mechanism": "momentum",
            "description": (
                "Mirror of momentum breakout for short side. "
                "Enter when price breaks below a recent swing low AND momentum "
                "oscillator confirms. Works in sustained downtrends."
            ),
            "indicator_families": ["RSI", "MACD", "EMA", "ATR"],
            "entry_conditions_template": "price breaks below {swing_low_lookback} bar low AND RSI < {rsi_sell_threshold}",
            "regime_tags": ["trending_bear"],
        },
    ],
    "sideways": [
        {
            "id": "rsi_reversion",
            "name": "RSI Mean Reversion",
            "mechanism": "mean_reversion",
            "description": (
                "Enter when RSI drops below oversold threshold (<30) in a range-bound market. "
                "Exit when RSI reaches overbought (>70) or via ATR-based stop. "
                "Requires low ADX (<25) to confirm range-bound conditions. "
                "DO NOT use in trending markets — RSI reversion fails badly in trends."
            ),
            "indicator_families": ["RSI", "ATR"],
            "entry_conditions_template": "RSI_{period} < {rsi_oversold} AND ADX < {adx_range_threshold}",
            "regime_tags": ["sideways"],
        },
        {
            "id": "bollinger_band_squeeze",
            "name": "Bollinger Band Squeeze",
            "mechanism": "mean_reversion",
            "description": (
                "Enter when Bollinger Band width compresses to a minimum threshold "
                "(indicating low volatility), expecting a volatility expansion. "
                "Direction determined by the preceding trend or RSI bias. "
                "Exit on Bollinger Band expansion exceeding {band_expansion_threshold}x the squeeze width "
                "or via fixed stop-loss."
            ),
            "indicator_families": ["BB", "RSI", "ATR"],
            "entry_conditions_template": "BB_width < {squeeze_threshold} AND RSI between {rsi_mid_low} and {rsi_mid_high}",
            "regime_tags": ["sideways"],
        },
        {
            "id": "vwap_pivot",
            "name": "VWAP Pivot Mean Reversion",
            "mechanism": "mean_reversion",
            "description": (
                "Enter when price deviates significantly above or below VWAP "
                "in a sideways market, expecting reversion to VWAP. "
                "Exit when price crosses VWAP or at predefined stop-loss. "
                "Requires confirmation that VWAP is roughly horizontal (not trending)."
            ),
            "indicator_families": ["VWAP", "ATR"],
            "entry_conditions_template": "price > VWAP * (1 + {deviation_threshold}) OR price < VWAP * (1 - {deviation_threshold})",
            "regime_tags": ["sideways"],
        },
    ],
    "high_vol": [
        {
            "id": "volatility_expansion",
            "name": "Volatility Expansion Play",
            "mechanism": "volatility",
            "description": (
                "Enter when ATR reaches a {atr_percentile}%ile high relative to its "
                "{atr_lookback}-bar history, expecting a volatility spike. "
                "Use wide stops (2x normal ATR) and smaller position size. "
                "Exit on ATR normalization or fixed time-based rule."
            ),
            "indicator_families": ["ATR", "RSI"],
            "entry_conditions_template": "ATR_percentile > {atr_high_percentile} AND RSI {direction} {rsi_threshold}",
            "regime_tags": ["high_vol"],
        },
    ],
    "low_vol": [
        {
            "id": "range_expansion_wait",
            "name": "Low-Vol Accumulation Zone",
            "mechanism": "accumulation",
            "description": (
                "In low-vol environments, do not enter. Wait for a confirmed "
                "range break above VWAP with volume confirmation. "
                "Place a limit order just above the resistance zone and wait. "
                "If no break after {wait_bars} bars, cancel and re-evaluate."
            ),
            "indicator_families": ["VWAP", "EMA", "ATR"],
            "entry_conditions_template": "volume > {volume_multiplier} * volume_SMA_{volume_period} AND price breaks above resistance",
            "regime_tags": ["low_vol"],
        },
    ],
}


def get_templates_for_regime(regime: str) -> list[dict]:
    """Return all templates applicable to the given regime."""
    return REGIME_TEMPLATES.get(regime, [])


def get_templates_by_mechanism(mechanism: str) -> list[dict]:
    """Return all templates of the given mechanism type across all regimes."""
    results = []
    for templates in REGIME_TEMPLATES.values():
        for t in templates:
            if t["mechanism"] == mechanism:
                results.append(t)
    return results


def get_all_templates() -> list[dict]:
    """Return a flat list of all templates."""
    results = []
    for templates in REGIME_TEMPLATES.values():
        results.extend(templates)
    return results
```

## Step 8.2 — Wire templates into strategy agent prompt

**File to modify**: `src/agents/strategy_agent.py` / `_build_system_prompt`

Add template injection into the prompt when current regime is known:
```python
from src.strategy_templates.registry import get_templates_for_regime

def _build_system_prompt(kb_context, pair_candidates, previous_diagnosis, current_regime=None):
    ...
    template_str = ""
    if current_regime:
        templates = get_templates_for_regime(current_regime)
        if templates:
            template_str = (
                f"\n\nREGIME-PLAYBOOK: The current regime is '{current_regime}'. "
                f"Consider these strategy templates for this regime:\n"
                + "\n".join(
                    f"- [{t['id']}] {t['name']}: {t['description'][:200]}"
                    for t in templates
                )
            )
    return (
        _STRATEGY_PROMPT
        .replace("{kb_context}", kb_str)
        .replace("{pair_candidates}", pairs_str)
        .replace("{previous_diagnosis}", diag_str)
        .replace("{regime_playbook}", template_str)  # NEW placeholder
    )
```

## Step 8.3 — Update prompt template

**File to modify**: `prompts/strategy_agent_v1.txt`

Add `{regime_playbook}` placeholder near the start of the instruction block:
```
The current market regime is {current_regime}. {regime_playbook}

Your task is to...
```

## Step 8.4 — Update `loop1.py` to pass regime to strategy agent

**File to modify**: `src/loop1.py`

```python
# In run_loop1(), when calling strategy_agent.generate_strategy():
current_regime = _detect_current_regime(db_path)  # from Phase 1 HMM or live candles

spec, backtest_results = strategy_agent.generate_strategy(
    pair_candidates=candidates,
    kb_context=kb_context,
    client=client,
    db_path=db_path,
    mcp_client=mcp,
    previous_diagnosis=last_diagnosis,
    current_regime=current_regime,  # NEW
)
```

## Step 8.5 — Add Phase 8 tests

**File to modify**: `tests/test_loop1.py`

New tests:
1. `get_templates_for_regime("sideways")` returns at least 2 templates.
2. `get_templates_by_mechanism("mean_reversion")` returns templates from multiple regimes.
3. When `current_regime="sideways"` is passed to `_build_system_prompt`, the prompt contains the regime-playbook text.
4. When `current_regime=None`, the playbook placeholder is empty string.

## Verification checklist
- [ ] Template registry returns relevant templates for each regime
- [ ] Strategy agent prompt contains playbook text when regime is known
- [ ] Templates are regime-tagged so they can be validated by Phase 3 pre-check
- [ ] Human trader mental model is reflected: observe → classify → retrieve → adapt


## Related

- MOC: [[_tasks]]
- [[agents]]
- [[backtesting]]
