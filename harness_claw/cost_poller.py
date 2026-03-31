from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Awaitable
from pathlib import Path
from typing import Any


CostCallback = Callable[[str, float, int, int], Awaitable[None]]


def _encode_cwd(cwd: str) -> str:
    expanded = os.path.expanduser(cwd)
    return expanded.replace("/", "-").lstrip("-")


class CostPoller:
    def __init__(
        self,
        session_id: str,
        working_dir: str,
        on_cost_update: CostCallback,
        poll_interval: float = 3.0,
        claude_home: Path | None = None,
    ) -> None:
        self.session_id = session_id
        self._working_dir = working_dir
        self._on_cost_update = on_cost_update
        self._poll_interval = poll_interval
        self._claude_home = claude_home or Path.home() / ".claude"
        self._last_cost: float = -1.0
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval)
            await self._poll()

    async def _poll(self) -> None:
        project_dir = self._claude_home / "projects" / _encode_cwd(self._working_dir)
        if not project_dir.exists():
            return

        jsonl_files = sorted(
            project_dir.glob("*.jsonl"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not jsonl_files:
            return

        total_cost = 0.0
        total_input = 0
        total_output = 0

        try:
            lines = jsonl_files[0].read_text().splitlines()
        except OSError:
            return

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event: dict[str, Any] = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                total_cost += event.get("total_cost_usd", 0.0)
                usage = event.get("usage", {})
                total_input += usage.get("input_tokens", 0)
                total_output += usage.get("output_tokens", 0)

        if total_cost != self._last_cost:
            self._last_cost = total_cost
            await self._on_cost_update(
                self.session_id, total_cost, total_input, total_output
            )
