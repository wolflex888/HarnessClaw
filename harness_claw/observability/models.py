from __future__ import annotations

# All datetime values in this module must be UTC (use datetime.utcnow() or datetime.now(timezone.utc)).
# Naive datetimes are not accepted — callers are responsible for passing UTC datetimes.

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

StuckReason = Literal["tool_timeout", "tool_loop", "output_silence"]


@dataclass
class ToolEvent:
    tool_name: str
    started_at: datetime
    ended_at: datetime | None   # None if still in progress
    duration_ms: int | None
    session_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": self.duration_ms,
            "session_id": self.session_id,
        }


@dataclass
class AgentStatus:
    session_id: str
    current_tool: str | None
    current_tool_started_at: datetime | None
    last_output_at: datetime | None
    recent_activity: list[ToolEvent]   # newest first, max 50
    is_stuck: bool
    stuck_reason: StuckReason | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "current_tool": self.current_tool,
            "current_tool_started_at": (
                self.current_tool_started_at.isoformat() if self.current_tool_started_at else None
            ),
            "last_output_at": (
                self.last_output_at.isoformat() if self.last_output_at else None
            ),
            "recent_activity": [e.to_dict() for e in self.recent_activity],
            "is_stuck": self.is_stuck,
            "stuck_reason": self.stuck_reason,
        }
