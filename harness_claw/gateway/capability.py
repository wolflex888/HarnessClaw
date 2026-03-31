from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class AgentAdvertisement:
    session_id: str
    role_id: str
    caps: list[str]
    status: str          # idle | busy | killed
    task_count: int
    connector: str       # "local" | "gateway" | custom


class CapabilityConnector(Protocol):
    async def register(self, agent: AgentAdvertisement) -> None: ...
    async def deregister(self, session_id: str) -> None: ...
    async def query(self, caps: list[str]) -> list[AgentAdvertisement]: ...


class LocalConnector:
    """Tracks HarnessClaw's own PTY sessions."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentAdvertisement] = {}

    async def register(self, agent: AgentAdvertisement) -> None:
        self._agents[agent.session_id] = agent

    async def deregister(self, session_id: str) -> None:
        self._agents.pop(session_id, None)

    async def query(self, caps: list[str]) -> list[AgentAdvertisement]:
        cap_set = set(caps)
        matches = [
            a for a in self._agents.values()
            if cap_set.issubset(set(a.caps))
        ]
        return sorted(matches, key=lambda a: a.task_count)

    def update_task_count(self, session_id: str, delta: int) -> None:
        if session_id in self._agents:
            self._agents[session_id].task_count += delta

    def set_status(self, session_id: str, status: str) -> None:
        if session_id in self._agents:
            self._agents[session_id].status = status
