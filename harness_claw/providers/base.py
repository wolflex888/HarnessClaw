from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Callable, Awaitable


class BaseProvider:
    """
    Base class for AI providers.

    stream_chat yields dicts with type "token" (delta: str), "usage"
    (input_tokens: int, output_tokens: int), or "session_init"
    (claude_session_id: str) for ClaudeCodeProvider.

    cwd: working directory for the subprocess (ClaudeCodeProvider only).
    claude_session_id: existing Claude Code session ID to resume.
    """

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        max_tokens: int,
        cwd: str | None = None,
        claude_session_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError
        yield  # noqa: unreachable

    async def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_tokens: int,
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError
        yield  # noqa: unreachable
