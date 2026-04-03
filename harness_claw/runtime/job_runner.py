from __future__ import annotations

import base64
import inspect
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Awaitable

from harness_claw.runtime.cost_poller import CostPoller, _encode_cwd
from harness_claw.runtime.pty_session import PtySession
from harness_claw.role_registry import RoleRegistry
from harness_claw.session import Session
from harness_claw.runtime.session_store import SessionStore

_logger = logging.getLogger(__name__)

Send = Callable[[dict[str, Any]], Awaitable[None]]


async def _call_send(send: Send, msg: dict[str, Any]) -> None:
    result = send(msg)
    if inspect.isawaitable(result):
        await result


class JobRunner:
    def __init__(
        self,
        registry: RoleRegistry,
        store: SessionStore,
        token_store: Any | None = None,
        connector: Any | None = None,
        dispatcher: Any | None = None,
        broker: Any | None = None,
        mcp_base_url: str = "http://localhost:8000",
    ) -> None:
        self._registry = registry
        self._store = store
        self._token_store = token_store
        self._connector = connector
        self._dispatcher = dispatcher
        self._broker = broker
        self._mcp_base_url = mcp_base_url
        self._pty_sessions: dict[str, PtySession] = {}
        self._cost_pollers: dict[str, CostPoller] = {}
        self._session_tokens: dict[str, str] = {}  # session_id → token
        self._senders: set[Send] = set()
        self._pty_callback_handler_factory: Any | None = None

    def add_sender(self, send: Send) -> None:
        self._senders.add(send)

    def remove_sender(self, send: Send) -> None:
        self._senders.discard(send)

    async def _broadcast(self, msg: dict[str, Any]) -> None:
        for send in list(self._senders):
            await _call_send(send, msg)

    def _write_mcp_config(self, cwd: str, token: str) -> None:
        """Merge HarnessClaw MCP entry into .claude/settings.local.json.

        Uses settings.local.json (gitignored, user-local) so the project's
        committed settings.json is never touched.
        """
        cwd_expanded = os.path.expanduser(cwd)
        claude_dir = Path(cwd_expanded) / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_path = claude_dir / "settings.local.json"

        existing: dict[str, Any] = {}
        if settings_path.exists():
            try:
                existing = json.loads(settings_path.read_text())
            except Exception:
                pass

        mcp_servers = existing.get("mcpServers", {})
        mcp_servers["harnessclaw"] = {
            "type": "sse",
            "url": f"{self._mcp_base_url}/mcp/sse?token={token}",
        }
        existing["mcpServers"] = mcp_servers
        settings_path.write_text(json.dumps(existing, indent=2))

    async def start_session(self, session: Session) -> None:
        session_id = session.session_id

        if session_id in self._pty_sessions:
            _logger.warning("start_session called for already-running session %s; ignoring", session_id)
            return

        role = self._registry.get(session.role_id)
        if role is None:
            _logger.error("start_session: role %r not found for session %s", session.role_id, session_id)
            return

        _logger.info("Starting PTY session %s (role=%s, provider=%s)", session_id, session.role_id, role.provider)

        is_terminal = role.provider == "terminal"

        # Issue token and write MCP config (agent sessions only)
        extra_env: dict[str, str] = {}
        if not is_terminal and self._token_store is not None:
            token = self._token_store.issue(session_id, role.scopes)
            self._session_tokens[session_id] = token
            extra_env["HARNESS_TOKEN"] = token
            self._write_mcp_config(session.working_dir, token)

        # Build command
        if is_terminal:
            cmd = [os.environ.get("SHELL", "/bin/zsh")]
        else:
            cmd = ["claude", "--system-prompt", role.system_prompt, "--model", role.model]

        pty = PtySession(session_id)

        async def on_output(data: bytes) -> None:
            await self._broadcast({
                "type": "output",
                "session_id": session_id,
                "data": base64.b64encode(data).decode(),
            })

        pty.add_output_callback(on_output)
        await pty.start(cmd, session.working_dir, extra_env=extra_env if extra_env else None)
        self._pty_sessions[session_id] = pty

        # Register in capability registry (agent sessions only)
        if not is_terminal and self._connector is not None:
            from harness_claw.gateway.capability import AgentAdvertisement
            await self._connector.register(AgentAdvertisement(
                session_id=session_id,
                role_id=session.role_id,
                caps=role.caps,
                status="idle",
                task_count=0,
                connector="local",
            ))

        # Register write callback with dispatcher (agent sessions only)
        if not is_terminal and self._dispatcher is not None:
            self._dispatcher.register_writer(session_id, pty.write)

        # Register callback handler for task notifications (agent sessions only)
        if not is_terminal and self._broker is not None and self._pty_callback_handler_factory is not None:
            handler = self._pty_callback_handler_factory(session_id)
            self._broker.register_callback_handler(session_id, handler)

        session.status = "running"
        self._store.save(session)
        await self._broadcast({
            "type": "session_update", "session_id": session_id,
            "status": "running", "name": session.name,
        })

        # Cost polling (agent sessions only)
        if not is_terminal:
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
        _logger.info("Killing session %s", session_id)
        pty = self._pty_sessions.pop(session_id, None)
        if pty:
            pty.kill()
        poller = self._cost_pollers.pop(session_id, None)
        if poller:
            poller.stop()
        # Revoke token
        if self._token_store is not None:
            token = self._session_tokens.pop(session_id, None)
            if token:
                self._token_store.revoke(token)
        # Deregister from capability registry
        if self._connector is not None:
            import asyncio
            asyncio.create_task(self._connector.deregister(session_id))
        # Unregister writer from dispatcher
        if self._dispatcher is not None:
            self._dispatcher.unregister_writer(session_id)
        if self._broker is not None:
            self._broker.unregister_callback_handler(session_id)
        session = self._store.get(session_id)
        if session:
            session.status = "killed"
            self._store.save(session)

    def delete_session(self, session_id: str) -> None:
        _logger.info("Deleting session %s", session_id)
        self.kill_session(session_id)
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
