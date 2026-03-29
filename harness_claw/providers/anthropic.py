from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Callable, Awaitable

import anthropic

from harness_claw.providers.base import BaseProvider


class AnthropicProvider(BaseProvider):
    def __init__(self) -> None:
        # Reads ANTHROPIC_API_KEY from environment automatically
        self._client = anthropic.AsyncAnthropic()

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        max_tokens: int,
        cwd: str | None = None,
        claude_session_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async for event in self._stream_once(messages, system, model, [], max_tokens):
            if event["type"] in ("token", "usage"):
                yield event

    async def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_tokens: int,
    ) -> AsyncIterator[dict[str, Any]]:
        current_messages = list(messages)

        while True:
            stop_reason: str | None = None
            content_blocks: list[dict[str, Any]] = []
            tool_use_blocks: list[dict[str, Any]] = []

            async for event in self._stream_once(current_messages, system, model, tools, max_tokens):
                if event["type"] == "token":
                    yield event
                elif event["type"] == "usage":
                    yield event
                elif event["type"] == "_stop":
                    stop_reason = event["stop_reason"]
                    content_blocks = event["content"]
                    tool_use_blocks = [b for b in content_blocks if b["type"] == "tool_use"]
                    for b in tool_use_blocks:
                        yield {
                            "type": "tool_call",
                            "tool_id": b["id"],
                            "name": b["name"],
                            "input": b["input"],
                        }

            if stop_reason != "tool_use":
                break

            tool_results = []
            for block in tool_use_blocks:
                result = await tool_executor(block["name"], block["input"])
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result,
                })

            current_messages = current_messages + [
                {"role": "assistant", "content": content_blocks},
                {"role": "user", "content": tool_results},
            ]

    async def _stream_once(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AsyncIterator[dict[str, Any]]:
        input_tokens = 0
        output_tokens = 0
        stop_reason: str | None = None
        content_blocks: list[dict[str, Any]] = []
        current_block: dict[str, Any] | None = None
        current_tool_input = ""

        kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        if tools:
            kwargs["tools"] = tools

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if isinstance(event, anthropic.types.RawMessageStartEvent):
                    input_tokens = event.message.usage.input_tokens

                elif isinstance(event, anthropic.types.RawContentBlockStartEvent):
                    cb = event.content_block
                    if cb.type == "text":
                        current_block = {"type": "text", "text": ""}
                    elif cb.type == "tool_use":
                        current_block = {
                            "type": "tool_use",
                            "id": cb.id,
                            "name": cb.name,
                            "input": {},
                        }
                        current_tool_input = ""

                elif isinstance(event, anthropic.types.RawContentBlockDeltaEvent):
                    if event.delta.type == "text_delta" and current_block:
                        current_block["text"] += event.delta.text
                        yield {"type": "token", "delta": event.delta.text}
                    elif event.delta.type == "input_json_delta":
                        current_tool_input += event.delta.partial_json

                elif isinstance(event, anthropic.types.RawContentBlockStopEvent):
                    if current_block:
                        if current_block["type"] == "tool_use":
                            current_block["input"] = (
                                json.loads(current_tool_input) if current_tool_input else {}
                            )
                        content_blocks.append(current_block)
                    current_block = None
                    current_tool_input = ""

                elif isinstance(event, anthropic.types.RawMessageDeltaEvent):
                    stop_reason = event.delta.stop_reason
                    output_tokens = event.usage.output_tokens

        yield {"type": "usage", "input_tokens": input_tokens, "output_tokens": output_tokens}
        yield {"type": "_stop", "stop_reason": stop_reason, "content": content_blocks}
