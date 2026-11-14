"""Conversation types shared by every model provider.

Assistant turns also keep the provider's raw content (`native`), which is sent
back unchanged to the same provider. That keeps history append-only, as
Claude's thinking blocks and prompt caching need. Other providers use the
neutral parts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False


@dataclass
class Message:
    role: Literal["user", "assistant"]
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    native: dict[str, Any] | None = None  # {"provider": ..., "content": [...]}

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role, "text": self.text}
        if self.tool_calls:
            data["tool_calls"] = [vars(c) for c in self.tool_calls]
        if self.tool_results:
            data["tool_results"] = [vars(r) for r in self.tool_results]
        if self.native is not None:
            data["native"] = self.native
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        return cls(
            role=data["role"],
            text=data.get("text", ""),
            tool_calls=[ToolCall(**c) for c in data.get("tool_calls", [])],
            tool_results=[ToolResult(**r) for r in data.get("tool_results", [])],
            native=data.get("native"),
        )


StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


@dataclass
class Completion:
    message: Message
    stop_reason: StopReason
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
