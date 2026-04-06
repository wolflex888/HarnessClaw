from __future__ import annotations

import asyncio
import logging
from typing import Any

from harness_claw.gateway.capability import LocalConnector
from harness_claw.role_registry import RoleRegistry
from harness_claw.session import Session

_logger = logging.getLogger(__name__)


class AgentPool:
    """Spawns and tracks PTY agents for the duration of a plan execution.

    One agent per role, reused across tasks. All owned sessions are killed
    on release_all().
    """

    def __init__(
        self,
        runner: Any,
        connector: LocalConnector,
        registry: RoleRegistry,
        working_dir: str,
        acquire_timeout: float = 30,
    ) -> None:
        self._runner = runner
        self._connector = connector
        self._registry = registry
        self._working_dir = working_dir
        self._acquire_timeout = acquire_timeout
        self._owned: dict[str, str] = {}  # role_id → session_id

    async def acquire(self, caps: list[str]) -> str:
        """Ensure an agent with matching caps is running. Returns session_id.

        Idempotent — second call for same caps returns the cached session_id.
        Raises RuntimeError if runner is None.
        Raises ValueError if no role matches caps.
        Raises TimeoutError if agent doesn't register within acquire_timeout seconds.
        """
        cap_set = set(caps)

        # Return existing session if we already own a matching role
        for role_id, session_id in self._owned.items():
            role = self._registry.get(role_id)
            if role and cap_set.issubset(set(role.caps)):
                return session_id

        if self._runner is None:
            raise RuntimeError("agent pool requires a runner — not configured")

        # Find a role in the registry that satisfies the requested caps
        role = next(
            (r for r in self._registry.all() if cap_set.issubset(set(r.caps))),
            None,
        )
        if role is None:
            raise ValueError(f"no role in registry matches caps {caps!r}")

        # Spawn the PTY agent
        session = Session(
            role_id=role.id,
            working_dir=self._working_dir,
            model=role.model,
        )
        session_id = session.session_id
        await self._runner.start_session(session)

        # Poll connector until the agent self-registers (or timeout)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._acquire_timeout
        while True:
            agents = await self._connector.query(caps)
            if any(a.session_id == session_id for a in agents):
                break
            if loop.time() >= deadline:
                raise TimeoutError(
                    f"agent {session_id!r} (role={role.id!r}) did not register"
                    f" within {self._acquire_timeout}s"
                )
            await asyncio.sleep(0.5)

        self._owned[role.id] = session_id
        return session_id

    async def release_all(self) -> None:
        """Kill all sessions owned by this pool. Logs and continues on error."""
        for role_id, session_id in list(self._owned.items()):
            try:
                # kill_session is synchronous — do not await
                self._runner.kill_session(session_id)
                await self._connector.deregister(session_id)
            except Exception as exc:
                _logger.warning(
                    "AgentPool: failed to kill session %s (role=%s): %s",
                    session_id, role_id, exc,
                )
        self._owned.clear()
