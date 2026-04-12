"""
claude_client.py — Anthropic API wrapper for all agent calls.

Critical contract:
- Extended thinking blocks are preserved EXACTLY across multi-turn conversations.
  Never summarise, truncate, or modify them. The signed `signature` field must
  survive the round-trip to/from the DB; otherwise the API will reject turn 2+.
- All thinking + responses are logged to reasoning_logs for audit.
- Rate limit errors are retried with exponential backoff (2s, 4s, 8s).
"""
import json
import logging
import time

import anthropic

from config.settings import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    CLAUDE_MAX_TOKENS,
)
from src.data.schema import get_connection

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE = 2  # seconds


class ClaudeClient:
    def __init__(self, db_path: str, api_key: str = None):
        self._db_path = db_path
        self._client = anthropic.Anthropic(api_key=api_key or ANTHROPIC_API_KEY)
        self._call_count = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict],
        tools: list[dict],
        system_prompt: str,
        thinking_budget: int,
        agent_name: str = "unknown",
        strategy_id: int = None,
    ) -> tuple[str, list[dict], list[dict]]:
        """
        Send a message to Claude with extended thinking enabled.

        Args:
            messages:        Full conversation history including thinking blocks
                             from prior turns (pass updated_messages from previous call).
            tools:           Anthropic tool schema list.
            system_prompt:   System prompt string.
            thinking_budget: Max tokens for extended thinking (from config).
            agent_name:      For reasoning_logs (e.g. 'strategy_agent').
            strategy_id:     Optional FK for reasoning_logs.

        Returns:
            (response_text, tool_calls, updated_messages)
            - response_text:   The assistant's text reply (may be empty if only tool calls)
            - tool_calls:      List of {'name': str, 'input': dict, 'id': str}
            - updated_messages: messages + the new assistant turn (pass to next call)
        """
        self._call_count += 1

        kwargs = {
            "model": CLAUDE_MODEL,
            "max_tokens": CLAUDE_MAX_TOKENS,
            "thinking": {"type": "enabled", "budget_tokens": thinking_budget},
            "system": system_prompt,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = self._call_with_retry(kwargs)

        # ── Parse response content ────────────────────────────────────────────
        response_text = ""
        tool_calls = []
        assistant_content = []  # list of raw content blocks for message history

        for block in response.content:
            if block.type == "thinking":
                # Keep the raw block dict so signature survives
                assistant_content.append(block.model_dump())
            elif block.type == "text":
                response_text += block.text
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                tool_calls.append({
                    "name": block.name,
                    "input": block.input,
                    "id": block.id,
                })
                assistant_content.append(block.model_dump())

        # ── Build updated messages (for next turn) ────────────────────────────
        updated_messages = list(messages) + [
            {"role": "assistant", "content": assistant_content}
        ]

        # ── Log to reasoning_logs ─────────────────────────────────────────────
        thinking_blocks = [b for b in assistant_content if b.get("type") == "thinking"]
        self._log(
            agent=agent_name,
            strategy_id=strategy_id,
            thinking=json.dumps(thinking_blocks),
            response=response_text or json.dumps(tool_calls),
        )

        return response_text, tool_calls, updated_messages

    def append_tool_result(
        self,
        messages: list[dict],
        tool_id: str,
        result: str,
    ) -> list[dict]:
        """
        Append a tool result to messages so Claude can continue after a tool call.

        Args:
            messages: updated_messages from the previous chat() call.
            tool_id:  The tool_use id from the tool_calls list.
            result:   JSON-serialisable string result.

        Returns:
            Updated messages list with the tool result appended.
        """
        return list(messages) + [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": result,
                    }
                ],
            }
        ]

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _call_with_retry(self, kwargs: dict):
        for attempt in range(_MAX_RETRIES + 1):
            try:
                return self._client.messages.create(**kwargs)
            except anthropic.RateLimitError as e:
                if attempt == _MAX_RETRIES:
                    raise
                wait = _BACKOFF_BASE ** (attempt + 1)
                logger.warning("Rate limit hit (attempt %d/%d). Waiting %ds.", attempt + 1, _MAX_RETRIES, wait)
                time.sleep(wait)
            except anthropic.APIStatusError as e:
                logger.error("Anthropic API error: %s", e)
                raise

    def _log(self, agent: str, strategy_id, thinking: str, response: str):
        try:
            conn = get_connection(self._db_path)
            conn.execute(
                "INSERT INTO reasoning_logs (agent, strategy_id, thinking, response, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (agent, strategy_id, thinking, response, int(time.time() * 1000)),
            )
            conn.commit()
        except Exception as exc:
            logger.warning("Failed to log reasoning: %s", exc)
