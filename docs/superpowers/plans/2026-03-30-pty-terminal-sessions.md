# PTY Terminal Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the chat-based Work tab with a full xterm.js terminal running `claude` interactively in a PTY, eliminating per-message subprocess startup overhead.

**Architecture:** Each session spawns a persistent `claude` PTY process that stays alive across turns. Raw terminal bytes are relayed over WebSocket (base64-encoded). Cost is tracked by polling Claude Code's on-disk JSONL session files every 3 seconds. The frontend renders xterm.js in the Work tab, replacing the chat bubble UI.

**Tech Stack:** Python `ptyprocess`, asyncio, FastAPI WebSocket; React 18, TypeScript, `@xterm/xterm`, `@xterm/addon-fit`, Tailwind CSS.

---

## File Map

```
# Backend — new
harness_claw/pty_session.py          CREATE  — PtySession: owns one claude PTY process
harness_claw/cost_poller.py          CREATE  — polls ~/.claude/.../session.jsonl for cost

# Backend — rewritten
harness_claw/job_runner.py           REWRITE — manages PtySession + CostPoller per session, broadcasts to WS clients
harness_claw/server.py               REWRITE — simplified WS handler, startup launches PTYs

# Backend — deleted
harness_claw/providers/claude_code.py  DELETE
tests/test_claude_code_provider.py     DELETE

# Backend — tests
tests/test_pty_session.py            CREATE
tests/test_cost_poller.py            CREATE

# Config
pyproject.toml                       MODIFY  — add ptyprocess dependency

# Frontend — new
ui/src/components/TerminalTab.tsx    CREATE  — xterm.js terminal component

# Frontend — rewritten
ui/src/types.ts                      REWRITE — simplified types, new WS message shapes
ui/src/App.tsx                       REWRITE — removes job/message/permission state, routes output to terminal

# Frontend — deleted
ui/src/components/WorkTab.tsx        DELETE
ui/src/components/PermissionDialog.tsx  DELETE

# Frontend — config
ui/package.json                      MODIFY  — add @xterm/xterm, @xterm/addon-fit
```

---

## Tasks

---

### Task 1: Add ptyprocess + PtySession

**Files:**
- Modify: `pyproject.toml`
- Create: `harness_claw/pty_session.py`
- Create: `tests/test_pty_session.py`

- [ ] **Step 1: Add ptyprocess to pyproject.toml**

Edit `pyproject.toml` — add `"ptyprocess>=0.7.0"` to the `dependencies` list:

```toml
dependencies = [
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.29.0",
    "pydantic>=2.7.0",
    "pyyaml>=6.0.1",
    "anthropic>=0.30.0",
    "ptyprocess>=0.7.0",
]
```

- [ ] **Step 2: Install the new dependency**

```bash
uv sync
```

Expected: resolves and installs `ptyprocess`.

- [ ] **Step 3: Write the failing tests**

Create `tests/test_pty_session.py`:

```python
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

from harness_claw.pty_session import PtySession


@pytest.fixture
def mock_proc():
    proc = MagicMock()
    proc.isalive.return_value = True
    proc.read.return_value = b"Hello\r\n"
    return proc


async def test_start_spawns_claude_with_correct_args(mock_proc):
    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc) as mock_spawn:
        mock_proc.read.side_effect = [b"data", EOFError()]
        pty = PtySession("sess-1")
        await pty.start("You are helpful.", "claude-sonnet-4-6", "/tmp")
        mock_spawn.assert_called_once()
        args = mock_spawn.call_args
        cmd = args[0][0]
        assert cmd[0] == "claude"
        assert "--system-prompt" in cmd
        assert "You are helpful." in cmd
        assert "--model" in cmd
        assert "claude-sonnet-4-6" in cmd
        pty.kill()


async def test_write_sends_bytes_to_proc(mock_proc):
    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc):
        mock_proc.read.side_effect = EOFError()
        pty = PtySession("sess-1")
        await pty.start("sys", "model", "/tmp")
        pty.write(b"hello")
        mock_proc.write.assert_called_once_with(b"hello")
        pty.kill()


async def test_resize_calls_setwinsize(mock_proc):
    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc):
        mock_proc.read.side_effect = EOFError()
        pty = PtySession("sess-1")
        await pty.start("sys", "model", "/tmp")
        pty.resize(cols=120, rows=40)
        mock_proc.setwinsize.assert_called_once_with(40, 120)
        pty.kill()


async def test_output_callback_receives_data(mock_proc):
    received = []

    async def cb(data: bytes) -> None:
        received.append(data)

    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc):
        mock_proc.read.side_effect = [b"chunk1", b"chunk2", EOFError()]
        pty = PtySession("sess-1")
        pty.add_output_callback(cb)
        await pty.start("sys", "model", "/tmp")
        # Give read_loop time to run
        await asyncio.sleep(0.05)
        assert b"chunk1" in received
        assert b"chunk2" in received
        pty.kill()


async def test_kill_terminates_proc(mock_proc):
    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc):
        mock_proc.read.side_effect = EOFError()
        pty = PtySession("sess-1")
        await pty.start("sys", "model", "/tmp")
        pty.kill()
        mock_proc.terminate.assert_called()
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
uv run pytest tests/test_pty_session.py -v
```

