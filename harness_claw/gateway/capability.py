from __future__ import annotations

import time
import uuid
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


class GatewayConnector:
    """External agents self-register via bootstrap token and heartbeat to stay alive."""

    def __init__(self, bootstrap_token: str, heartbeat_ttl: int = 30) -> None:
        self._bootstrap_token = bootstrap_token
        self._heartbeat_ttl = heartbeat_ttl
        self._agents: dict[str, AgentAdvertisement] = {}
        self._last_seen: dict[str, float] = {}

    async def register_external(self, bootstrap_token: str, caps: list[str], role_id: str) -> str:
        if bootstrap_token != self._bootstrap_token:
            raise ValueError("invalid bootstrap_token")
        session_id = str(uuid.uuid4())
        agent = AgentAdvertisement(
            session_id=session_id,
            role_id=role_id,
            caps=caps,
            status="idle",
            task_count=0,
            connector="gateway",
        )
        self._agents[session_id] = agent
        self._last_seen[session_id] = time.monotonic()
        return session_id

    async def heartbeat(self, session_id: str) -> None:
        if session_id in self._agents:
            self._last_seen[session_id] = time.monotonic()

    def _expire_stale(self) -> None:
        now = time.monotonic()
        stale = [sid for sid, ts in self._last_seen.items()
                 if now - ts > self._heartbeat_ttl]
        for sid in stale:
            self._agents.pop(sid, None)
            self._last_seen.pop(sid, None)

    async def register(self, agent: AgentAdvertisement) -> None:
        self._agents[agent.session_id] = agent
        self._last_seen[agent.session_id] = time.monotonic()

    async def deregister(self, session_id: str) -> None:
        self._agents.pop(session_id, None)
        self._last_seen.pop(session_id, None)

    async def query(self, caps: list[str]) -> list[AgentAdvertisement]:
        self._expire_stale()
        cap_set = set(caps)
        matches = [a for a in self._agents.values() if cap_set.issubset(set(a.caps))]
        return sorted(matches, key=lambda a: a.task_count)
