"""
settings.py — All configurable parameters in one place.
Never hardcode these elsewhere. Import from here.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── API Keys ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
BINANCE_TESTNET_API_KEY = os.getenv("BINANCE_TESTNET_API_KEY")
BINANCE_TESTNET_SECRET = os.getenv("BINANCE_TESTNET_SECRET")

# ── Database ──────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "./trading_system.db")

# ── Data Feed ────────────────────────────────────────────────────────────────
# How often (seconds) to poll CCXT for new candles.
# Set to 60 for 1h candles in production; use 60 for 1m candles during integration tests.
CCXT_POLL_INTERVAL_SECONDS = int(os.getenv("CCXT_POLL_INTERVAL_SECONDS", "60"))

# Maximum live candles to retain in live_candles table per symbol/timeframe.
LIVE_CANDLES_BUFFER = int(os.getenv("LIVE_CANDLES_BUFFER", "200"))

# ── Pair Universe Screening ──────────────────────────────────────────────────
# Minimum 24h USD volume for a pair to be included in screening.
UNIVERSE_MIN_VOLUME_USD = float(os.getenv("UNIVERSE_MIN_VOLUME_USD", "50_000_000"))

# Maximum pairs to screen before ranking.
UNIVERSE_MAX_CANDIDATES = int(os.getenv("UNIVERSE_MAX_CANDIDATES", "20"))

# Number of top-ranked pairs passed to the strategy agent.
UNIVERSE_TOP_N = int(os.getenv("UNIVERSE_TOP_N", "5"))

# ── Backtesting ───────────────────────────────────────────────────────────────
# Walk-forward configuration — starting values, to be calibrated.
# See .claude/rules/testing/calibration_tests.md
BACKTEST_N_SLICES = int(os.getenv("BACKTEST_N_SLICES", "5"))
BACKTEST_TRAIN_RATIO = float(os.getenv("BACKTEST_TRAIN_RATIO", "0.80"))  # 80/20 split
BACKTEST_MIN_TRADES_PER_SLICE = int(os.getenv("BACKTEST_MIN_TRADES_PER_SLICE", "5"))

# Cost model — starting values, to be calibrated.
SLIPPAGE_PER_SIDE = float(os.getenv("SLIPPAGE_PER_SIDE", "0.001"))    # 0.1%
EXCHANGE_FEE_PER_SIDE = float(os.getenv("EXCHANGE_FEE_PER_SIDE", "0.001"))  # 0.1%
# Extra slippage applied only to stop-loss exits (gap-through penalty in fast markets)
STOP_LOSS_EXTRA_SLIPPAGE = float(os.getenv("STOP_LOSS_EXTRA_SLIPPAGE", "0.001"))  # 0.1%
# Volume-proportional slippage scaling: position > THRESHOLD % of bar volume scales up
VOLUME_SLIPPAGE_THRESHOLD = float(os.getenv("VOLUME_SLIPPAGE_THRESHOLD", "0.10"))       # 10%
VOLUME_SLIPPAGE_MAX_MULTIPLIER = float(os.getenv("VOLUME_SLIPPAGE_MAX_MULTIPLIER", "3.0"))  # 3× at 100% volume

# ── Degradation Monitor ───────────────────────────────────────────────────────
# Starting values — computed dynamically per strategy after backtest calibration.
# These are fallback defaults only.
DEGRADATION_WINDOW = int(os.getenv("DEGRADATION_WINDOW", "20"))
DEGRADATION_THRESHOLD_FLOOR = float(os.getenv("DEGRADATION_THRESHOLD_FLOOR", "0.30"))

# ── Claude Agents ────────────────────────────────────────────────────────────
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
CLAUDE_HAIKU_MODEL = os.getenv("CLAUDE_HAIKU_MODEL", "claude-haiku-4-5-20251001")
CLAUDE_THINKING_BUDGET_STRATEGY = int(os.getenv("CLAUDE_THINKING_BUDGET_STRATEGY", "8000"))
CLAUDE_THINKING_BUDGET_ANALYST = int(os.getenv("CLAUDE_THINKING_BUDGET_ANALYST", "5000"))
CLAUDE_THINKING_BUDGET_ANALYST_BRIEF = int(os.getenv("CLAUDE_THINKING_BUDGET_ANALYST_BRIEF", "2000"))
CLAUDE_MAX_TOKENS = int(os.getenv("CLAUDE_MAX_TOKENS", "16000"))  # must exceed largest thinking budget

# ── Loop 1 ───────────────────────────────────────────────────────────────────
LOOP1_MAX_ATTEMPTS = int(os.getenv("LOOP1_MAX_ATTEMPTS", "5"))

# ── Probationary Tier ────────────────────────────────────────────────────────
# Strategies graded "probation" (score 0.50–0.70) deploy at reduced size with a
# tighter degradation threshold and auto-demote after N consecutive losses.
PROBATION_SIZE_MULTIPLIER = float(os.getenv("PROBATION_SIZE_MULTIPLIER", "0.5"))
PROBATION_THRESHOLD_BUMP  = float(os.getenv("PROBATION_THRESHOLD_BUMP",  "0.05"))
PROBATION_PROMOTE_WINS    = int(os.getenv("PROBATION_PROMOTE_WINS", "20"))
PROBATION_DEMOTE_LOSSES   = int(os.getenv("PROBATION_DEMOTE_LOSSES", "5"))

# ── Empirical Search (Strategy Discovery Redesign) ──────────────────────────
CANDIDATE_POOL_SIZE = int(os.getenv("CANDIDATE_POOL_SIZE", "12"))
EMPIRICAL_SEARCH_TOP_K = int(os.getenv("EMPIRICAL_SEARCH_TOP_K", "3"))
EMPIRICAL_SEARCH_MIN_VIABLE_PF = float(os.getenv("EMPIRICAL_SEARCH_MIN_VIABLE_PF", "1.2"))
CANDIDATE_EARLY_TERM_MIN_TRADES = int(os.getenv("CANDIDATE_EARLY_TERM_MIN_TRADES", "5"))

# ── Memory Architecture ───────────────────────────────────────────────────────
# FinMem cognitive span: Top-K entries per layer in working memory retrieval.
COGNITIVE_SPAN_K = int(os.getenv("COGNITIVE_SPAN_K", "5"))

# ── Risk Agent ───────────────────────────────────────────────────────────────
RISK_MAX_POSITION_PCT = float(os.getenv("RISK_MAX_POSITION_PCT", "0.05"))   # 5% of balance
RISK_MAX_CONCURRENT = int(os.getenv("RISK_MAX_CONCURRENT", "3"))
RISK_MAX_DAILY_LOSS = float(os.getenv("RISK_MAX_DAILY_LOSS", "0.03"))       # 3% daily loss limit

# StoplossGuard (from Freqtrade research): pause trading after consecutive losses
STOPLOSS_GUARD_CONSECUTIVE = int(os.getenv("STOPLOSS_GUARD_CONSECUTIVE", "3"))
STOPLOSS_GUARD_COOLDOWN_MINUTES = int(os.getenv("STOPLOSS_GUARD_COOLDOWN_MINUTES", "60"))

# ── ATR Position Sizing ───────────────────────────────────────────────────────
ATR_PERIOD = int(os.getenv("ATR_PERIOD", "14"))
ATR_MULTIPLIER = float(os.getenv("ATR_MULTIPLIER", "1.5"))
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "0.01"))  # 1% account equity

# ── Execution / OCO fallback ─────────────────────────────────────────────────
# How often (seconds) to poll for stop/TP hit when OCO is rejected by Testnet.
OCO_POLL_INTERVAL_SECONDS = int(os.getenv("OCO_POLL_INTERVAL_SECONDS", "30"))
# Maximum time (seconds) a trade can stay open before forced market close.
OCO_MAX_WAIT_SECONDS = int(os.getenv("OCO_MAX_WAIT_SECONDS", "86400"))  # 24h

# How many hours without a completed trade before the degradation monitor
# does a time-based check regardless of trade count.
STALE_STRATEGY_HOURS = int(os.getenv("STALE_STRATEGY_HOURS", "48"))

# ── TradingView MCP ──────────────────────────────────────────────────────────
MCP_SERVER_CMD = os.getenv("MCP_SERVER_CMD", "python -m tradingview_mcp").split()
MCP_TIMEOUT_SECONDS = float(os.getenv("MCP_TIMEOUT_SECONDS", "2.0"))

# ── Execution Mode ──────────────────────────────────────────────────────────
# "paper" = simulated execution with real prices (PaperExchange)
# "live"  = real orders on exchange (requires futures API keys)
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "paper")
PAPER_STARTING_BALANCE = float(os.getenv("PAPER_STARTING_BALANCE", "10000"))