Expected: `ModuleNotFoundError: No module named 'harness_claw.pty_session'`

- [ ] **Step 5: Create `harness_claw/pty_session.py`**

```python
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
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_pty_session.py -v
```

Expected: 5 passed.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock harness_claw/pty_session.py tests/test_pty_session.py
git commit -m "feat: add PtySession for persistent claude PTY process"
```

---

### Task 2: CostPoller

**Files:**
- Create: `harness_claw/cost_poller.py`
- Create: `tests/test_cost_poller.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cost_poller.py`:

```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import pytest

from harness_claw.cost_poller import CostPoller


async def test_poll_reads_jsonl_and_calls_callback(tmp_path):
    project_dir = tmp_path / "projects" / "-tmp-myproject"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / "session-abc.jsonl"
    jsonl.write_text(
        json.dumps({"type": "result", "total_cost_usd": 0.05, "usage": {"input_tokens": 100, "output_tokens": 50}}) + "\n"
        + json.dumps({"type": "result", "total_cost_usd": 0.03, "usage": {"input_tokens": 60, "output_tokens": 30}}) + "\n"
    )

    updates = []

    async def on_update(session_id, cost, input_tokens, output_tokens):
        updates.append((session_id, cost, input_tokens, output_tokens))

    poller = CostPoller("sess-1", "/tmp/myproject", on_update, claude_home=tmp_path)
    await poller._poll()

    assert len(updates) == 1
    sid, cost, inp, out = updates[0]
    assert sid == "sess-1"
    assert abs(cost - 0.08) < 0.001
    assert inp == 160
    assert out == 80


async def test_poll_skips_when_no_project_dir(tmp_path):
    updates = []

    async def on_update(session_id, cost, input_tokens, output_tokens):
        updates.append((session_id, cost, input_tokens, output_tokens))

    poller = CostPoller("sess-1", "/tmp/nonexistent", on_update, claude_home=tmp_path)
    await poller._poll()

    assert updates == []


async def test_poll_only_calls_callback_when_cost_changes(tmp_path):
    project_dir = tmp_path / "projects" / "-tmp-proj"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / "s.jsonl"
    jsonl.write_text(
        json.dumps({"type": "result", "total_cost_usd": 0.01, "usage": {"input_tokens": 10, "output_tokens": 5}}) + "\n"
    )

    updates = []

    async def on_update(session_id, cost, input_tokens, output_tokens):
        updates.append(cost)

    poller = CostPoller("sess-1", "/tmp/proj", on_update, claude_home=tmp_path)
    await poller._poll()
    await poller._poll()  # same data, should not call again

    assert len(updates) == 1


async def test_poll_ignores_non_result_events(tmp_path):
    project_dir = tmp_path / "projects" / "-tmp-x"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / "s.jsonl"
    jsonl.write_text(
        json.dumps({"type": "assistant", "message": "hi"}) + "\n"
        + json.dumps({"type": "result", "total_cost_usd": 0.02, "usage": {"input_tokens": 20, "output_tokens": 10}}) + "\n"
    )

    updates = []

    async def on_update(session_id, cost, input_tokens, output_tokens):
        updates.append(cost)

    poller = CostPoller("sess-1", "/tmp/x", on_update, claude_home=tmp_path)
    await poller._poll()

    assert len(updates) == 1
    assert abs(updates[0] - 0.02) < 0.001
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cost_poller.py -v
```

Expected: `ModuleNotFoundError: No module named 'harness_claw.cost_poller'`

- [ ] **Step 3: Create `harness_claw/cost_poller.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_cost_poller.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run all backend tests**

