from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Awaitable

import ptyprocess

OutputCallback = Callable[[bytes], Awaitable[None]]


class PtySession:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._proc: ptyprocess.PtyProcess | None = None
        self._callbacks: list[OutputCallback] = []
        self._read_task: asyncio.Task[None] | None = None

    async def start(self, system_prompt: str, model: str, cwd: str) -> None:
        cwd_expanded = os.path.expanduser(cwd)
        cmd = ["claude", "--system-prompt", system_prompt, "--model", model]
        self._proc = ptyprocess.PtyProcess.spawn(
            cmd, cwd=cwd_expanded, dimensions=(24, 80)
        )
        self._read_task = asyncio.create_task(self._read_loop())

    def add_output_callback(self, cb: OutputCallback) -> None:
        self._callbacks.append(cb)

    def remove_output_callback(self, cb: OutputCallback) -> None:
        try:
            self._callbacks.remove(cb)
        except ValueError:
            pass

    def write(self, data: bytes) -> None:
        if self._proc and self._proc.isalive():
            self._proc.write(data)

    def resize(self, cols: int, rows: int) -> None:
        if self._proc and self._proc.isalive():
            self._proc.setwinsize(rows, cols)

    def kill(self) -> None:
        if self._read_task:
            self._read_task.cancel()
        if self._proc and self._proc.isalive():
            self._proc.terminate(force=True)

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.isalive()

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while self._proc and self._proc.isalive():
            try:
                data = await loop.run_in_executor(None, self._proc.read, 4096)
                if data:
                    for cb in list(self._callbacks):
                        await cb(data)
            except EOFError:
                break
            except asyncio.CancelledError:
                break
            except Exception:
                break
