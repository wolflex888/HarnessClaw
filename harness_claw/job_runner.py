from __future__ import annotations

import asyncio
import inspect
import os
from pathlib import Path
from typing import Any, Callable, Awaitable

from harness_claw.providers.base import BaseProvider
from harness_claw.providers.claude_code import ClaudeCodeProvider
from harness_claw.providers.anthropic import AnthropicProvider
from harness_claw.role_registry import RoleRegistry
from harness_claw.session import Session
from harness_claw.session_store import SessionStore

PROVIDERS: dict[str, BaseProvider] = {
    "anthropic": AnthropicProvider(),
    "claude-code": ClaudeCodeProvider(),
}

Send = Callable[[dict[str, Any]], Awaitable[None]]


async def _call_send(send: Send, msg: dict[str, Any]) -> None:
    result = send(msg)
    if inspect.isawaitable(result):
        await result


class JobRunner:
    def __init__(self, registry: RoleRegistry, store: SessionStore) -> None:
        self._registry = registry
        self._store = store
        self._tasks: dict[str, asyncio.Task[None]] = {}  # job_id → task

    def get_or_create_session(self, session_id: str) -> Session:
        session = self._store.get(session_id)
        if session is None:
            raise KeyError(f"Session {session_id!r} not found")
        return session

    async def run_job(self, session_id: str, text: str, send: Send) -> str:
        session = self.get_or_create_session(session_id)
        role = self._registry.get(session.role_id)
        if role is None:
            raise KeyError(f"Role {session.role_id!r} not found")

        job_id = f"job-{session_id[:8]}-{len(session.messages)}"
        provider = PROVIDERS.get(role.provider, PROVIDERS["claude-code"])

        # Set session name from first user message
        if not session.name and text:
            session.name = text[:40]
            await _call_send(send, {"type": "session_update", "session_id": session_id, "name": session.name, "status": "running"})

        session.add_user_message(text)
        session.status = "running"
        self._store.save(session)

        await _call_send(send, {"type": "job_update", "job_id": job_id, "session_id": session_id, "status": "running", "progress": None, "title": text[:40]})

        full_response = ""
        try:
            async for event in provider.stream_chat(
                messages=session.messages,
                system=role.system_prompt,
                model=role.model,
                max_tokens=role.max_tokens,
                cwd=session.working_dir,
                claude_session_id=session.claude_session_id,
            ):
                if event["type"] == "token":
                    full_response += event["delta"]
                    await _call_send(send, {"type": "token", "job_id": job_id, "delta": event["delta"]})
                elif event["type"] == "usage":
                    session.input_tokens += event["input_tokens"]
                    session.output_tokens += event["output_tokens"]
                    await _call_send(send, {
                        "type": "usage",
                        "job_id": job_id,
                        "input_tokens": session.input_tokens,
                        "output_tokens": session.output_tokens,
                        "cost_usd": session.cost_usd,
                    })
                elif event["type"] == "session_init":
                    session.claude_session_id = event["claude_session_id"]
                elif event["type"] == "permission_request":
                    await _call_send(send, {
                        "type": "permission_request",
                        "session_id": session_id,
                        "request_id": event["request_id"],
                        "tool_name": event["tool_name"],
                        "input": event["input"],
                    })
                elif event["type"] == "error":
                    await _call_send(send, {"type": "error", "job_id": job_id, "message": event["message"]})

        except asyncio.CancelledError:
            session.status = "killed"
            self._store.save(session)
            await _call_send(send, {"type": "job_update", "job_id": job_id, "session_id": session_id, "status": "failed", "progress": None, "title": text[:40]})
            await _call_send(send, {"type": "session_update", "session_id": session_id, "name": session.name, "status": "killed"})
            return ""

        session.add_assistant_message(full_response)
        session.status = "idle"
        self._store.save(session)

        await _call_send(send, {"type": "job_update", "job_id": job_id, "session_id": session_id, "status": "completed", "progress": 100, "title": text[:40]})
        await _call_send(send, {"type": "session_update", "session_id": session_id, "name": session.name, "status": "idle"})
        return full_response

    def resolve_permission(self, request_id: str, *, approved: bool) -> None:
        provider = PROVIDERS.get("claude-code")
        if isinstance(provider, ClaudeCodeProvider):
            provider.resolve_permission(request_id, approved=approved)

    def kill_job(self, session_id: str) -> None:
        for job_id, task in list(self._tasks.items()):
            if session_id in job_id:
                task.cancel()

    def delete_session(self, session_id: str) -> None:
        session = self._store.get(session_id)
        if session and session.claude_session_id:
            self._delete_claude_session(session)
        self._store.delete(session_id)

    def _delete_claude_session(self, session: Session) -> None:
        """Delete Claude Code's on-disk session file."""
        if not session.claude_session_id:
            return
        cwd = os.path.expanduser(session.working_dir)
        encoded = cwd.replace("/", "-").lstrip("-")
        claude_dir = Path.home() / ".claude" / "projects" / encoded
        session_file = claude_dir / f"{session.claude_session_id}.jsonl"
        if session_file.exists():
            session_file.unlink()