```bash
uv run pytest tests/ -v
```

Expected: all pass (27+ tests).

- [ ] **Step 6: Commit**

```bash
git add harness_claw/cost_poller.py tests/test_cost_poller.py
git commit -m "feat: add CostPoller to track session cost from Claude Code JSONL"
```

---

### Task 3: Rewrite JobRunner

**Files:**
- Rewrite: `harness_claw/job_runner.py`
- Modify: `tests/test_job_runner.py`

- [ ] **Step 1: Read current test_job_runner.py**

```bash
cat tests/test_job_runner.py
```

Note what tests exist — we'll replace them with new ones that test the PTY-based runner.

- [ ] **Step 2: Write new test_job_runner.py**

Replace `tests/test_job_runner.py` entirely:

```python
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from harness_claw.job_runner import JobRunner
from harness_claw.role_registry import RoleRegistry
from harness_claw.session import Session
from harness_claw.session_store import SessionStore


def make_session(**kwargs) -> Session:
    defaults = dict(role_id="assistant", working_dir="/tmp", model="claude-sonnet-4-6")
    defaults.update(kwargs)
    return Session(**defaults)


def make_runner(sessions=None):
    registry = MagicMock(spec=RoleRegistry)
    role = MagicMock()
    role.system_prompt = "You are helpful."
    role.model = "claude-sonnet-4-6"
    registry.get.return_value = role

    store = MagicMock(spec=SessionStore)
    store.get.return_value = sessions[0] if sessions else make_session()
    store.all.return_value = sessions or []

    return JobRunner(registry, store), registry, store


async def test_start_session_spawns_pty():
    runner, _, store = make_runner()
    session = make_session(session_id="s1")
    store.get.return_value = session

    with patch("harness_claw.job_runner.PtySession") as MockPty:
        mock_pty = MagicMock()
        mock_pty.start = AsyncMock()
        MockPty.return_value = mock_pty

        with patch("harness_claw.job_runner.CostPoller"):
            await runner.start_session(session)

        MockPty.assert_called_once_with("s1")
        mock_pty.start.assert_called_once_with("You are helpful.", "claude-sonnet-4-6", "/tmp")


async def test_write_forwards_to_pty():
    runner, _, store = make_runner()
    session = make_session(session_id="s1")

    with patch("harness_claw.job_runner.PtySession") as MockPty:
        mock_pty = MagicMock()
        mock_pty.start = AsyncMock()
        MockPty.return_value = mock_pty

        with patch("harness_claw.job_runner.CostPoller"):
            await runner.start_session(session)

        runner.write("s1", b"hello")
        mock_pty.write.assert_called_once_with(b"hello")


async def test_resize_forwards_to_pty():
    runner, _, store = make_runner()
    session = make_session(session_id="s1")

    with patch("harness_claw.job_runner.PtySession") as MockPty:
        mock_pty = MagicMock()
        mock_pty.start = AsyncMock()
        MockPty.return_value = mock_pty

        with patch("harness_claw.job_runner.CostPoller"):
            await runner.start_session(session)

        runner.resize("s1", cols=120, rows=40)
        mock_pty.resize.assert_called_once_with(cols=120, rows=40)


async def test_kill_session_kills_pty():
    runner, _, store = make_runner()
    session = make_session(session_id="s1")

    with patch("harness_claw.job_runner.PtySession") as MockPty:
        mock_pty = MagicMock()
        mock_pty.start = AsyncMock()
        MockPty.return_value = mock_pty

        with patch("harness_claw.job_runner.CostPoller"):
            await runner.start_session(session)

        runner.kill_session("s1")
        mock_pty.kill.assert_called_once()


async def test_broadcast_output_to_all_senders():
    runner, _, _ = make_runner()
    received_a = []
    received_b = []

    async def send_a(msg): received_a.append(msg)
    async def send_b(msg): received_b.append(msg)

    runner.add_sender(send_a)
    runner.add_sender(send_b)

    await runner._broadcast({"type": "output", "session_id": "s1", "data": "abc"})

    assert len(received_a) == 1
    assert len(received_b) == 1
    assert received_a[0]["data"] == "abc"
```

