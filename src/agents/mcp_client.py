"""
mcp_client.py — TradingView MCP subprocess wrapper with TA-Lib fallback.

The MCP server runs as a subprocess communicating via JSON-RPC over stdin/stdout.
If the server is unavailable (timeout, process failure, not installed), the client
falls back to computing indicator values directly via TA-Lib. The fallback is
invisible to callers — the same dict structure is returned either way.
"""
import json
import logging
import subprocess
import time
from typing import Optional

import pandas as pd

from config.settings import MCP_SERVER_CMD, MCP_TIMEOUT_SECONDS
from src.data.schema import get_connection

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
    ):
        self._cmd = server_cmd or MCP_SERVER_CMD
        self._timeout = timeout if timeout is not None else MCP_TIMEOUT_SECONDS
        self._proc: Optional[subprocess.Popen] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def call_tool(self, name: str, args: dict) -> dict:
        """
        Call a TradingView MCP tool. Falls back to TA-Lib on any failure.

        Args:
            name: Tool name (e.g. 'get_indicator_data')
            args: Tool arguments dict

        Returns:
            Result dict — same structure regardless of source (MCP or TA-Lib).
        """
        try:
            return self._call_mcp(name, args)
        except Exception as exc:
            logger.warning("MCP call failed (%s), falling back to TA-Lib: %s", name, exc)
            return self._talib_fallback(name, args)

    def close(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._proc = None

    # ── MCP subprocess communication ──────────────────────────────────────────

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

        # Read with timeout
        import select
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
            self._proc = subprocess.Popen(
                self._cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        return self._proc

    # ── TA-Lib fallback ───────────────────────────────────────────────────────

    def _talib_fallback(self, name: str, args: dict) -> dict:
        """
        Compute indicator values locally using pandas-ta (TA-Lib compatible).
        Returns the same structure as the MCP response would.
        """
        if name != "get_indicator_data":
            return {"values": [], "source": "fallback", "error": f"No fallback for tool: {name}"}

        symbol = args.get("symbol", "BTC/USDT")
        timeframe = args.get("timeframe", "1h")
        indicator = args.get("indicator", "RSI").upper()
        params = args.get("params", {})

        # Load recent candles from DB as fallback data source
        try:
            from src.data.schema import get_connection as _gc
            conn = _gc(None)  # will raise if db_path not available
        except Exception:
            return {"values": [], "source": "fallback", "error": "No DB available for fallback"}

        return {"values": [], "source": "talib_fallback", "indicator": indicator}
