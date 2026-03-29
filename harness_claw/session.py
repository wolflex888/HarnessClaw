from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from harness_claw.pricing import get_cost


@dataclass
class Session:
    role_id: str
    working_dir: str
    model: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    status: str = "idle"  # idle | running | killed
    claude_session_id: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        return get_cost(self.model, self.input_tokens, self.output_tokens)

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_assistant_message(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "role_id": self.role_id,
            "working_dir": self.working_dir,
            "model": self.model,
            "name": self.name,
            "status": self.status,
            "claude_session_id": self.claude_session_id,
            "messages": self.messages,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        s = cls(
            role_id=data["role_id"],
            working_dir=data["working_dir"],
            model=data["model"],
            session_id=data["session_id"],
        )
        s.name = data.get("name", "")
        s.status = data.get("status", "idle")
        s.claude_session_id = data.get("claude_session_id")
        s.messages = data.get("messages", [])
        s.input_tokens = data.get("input_tokens", 0)
        s.output_tokens = data.get("output_tokens", 0)
        return s