- [ ] **Step 3: Run new tests to verify they fail**

```bash
uv run pytest tests/test_job_runner.py -v
```

Expected: errors — `PtySession`, `CostPoller` not imported in `job_runner.py` yet.

- [ ] **Step 4: Rewrite `harness_claw/job_runner.py`**

```python
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
```

- [ ] **Step 5: Run new job_runner tests**

```bash
uv run pytest tests/test_job_runner.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Run all backend tests**

```bash
uv run pytest tests/ -v --ignore=tests/test_claude_code_provider.py
```

Expected: all pass (ignoring the old provider test which will be deleted in Task 8).

- [ ] **Step 7: Commit**

```bash
git add harness_claw/job_runner.py tests/test_job_runner.py
git commit -m "feat: rewrite JobRunner to manage PTY sessions and cost polling"
```

---

### Task 4: Rewrite server.py

**Files:**
- Rewrite: `harness_claw/server.py`

- [ ] **Step 1: Rewrite `harness_claw/server.py`**

```python
from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from harness_claw.role_registry import RoleRegistry
from harness_claw.session import Session
from harness_claw.session_store import SessionStore
from harness_claw.job_runner import JobRunner

_root = Path(__file__).parent.parent
_agents_yaml = _root / "agents.yaml"
_sessions_json = _root / "sessions.json"

registry = RoleRegistry(_agents_yaml)
store = SessionStore(_sessions_json)
runner = JobRunner(registry, store)

app = FastAPI()


@app.on_event("startup")
async def startup() -> None:
    """Start PTY processes for all non-killed sessions on server boot."""
    for session in store.all():
        if session.status != "killed":
            await runner.start_session(session)


# --- REST ---

@app.get("/api/roles")
def list_roles() -> list[dict[str, Any]]:
    return [
        {"id": r.id, "name": r.name, "provider": r.provider,
         "model": r.model, "system_prompt": r.system_prompt, "max_tokens": r.max_tokens}
        for r in registry.all()
    ]


class CreateSessionRequest(BaseModel):
    role_id: str
    working_dir: str


@app.get("/api/sessions")
def list_sessions() -> dict[str, list[dict[str, Any]]]:
    grouped = store.grouped_by_dir()
    return {
        wd: [s.to_dict() for s in sessions]
        for wd, sessions in grouped.items()
    }


@app.post("/api/sessions", status_code=201)
async def create_session(req: CreateSessionRequest) -> dict[str, Any]:
    role = registry.get(req.role_id)
    if role is None:
        raise HTTPException(status_code=404, detail=f"Role {req.role_id!r} not found")
    session = Session(
        role_id=req.role_id,
        working_dir=req.working_dir,
        model=role.model,
    )
    store.save(session)
    await runner.start_session(session)
    return session.to_dict()


@app.delete("/api/sessions/{session_id}", status_code=204)
def delete_session(session_id: str) -> None:
    if store.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    runner.delete_session(session_id)


# --- WebSocket ---

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def send(msg: dict[str, Any]) -> None:
        await queue.put(msg)

    runner.add_sender(send)

    async def sender() -> None:
        while True:
            msg = await queue.get()
            await ws.send_json(msg)

    sender_task = asyncio.create_task(sender())
    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")

            if msg_type == "input":
                raw = base64.b64decode(data["data"])
                runner.write(data["session_id"], raw)

            elif msg_type == "resize":
                runner.resize(data["session_id"], cols=data["cols"], rows=data["rows"])

            elif msg_type == "cancel":
                runner.kill_session(data["session_id"])

    except WebSocketDisconnect:
        pass
    finally:
        runner.remove_sender(send)
        sender_task.cancel()


# --- SPA ---

_dist = _root / "ui" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        return FileResponse(str(_dist / "index.html"))
```

- [ ] **Step 2: Verify server imports cleanly**

```bash
uv run python -c "from harness_claw.server import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add harness_claw/server.py
git commit -m "feat: rewrite server with PTY-based WS handler and startup PTY launch"
```

---

### Task 5: Update types.ts

**Files:**
- Rewrite: `ui/src/types.ts`

- [ ] **Step 1: Rewrite `ui/src/types.ts`**

```typescript
// Role template (from /api/roles)
export interface RoleConfig {
  id: string
  name: string
  provider: string
  model: string
  system_prompt: string
  max_tokens: number
}

