from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Deque

from harness_claw.observability.models import AgentStatus, ToolEvent

_TOOL_TIMEOUT_SECONDS = 300   # 5 minutes
_OUTPUT_SILENCE_SECONDS = 300  # 5 minutes
_LOOP_THRESHOLD = 10           # same tool N times in a row


@dataclass
class _SessionObs:
    session_id: str
    current_tool: str | None = None
    current_tool_started_at: datetime | None = None
    last_output_at: datetime | None = None
    recent_activity: Deque[ToolEvent] = field(default_factory=lambda: deque(maxlen=50))


def _compute_stuck(obs: _SessionObs) -> tuple[bool, str | None]:
    now = datetime.now(timezone.utc)

    if obs.current_tool and obs.current_tool_started_at:
        elapsed = (now - obs.current_tool_started_at).total_seconds()
        if elapsed > _TOOL_TIMEOUT_SECONDS:
            return True, "tool_timeout"

    recent = list(obs.recent_activity)
    if len(recent) >= _LOOP_THRESHOLD:
        names = [e.tool_name for e in recent[:_LOOP_THRESHOLD]]
        if len(set(names)) == 1:
            return True, "tool_loop"

    if obs.last_output_at and not obs.current_tool:
        silence = (now - obs.last_output_at).total_seconds()
        if silence > _OUTPUT_SILENCE_SECONDS:
            return True, "output_silence"

    return False, None


class AgentObservabilityStore:
    def __init__(self) -> None:
        self._sessions: dict[str, _SessionObs] = {}

    def init_session(self, session_id: str) -> None:
        if session_id not in self._sessions:
            self._sessions[session_id] = _SessionObs(session_id=session_id)

    def remove_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def update_tool_start(self, session_id: str, tool_name: str, timestamp: datetime) -> None:
        obs = self._sessions.get(session_id)
        if obs is None:
            return
        obs.current_tool = tool_name
        obs.current_tool_started_at = timestamp

    def update_tool_end(
        self, session_id: str, tool_name: str, ended_at: datetime, duration_ms: int
    ) -> None:
        obs = self._sessions.get(session_id)
        if obs is None:
            return
        started_at = obs.current_tool_started_at or ended_at - timedelta(milliseconds=duration_ms)
        event = ToolEvent(
            tool_name=tool_name,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            session_id=session_id,
        )
        obs.recent_activity.appendleft(event)
        if obs.current_tool == tool_name:
            obs.current_tool = None
            obs.current_tool_started_at = None

    def update_output(self, session_id: str, at: datetime) -> None:
        obs = self._sessions.get(session_id)
        if obs is None:
            return
        obs.last_output_at = at

    def add_backfill_event(self, session_id: str, event: ToolEvent) -> None:
        """Add a completed ToolEvent from JSONL backfill."""
        obs = self._sessions.get(session_id)
        if obs is None:
            return
        obs.recent_activity.appendleft(event)

    def get_status(self, session_id: str) -> AgentStatus | None:
        obs = self._sessions.get(session_id)
        if obs is None:
            return None
        is_stuck, stuck_reason = _compute_stuck(obs)
        return AgentStatus(
            session_id=session_id,
            current_tool=obs.current_tool,
            current_tool_started_at=obs.current_tool_started_at,
            last_output_at=obs.last_output_at,
            recent_activity=list(obs.recent_activity),
            is_stuck=is_stuck,
            stuck_reason=stuck_reason,
        )

    def all_statuses(self) -> list[AgentStatus]:
        return [s for sid in self._sessions if (s := self.get_status(sid)) is not None]
