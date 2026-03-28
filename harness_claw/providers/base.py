from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Callable, Awaitable


class BaseProvider:
    """
    Base class for AI providers.

    stream_chat yields dicts with type "token" (delta: str) or "usage"
    (input_tokens: int, output_tokens: int).

    stream_with_tools yields the same plus "tool_call"
    (tool_id: str, name: str, input: dict). The tool_executor callback
    receives (tool_name, tool_input) and returns the result string.
    """

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        max_tokens: int,
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError
        yield  # make this an async generator  # noqa: unreachable

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