// Session (from /api/sessions)
export interface SessionData {
  session_id: string
  role_id: string
  working_dir: string
  model: string
  name: string
  status: 'idle' | 'running' | 'killed'
  claude_session_id: string | null
  input_tokens: number
  output_tokens: number
}

// UI-side session state
export interface SessionState {
  session_id: string
  role_id: string
  working_dir: string
  model: string
  name: string
  status: 'idle' | 'running' | 'killed'
  input_tokens: number
  output_tokens: number
  cost_usd: number
  tools: ToolInfo[]
}

export interface ToolInfo {
  name: string
  description: string
}

// WebSocket: server → client
export type WSIncoming =
  | { type: 'output'; session_id: string; data: string }
  | { type: 'cost_update'; session_id: string; cost_usd: number; input_tokens: number; output_tokens: number }
  | { type: 'session_update'; session_id: string; name: string; status: 'idle' | 'running' | 'killed' }
  | { type: 'session_deleted'; session_id: string }

// WebSocket: client → server
export type WSSend =
  | { type: 'input'; session_id: string; data: string }
  | { type: 'resize'; session_id: string; cols: number; rows: number }
  | { type: 'cancel'; session_id: string }
```

- [ ] **Step 2: Commit**

```bash
git add ui/src/types.ts
git commit -m "feat: simplify types for PTY-based terminal sessions"
```

---

### Task 6: Add xterm packages + create TerminalTab

**Files:**
- Modify: `ui/package.json`
- Create: `ui/src/components/TerminalTab.tsx`

- [ ] **Step 1: Install xterm packages**

```bash
cd ui && npm install @xterm/xterm @xterm/addon-fit && cd ..
```

Expected: packages added to `node_modules` and `package.json`.

- [ ] **Step 2: Create `ui/src/components/TerminalTab.tsx`**

```tsx
import { useEffect, useRef } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

interface Props {
  sessionId: string
  onRegister: (writeFn: (data: Uint8Array) => void) => void
  onUnregister: () => void
  onInput: (data: string) => void  // base64-encoded keystroke bytes
  onResize: (cols: number, rows: number) => void
}

export function TerminalTab({ sessionId, onRegister, onUnregister, onInput, onResize }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const term = new Terminal({
      theme: { background: '#030712', foreground: '#e2e8f0' },
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
    })
    const fitAddon = new FitAddon()
    term.loadAddon(fitAddon)
    term.open(containerRef.current)
    fitAddon.fit()

    // Register write function with App so WS output can reach this terminal
    onRegister((data: Uint8Array) => term.write(data))

    // Send keystrokes to server as base64
    const dataDispose = term.onData((str: string) => {
      const bytes = new TextEncoder().encode(str)
      let binary = ''
      bytes.forEach(b => { binary += String.fromCharCode(b) })
      onInput(btoa(binary))
    })

    // Resize terminal when container changes size
    const ro = new ResizeObserver(() => {
      fitAddon.fit()
      onResize(term.cols, term.rows)
    })
    ro.observe(containerRef.current)

    // Send initial size
    onResize(term.cols, term.rows)

    return () => {
      onUnregister()
      dataDispose.dispose()
      ro.disconnect()
      term.dispose()
    }
  }, [sessionId])  // remount when session changes

  return (
    <div
      ref={containerRef}
      className="flex-1 min-h-0"
      style={{ padding: '8px', backgroundColor: '#030712' }}
    />
  )
}
```

- [ ] **Step 3: Verify TypeScript compiles (type check only)**

```bash
cd ui && npx tsc --noEmit 2>&1 | head -20
```

Expected: errors about App.tsx (not yet updated) — that's fine. No errors inside TerminalTab.tsx itself.

- [ ] **Step 4: Commit**

```bash
git add ui/package.json ui/package-lock.json ui/src/components/TerminalTab.tsx
git commit -m "feat: add TerminalTab with xterm.js for interactive claude sessions"
```

---

### Task 7: Rewrite App.tsx

**Files:**
- Rewrite: `ui/src/App.tsx`

- [ ] **Step 1: Rewrite `ui/src/App.tsx`**

```tsx
import { useEffect, useRef, useState, useCallback } from 'react'
import { WsClient } from './ws'
import type { RoleConfig, SessionState, WSIncoming } from './types'
import { SessionSidebar } from './components/SessionSidebar'
import { SessionCreatePanel } from './components/SessionCreatePanel'
import { SessionCostBar } from './components/SessionCostBar'
import { TabPanel } from './components/TabPanel'
import { TerminalTab } from './components/TerminalTab'
import { TasksTab } from './components/TasksTab'
import { AgentTab } from './components/AgentTab'
import { ToolsTab } from './components/ToolsTab'

