from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from harness_claw.pricing import get_cost


@dataclass
class Session:
    agent_id: str
    model: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
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
