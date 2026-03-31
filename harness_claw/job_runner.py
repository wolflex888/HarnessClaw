from __future__ import annotations

import asyncio
import base64
import inspect
import os
from pathlib import Path
from typing import Any, Callable, Awaitable

from harness_claw.cost_poller import CostPoller, _encode_cwd
from harness_claw.pty_session import PtySession
from harness_claw.role_registry import RoleRegistry
from harness_claw.session import Session
from harness_claw.session_store import SessionStore

Send = Callable[[dict[str, Any]], Awaitable[None]]


async def _call_send(send: Send, msg: dict[str, Any]) -> None:
    result = send(msg)
    if inspect.isawaitable(result):
        await result


class JobRunner:
    def __init__(self, registry: RoleRegistry, store: SessionStore) -> None:
        self._registry = registry
        self._store = store
        self._pty_sessions: dict[str, PtySession] = {}
        self._cost_pollers: dict[str, CostPoller] = {}
        self._senders: set[Send] = set()

    def add_sender(self, send: Send) -> None:
        self._senders.add(send)

    def remove_sender(self, send: Send) -> None:
        self._senders.discard(send)

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        for send in list(self._senders):
            await _call_send(send, msg)

    async def start_session(self, session: Session) -> None:
        role = self._registry.get(session.role_id)
        if role is None:
            return

        session_id = session.session_id

        pty = PtySession(session_id)

        async def on_output(data: bytes) -> None:
            await self._broadcast({
                "type": "output",
                "session_id": session_id,
                "data": base64.b64encode(data).decode(),
            })

        pty.add_output_callback(on_output)
        await pty.start(role.system_prompt, role.model, session.working_dir)
        self._pty_sessions[session_id] = pty

        async def on_cost_update(sid: str, cost: float, input_tokens: int, output_tokens: int) -> None:
            s = self._store.get(sid)
            if s:
                s.input_tokens = input_tokens
                s.output_tokens = output_tokens
                self._store.save(s)
            await self._broadcast({
                "type": "cost_update",
                "session_id": sid,
                "cost_usd": cost,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            })

        poller = CostPoller(session_id, session.working_dir, on_cost_update)
        poller.start()
        self._cost_pollers[session_id] = poller

    def write(self, session_id: str, data: bytes) -> None:
        pty = self._pty_sessions.get(session_id)
        if pty:
            pty.write(data)

    def resize(self, session_id: str, cols: int, rows: int) -> None:
        pty = self._pty_sessions.get(session_id)
        if pty:
            pty.resize(cols=cols, rows=rows)

    def kill_session(self, session_id: str) -> None:
        pty = self._pty_sessions.get(session_id)
        if pty:
            pty.kill()
        poller = self._cost_pollers.get(session_id)
        if poller:
            poller.stop()

    def delete_session(self, session_id: str) -> None:
        self.kill_session(session_id)
        self._pty_sessions.pop(session_id, None)
        self._cost_pollers.pop(session_id, None)
        session = self._store.get(session_id)
        if session:
            self._delete_claude_session(session)
        self._store.delete(session_id)

    def _delete_claude_session(self, session: Session) -> None:
        cwd = os.path.expanduser(session.working_dir)
        encoded = _encode_cwd(cwd)
        claude_dir = Path.home() / ".claude" / "projects" / encoded
        if claude_dir.exists():
            for f in claude_dir.glob("*.jsonl"):
                f.unlink(missing_ok=True)