function emptySessionState(data: {
  session_id: string; role_id: string; working_dir: string; model: string;
  name: string; status: 'idle' | 'running' | 'killed';
  input_tokens: number; output_tokens: number;
}): SessionState {
  return {
    ...data,
    cost_usd: 0,
    tools: [],
  }
}

export default function App() {
  const [roles, setRoles] = useState<RoleConfig[]>([])
  const [sessions, setSessions] = useState<Record<string, SessionState>>({})
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const wsRef = useRef<WsClient | null>(null)
  // Maps session_id → the xterm write function for that terminal
  const terminalWriters = useRef<Record<string, (data: Uint8Array) => void>>({})

  // Load roles + sessions from REST
  useEffect(() => {
    fetch('/api/roles').then(r => r.json()).then(setRoles).catch(console.error)
    fetch('/api/sessions').then(r => r.json()).then((grouped: Record<string, Array<{
      session_id: string; role_id: string; working_dir: string; model: string;
      name: string; status: 'idle' | 'running' | 'killed';
      claude_session_id: string | null; input_tokens: number; output_tokens: number;
    }>>) => {
      const flat: Record<string, SessionState> = {}
      for (const sessionList of Object.values(grouped)) {
        for (const s of sessionList) {
          flat[s.session_id] = emptySessionState(s)
          flat[s.session_id].input_tokens = s.input_tokens
          flat[s.session_id].output_tokens = s.output_tokens
        }
      }
      setSessions(flat)
      const first = Object.values(flat)[0]
      if (first) setActiveSessionId(first.session_id)
    }).catch(console.error)
  }, [])

  const handleWsMessage = useCallback((raw: unknown) => {
    const msg = raw as WSIncoming

    if (msg.type === 'output') {
      const writeFn = terminalWriters.current[msg.session_id]
      if (writeFn) {
        const binary = atob(msg.data)
        const bytes = Uint8Array.from(binary, c => c.charCodeAt(0))
        writeFn(bytes)
      }
    } else if (msg.type === 'cost_update') {
      setSessions(prev => {
        const existing = prev[msg.session_id]
        if (!existing) return prev
        return {
          ...prev,
          [msg.session_id]: {
            ...existing,
            cost_usd: msg.cost_usd,
            input_tokens: msg.input_tokens,
            output_tokens: msg.output_tokens,
          },
        }
      })
    } else if (msg.type === 'session_update') {
      setSessions(prev => {
        const existing = prev[msg.session_id]
        if (!existing) return prev
        return { ...prev, [msg.session_id]: { ...existing, name: msg.name, status: msg.status } }
      })
    } else if (msg.type === 'session_deleted') {
      setSessions(prev => {
        const next = { ...prev }
        delete next[msg.session_id]
        return next
      })
      setActiveSessionId(prev => prev === msg.session_id ? null : prev)
    }
  }, [])

  useEffect(() => {
    wsRef.current = new WsClient(handleWsMessage)
    return () => wsRef.current?.destroy()
  }, [handleWsMessage])

  const handleKill = useCallback((sessionId: string) => {
    wsRef.current?.send({ type: 'cancel', session_id: sessionId })
  }, [])

  const handleDelete = useCallback(async (sessionId: string) => {
    await fetch(`/api/sessions/${sessionId}`, { method: 'DELETE' })
  }, [])

  const handleCreateSession = useCallback(async (roleId: string, workingDir: string) => {
    const res = await fetch('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role_id: roleId, working_dir: workingDir }),
    })
    if (!res.ok) return
    const data = await res.json()
    setSessions(prev => ({ ...prev, [data.session_id]: emptySessionState(data) }))
    setActiveSessionId(data.session_id)
    setShowCreate(false)
  }, [])

  const activeSession = activeSessionId ? sessions[activeSessionId] : null
  const activeRole = roles.find(r => r.id === activeSession?.role_id)

  return (
    <div className="flex h-screen overflow-hidden bg-gray-950 text-gray-200">
      <SessionSidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelect={(id) => { setActiveSessionId(id); setShowCreate(false) }}
        onNewSession={() => setShowCreate(true)}
        onDelete={handleDelete}
        onKill={handleKill}
      />

      <div className="flex flex-col flex-1 min-w-0">
        {showCreate ? (
          <SessionCreatePanel
            roles={roles}
            onCreate={handleCreateSession}
            onCancel={() => setShowCreate(false)}
          />
        ) : activeSession ? (
          <>
            <SessionCostBar
              model={activeSession.model}
              inputTokens={activeSession.input_tokens}
              outputTokens={activeSession.output_tokens}
              costUsd={activeSession.cost_usd}
              sessionName={activeSession.name}
              status={activeSession.status}
            />
            <TabPanel>
              {(activeTab) => {
                if (activeTab === 'work') return (
                  <TerminalTab
                    key={activeSession.session_id}
                    sessionId={activeSession.session_id}
                    onRegister={(writeFn) => {
                      terminalWriters.current[activeSession.session_id] = writeFn
                    }}
                    onUnregister={() => {
                      delete terminalWriters.current[activeSession.session_id]
                    }}
                    onInput={(data) => wsRef.current?.send({
                      type: 'input',
                      session_id: activeSession.session_id,
                      data,
                    })}
                    onResize={(cols, rows) => wsRef.current?.send({
                      type: 'resize',
                      session_id: activeSession.session_id,
                      cols,
                      rows,
                    })}
                  />
                )
                if (activeTab === 'tasks') return <TasksTab jobs={[]} />
                if (activeTab === 'agent') return <AgentTab session={activeSession} role={activeRole} />
                if (activeTab === 'tools') return <ToolsTab tools={activeSession.tools} />
                return null
              }}
            </TabPanel>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
            Select a session or create a new one
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Build to verify TypeScript compiles**

```bash
cd ui && npm run build 2>&1 | tail -10
```

Expected: success, 0 TypeScript errors.

- [ ] **Step 3: Commit**

```bash
git add ui/src/App.tsx
git commit -m "feat: rewrite App for PTY-based terminal, route WS output to xterm"
```

---

### Task 8: Delete old files + final verify

**Files:**
- Delete: `harness_claw/providers/claude_code.py`
- Delete: `tests/test_claude_code_provider.py`
- Delete: `ui/src/components/WorkTab.tsx`
- Delete: `ui/src/components/PermissionDialog.tsx`

- [ ] **Step 1: Delete old backend files**

```bash
git rm harness_claw/providers/claude_code.py tests/test_claude_code_provider.py
```

- [ ] **Step 2: Delete old frontend files**

```bash
git rm ui/src/components/WorkTab.tsx ui/src/components/PermissionDialog.tsx
```

- [ ] **Step 3: Run all backend tests**

```bash
uv run pytest tests/ -v
```

Expected: all pass.

- [ ] **Step 4: Build frontend**

```bash
cd ui && npm run build 2>&1 | tail -5
```

Expected: success.

- [ ] **Step 5: Commit**

```bash
git commit -m "chore: remove chat-based WorkTab, PermissionDialog, and ClaudeCodeProvider"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| Replace Work tab with xterm.js terminal | Task 6, 7 |
| Spawn `claude` in PTY, stays alive | Task 1 |
| Raw bytes relayed over WebSocket (base64) | Task 3, 4, 6, 7 |
| Resize events | Task 4, 6, 7 |
| Cost tracked from JSONL on disk | Task 2 |
| `cost_update` WS message | Task 2, 3, 4, 5 |
| PTY started on session create | Task 4 |
| PTY started on server startup (non-killed) | Task 4 |
| Delete `claude_code.py`, `WorkTab.tsx`, `PermissionDialog.tsx` | Task 8 |

**Placeholder scan:** No TBDs, TODOs, or incomplete steps. All code blocks are complete.

**Type consistency:** `PtySession.resize(cols, rows)` matches usage in `JobRunner.resize(session_id, cols, rows)` and server handler. `CostPoller` callback signature `(str, float, int, int)` matches `on_cost_update` closures in `JobRunner`. `TerminalTab` props match usage in `App.tsx`.
