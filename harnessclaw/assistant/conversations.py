"""One conversation per chat, saved as JSON under ~/.harnessclaw/conversations/."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harnessclaw.assistant.messages import Message

_KEY_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Conversation:
    key: str
    model: str = ""  # the [[models]] name chosen with /model; "" = the default
    started_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    # Private memories were shown in this conversation, so it must not go to a cloud model.
    private_context: bool = False
    messages: list[Message] = field(default_factory=list)

    def idle_hours(self) -> float:
        then = datetime.fromisoformat(self.updated_at)
        return (datetime.now(UTC) - then).total_seconds() / 3600

    def reset(self) -> None:
        self.started_at = self.updated_at = _now()
        self.private_context = False
        self.messages = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "model": self.model,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "private_context": self.private_context,
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Conversation:
        return cls(
            key=data["key"],
            model=data.get("model", ""),
            started_at=data.get("started_at", _now()),
            updated_at=data.get("updated_at", _now()),
            private_context=bool(data.get("private_context", False)),
            messages=[Message.from_dict(m) for m in data.get("messages", [])],
        )


class ConversationStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / f"{_KEY_RE.sub('_', key)}.json"

    def load(self, key: str) -> Conversation:
        path = self._path(key)
        try:
            return Conversation.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return Conversation(key=key)
        except (ValueError, KeyError, TypeError):
            # A damaged file: keep it for inspection and start over.
            path.replace(path.with_suffix(".damaged.json"))
            return Conversation(key=key)

    def save(self, conv: Conversation) -> None:
        conv.updated_at = _now()
        path = self._path(conv.key)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(conv.to_dict(), f, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
