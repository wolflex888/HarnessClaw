from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Any

from harness_claw.providers.base import BaseProvider


class ClaudeCodeProvider(BaseProvider):
    def __init__(self) -> None:
        self._pending: dict[str, tuple[asyncio.Event, bool | None]] = {}

    def resolve_permission(self, request_id: str, *, approved: bool) -> None:
        if request_id in self._pending:
            event, _ = self._pending[request_id]
            self._pending[request_id] = (event, approved)
            event.set()

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        max_tokens: int,
        cwd: str | None = None,
        claude_session_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        prompt = messages[-1]["content"] if messages else ""

        cmd = [
            "claude",
            "-p",
            "--verbose",
            "--output-format", "stream-json",
            "--system-prompt", system,
            "--model", model,
        ]
        if claude_session_id:
            cmd += ["--resume", claude_session_id]
        cmd.append(prompt)

        env = {**os.environ}
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE,
            cwd=os.path.expanduser(cwd) if cwd else None,
            env=env,
        )

        # Close stdin immediately so the process doesn't hang waiting for EOF.
        # Permission responses are written before closing in _handle_event.
        assert proc.stdin is not None
        proc.stdin.close()

        try:
            async for line in self._read_lines(proc):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                async for yielded in self._handle_event(event, proc):
                    yield yielded
        finally:
            if proc.returncode is None:
                proc.terminate()
                await proc.wait()

    async def _read_lines(self, proc: asyncio.subprocess.Process) -> AsyncIterator[str]:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            yield line.decode("utf-8", errors="replace")

    async def _handle_event(
        self, event: dict[str, Any], proc: asyncio.subprocess.Process
    ) -> AsyncIterator[dict[str, Any]]:
        event_type = event.get("type")

        if event_type == "system" and event.get("subtype") == "init":
            yield {
                "type": "session_init",
                "claude_session_id": event.get("session_id", ""),
                "tools": event.get("tools", []),
            }

        elif event_type == "assistant":
            message = event.get("message", {})
            for block in message.get("content", []):
                if block.get("type") == "text":
                    yield {"type": "token", "delta": block["text"]}

        elif event_type == "result":
            usage = event.get("usage", {})
            yield {
                "type": "usage",
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cost_usd": event.get("total_cost_usd", 0.0),
            }
            if event.get("subtype") == "error":
                yield {"type": "error", "message": event.get("error", "Unknown error")}
