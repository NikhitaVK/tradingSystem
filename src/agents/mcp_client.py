"""
mcp_client.py — TradingView MCP subprocess wrapper with native TA fallback.

The MCP server runs as a subprocess communicating via JSON-RPC over stdin/stdout.
If the server is unavailable (timeout, process failure, not installed), the client
falls back to computing indicator values directly via the native TA library.
The fallback is invisible to callers — the same dict structure is returned.
"""
import json
import logging
import select
import subprocess
import time
from typing import Optional

from config.settings import MCP_SERVER_CMD, MCP_TIMEOUT_SECONDS
from src.backtest.indicators import (
    compute_rsi,
    compute_ema,
    compute_macd,
    compute_bb,
    compute_atr,
)

logger = logging.getLogger(__name__)

_MSG_ID = 0


def _next_id() -> int:
    global _MSG_ID
    _MSG_ID += 1
    return _MSG_ID


class MCPClient:
    def __init__(
        self,
        server_cmd: list[str] = None,
        timeout: float = None,
        db_path: str = None,
    ):
        self._cmd = server_cmd or MCP_SERVER_CMD
        self._timeout = timeout if timeout is not None else MCP_TIMEOUT_SECONDS
        self._db_path = db_path
        self._proc: Optional[subprocess.Popen] = None
        self._start()

    # ── Public API ────────────────────────────────────────────────────────────

    def call_tool(self, name: str, args: dict) -> dict:
        """
        Call a TradingView MCP tool. Falls back to native TA on any failure.

        Args:
            name: Tool name (e.g. 'get_indicator_data')
            args: Tool arguments dict

        Returns:
            Result dict — same structure regardless of source (MCP or TA).
        """
        try:
            return self._call_mcp(name, args)
        except Exception as exc:
            logger.warning("MCP call failed (%s), falling back to native TA: %s", name, exc)
            return self._native_ta_fallback(name, args)

    def close(self):
        """Terminate the MCP subprocess."""
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _start(self):
        """Launch the MCP subprocess. Failures are silent — fallback handles them."""
        try:
            self._proc = subprocess.Popen(
                self._cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            logger.info("MCP subprocess started (PID %s): %s", self._proc.pid, self._cmd)
        except FileNotFoundError:
            logger.debug("MCP server command not found: %s — will use TA fallback", self._cmd)
            self._proc = None
        except PermissionError:
            logger.debug("MCP server permission denied: %s — will use TA fallback", self._cmd)
            self._proc = None

    # ── MCP subprocess communication ───────────────────────────────────────────

    def _call_mcp(self, name: str, args: dict) -> dict:
        proc = self._get_proc()
        msg = json.dumps({
            "jsonrpc": "2.0",
            "id": _next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        }) + "\n"

        proc.stdin.write(msg.encode())
        proc.stdin.flush()

        ready, _, _ = select.select([proc.stdout], [], [], self._timeout)
        if not ready:
            raise TimeoutError(f"MCP server did not respond within {self._timeout}s")

        line = proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP server closed stdout")

        response = json.loads(line.decode())
        if "error" in response:
            raise RuntimeError(f"MCP error: {response['error']}")

        return response.get("result", {})

    def _get_proc(self) -> subprocess.Popen:
        if self._proc is None or self._proc.poll() is not None:
            self._start()
            if self._proc is None:
                raise RuntimeError("MCP subprocess unavailable")
        return self._proc

    # ── Native TA fallback ────────────────────────────────────────────────────

    def _native_ta_fallback(self, name: str, args: dict) -> dict:
        """
        Compute indicator values using the native indicators library.
        Returns the same structure the MCP server would return.
        """
        if name != "get_indicator_data":
            return {"values": [], "source": "fallback", "error": f"No fallback for tool: {name}"}

        symbol = args.get("symbol", "BTC/USDT")
        timeframe = args.get("timeframe", "1h")
        indicator = args.get("indicator", "RSI").upper()
        params = args.get("params", {})

        # Load candles from live_candles table
        candles = self._load_candles(symbol, timeframe)
        if candles is None or len(candles) < 2:
            return {"values": [], "source": "native_ta", "error": "Insufficient candle data"}

        return self._compute_indicator(indicator, params, candles, symbol)

    def _load_candles(self, symbol: str, timeframe: str):
        """Load the most recent candles for a symbol/timeframe from the DB."""
        if not self._db_path:
            return None
        try:
            from src.data.schema import get_connection
            conn = get_connection(self._db_path)
            rows = conn.execute(
                """
                SELECT timestamp, open, high, low, close, volume
                FROM live_candles
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp DESC
                LIMIT 200
                """,
                (symbol, timeframe),
            ).fetchall()
            if not rows:
                return None
            df = __import__("pandas").DataFrame(rows, columns=[
                "timestamp", "open", "high", "low", "close", "volume"
            ])
            df = df.sort_values("timestamp").reset_index(drop=True)
            return df
        except Exception as exc:
            logger.debug("Could not load candles from DB: %s", exc)
            return None

    def _compute_indicator(self, indicator: str, params: dict, candles, symbol: str) -> dict:
        """Compute a named indicator over candles, returning values + metadata."""
        close = candles["close"]
        high = candles["high"]
        low = candles["low"]

        period = params.get("period", 14)
        std_dev = params.get("std_dev", 2.0)
        fast = params.get("fast", 12)
        slow = params.get("slow", 26)
        signal = params.get("signal", 9)

        values = []

        if indicator == "RSI":
            series = compute_rsi(close, period=period)
            for ts, val in zip(candles["timestamp"], series):
                if val is not None:
                    values.append({"timestamp": int(ts), "value": round(float(val), 4)})

        elif indicator == "EMA":
            series = compute_ema(close, period=period)
            for ts, val in zip(candles["timestamp"], series):
                if val is not None:
                    values.append({"timestamp": int(ts), "value": round(float(val), 4)})

        elif indicator == "MACD":
            result = compute_macd(close, fast=fast, slow=slow, signal=signal)
            for i, (ts, macd, sig, hist) in enumerate(zip(
                candles["timestamp"],
                result["macd"], result["signal"], result["hist"]
            )):
                if macd is not None:
                    values.append({
                        "timestamp": int(ts),
                        "macd": round(float(macd), 6),
                        "signal": round(float(sig), 6),
                        "hist": round(float(hist), 6),
                    })

        elif indicator == "BB":
            result = compute_bb(close, period=period, std_dev=std_dev)
            for ts, upper, mid, lower in zip(
                candles["timestamp"], result["upper"], result["mid"], result["lower"]
            ):
                if upper is not None:
                    values.append({
                        "timestamp": int(ts),
                        "upper": round(float(upper), 4),
                        "mid": round(float(mid), 4),
                        "lower": round(float(lower), 4),
                    })

        elif indicator == "ATR":
            series = compute_atr(high, low, close, period=period)
            for ts, val in zip(candles["timestamp"], series):
                if val is not None:
                    values.append({"timestamp": int(ts), "value": round(float(val), 4)})

        else:
            return {
                "values": [],
                "source": "native_ta",
                "error": f"Unknown indicator: {indicator}",
            }

        return {
            "values": values,
            "source": "native_ta",
            "indicator": indicator,
            "symbol": symbol,
            "params": params,
        }
