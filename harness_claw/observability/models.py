from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ToolEvent:
    tool_name: str
    started_at: datetime
    ended_at: datetime | None   # None if still in progress
    duration_ms: int | None
    session_id: str


@dataclass
class AgentStatus:
    session_id: str
    current_tool: str | None
    current_tool_started_at: datetime | None
    last_output_at: datetime | None
    recent_activity: list[ToolEvent]   # newest first, max 50
    is_stuck: bool
    stuck_reason: str | None           # "tool_timeout" | "tool_loop" | "output_silence" | None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "current_tool": self.current_tool,
            "current_tool_started_at": (
                self.current_tool_started_at.isoformat() if self.current_tool_started_at else None
            ),
            "last_output_at": (
                self.last_output_at.isoformat() if self.last_output_at else None
            ),
            "recent_activity": [
                {
                    "tool_name": e.tool_name,
                    "started_at": e.started_at.isoformat(),
                    "ended_at": e.ended_at.isoformat() if e.ended_at else None,
                    "duration_ms": e.duration_ms,
                    "session_id": e.session_id,
                }
                for e in self.recent_activity
            ],
            "is_stuck": self.is_stuck,
            "stuck_reason": self.stuck_reason,
        }
