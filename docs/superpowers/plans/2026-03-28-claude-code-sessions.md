# Claude Code Sessions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the direct Anthropic API backend with Claude Code subprocess sessions, add session persistence with kill/resume/delete lifecycle, add uv project setup with `harnessclaw run` CLI, and rebuild the UI around sessions grouped by working directory with Work/Tasks/Agent/Tools tabs and an inline permission dialog.

**Architecture:** ClaudeCodeProvider spawns `claude -p --input-format stream-json --output-format stream-json` as an asyncio subprocess per message, using `--resume` to continue Claude Code's on-disk session history. A SessionStore persists sessions to `sessions.json`. The UI switches from agent-centric to session-centric with grouped sidebar and tabbed main area.

**Tech Stack:** Python 3.12+, uv, FastAPI, uvicorn, asyncio subprocesses, Claude Code CLI; React 18, TypeScript, Vite, Tailwind CSS.

---

## File Map

```
# Backend
pyproject.toml                          CREATE  — uv project, harnessclaw CLI entry point
harness_claw/cli.py                     CREATE  — `harnessclaw run` subcommand
harness_claw/session.py                 MODIFY  — add role_id, working_dir, name, status, claude_session_id fields
harness_claw/role_registry.py           CREATE  — loads role templates from agents.yaml
harness_claw/session_store.py           CREATE  — load/save/delete sessions.json
harness_claw/providers/base.py          MODIFY  — add cwd, claude_session_id optional params
harness_claw/providers/anthropic.py     MODIFY  — accept (ignore) new params
harness_claw/providers/claude_code.py   CREATE  — ClaudeCodeProvider
harness_claw/job_runner.py              MODIFY  — session-centric, permission flow, kill/resume/delete
harness_claw/server.py                  MODIFY  — session REST endpoints, permission_response WS handling
agents.yaml                             MODIFY  — rename to roles format

# Tests
tests/test_role_registry.py             CREATE
tests/test_session_store.py             CREATE
tests/test_claude_code_provider.py      CREATE

# Frontend
ui/src/types.ts                         MODIFY  — session types, new WS messages
ui/src/App.tsx                          MODIFY  — session-centric state
ui/src/components/SessionSidebar.tsx    CREATE  — replaces AgentSidebar
ui/src/components/SessionCreatePanel.tsx CREATE
ui/src/components/TabPanel.tsx          CREATE
ui/src/components/WorkTab.tsx           CREATE  — replaces ChatPanel
ui/src/components/TasksTab.tsx          CREATE  — replaces JobsPanel
ui/src/components/AgentTab.tsx          CREATE
ui/src/components/ToolsTab.tsx          CREATE
ui/src/components/PermissionDialog.tsx  CREATE
ui/src/components/SessionCostBar.tsx    MODIFY  — use session data
ui/src/components/AgentSidebar.tsx      REMOVE  (delete)
ui/src/components/ChatPanel.tsx         REMOVE  (delete)
ui/src/components/JobsPanel.tsx         REMOVE  (delete)
ui/src/components/AgentConfigPanel.tsx  REMOVE  (delete)
```

---

## Tasks

Write the following tasks in order. Each task must have:
- Complete, runnable code (no placeholders)
- TDD where applicable (test first, then implementation)
- Exact commands to run tests
- Exact git commit command

---

### Task 1: pyproject.toml + CLI entry point

Convert the project to uv. Replace `setup.py` and `requirements.txt` with `pyproject.toml`. Add `harness_claw/cli.py` with a `run` subcommand that spawns both uvicorn and `npm run dev` concurrently.

**Files:**
- Create: `pyproject.toml`
- Create: `harness_claw/cli.py`
- Delete: `setup.py` (remove from git)
- Keep: `requirements.txt` (uv can still use it as a fallback reference, but pyproject.toml is authoritative)

`pyproject.toml`:
```toml
[project]
name = "harness-claw"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.29.0",
    "pydantic>=2.7.0",
    "pyyaml>=6.0.1",
    "anthropic>=0.30.0",
]

[project.scripts]
harnessclaw = "harness_claw.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
dev-dependencies = [
    "pytest>=8.2.0",
    "pytest-asyncio>=0.23.0",
    "httpx>=0.27.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

Steps:
- [ ] Create `pyproject.toml` with exact content above
- [ ] Create `harness_claw/cli.py`:

```python
from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != "run":
        print("Usage: harnessclaw run")
        sys.exit(1)

    root = Path(__file__).parent.parent
    ui_dir = root / "ui"

    backend = subprocess.Popen(
        ["uvicorn", "harness_claw.server:app", "--reload", "--port", "8000"],
        cwd=root,
    )
    frontend = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=ui_dir,
    )

    def _shutdown(sig: int, frame: object) -> None:
        backend.terminate()
        frontend.terminate()
        backend.wait()
        frontend.wait()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    backend.wait()
    frontend.wait()
```

- [ ] Remove `setup.py`: `git rm setup.py`
- [ ] Run: `uv sync` — expected: creates `.venv/` and installs deps
- [ ] Run: `uv run pytest tests/ -v` — expected: 18 passed
- [ ] Commit:
```bash
git add pyproject.toml harness_claw/cli.py
git rm setup.py
git commit -m "feat: convert to uv project with harnessclaw run CLI"
```

---

### Task 2: Update Session model

Add `role_id`, `working_dir`, `name`, `status`, `claude_session_id` fields. Remove `agent_id`. Keep `add_user_message` and `add_assistant_message`.

**Files:**
- Modify: `harness_claw/session.py`
- Modify: `tests/test_session.py`

- [ ] **Step 1: Update `tests/test_session.py`** — replace all tests to match new model:

```python
from harness_claw.session import Session


def test_session_defaults() -> None:
    s = Session(role_id="general-purpose", working_dir="~/src", model="claude-sonnet-4-6")
    assert s.status == "idle"
    assert s.name == ""
    assert s.claude_session_id is None
    assert s.input_tokens == 0
    assert s.output_tokens == 0
    assert s.messages == []


def test_session_cost_usd() -> None:
    s = Session(role_id="general-purpose", working_dir="~/src", model="claude-sonnet-4-6")
    s.input_tokens = 1_000_000
    s.output_tokens = 1_000_000
    assert abs(s.cost_usd - 18.0) < 0.001  # 3.00 + 15.00


def test_session_unknown_model_cost_is_zero() -> None:
    s = Session(role_id="x", working_dir="~/src", model="unknown-model")
    s.input_tokens = 1000
    assert s.cost_usd == 0.0


def test_session_has_unique_id() -> None:
    a = Session(role_id="x", working_dir="~/src", model="claude-sonnet-4-6")
    b = Session(role_id="x", working_dir="~/src", model="claude-sonnet-4-6")
    assert a.session_id != b.session_id


def test_session_add_messages() -> None:
    s = Session(role_id="x", working_dir="~/src", model="claude-sonnet-4-6")
    s.add_user_message("hello")
    s.add_assistant_message("hi")
    assert s.messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]


def test_session_to_dict() -> None:
    s = Session(role_id="code-writer", working_dir="~/src/foo", model="claude-sonnet-4-6")
    s.name = "Fix the bug"
    d = s.to_dict()
    assert d["role_id"] == "code-writer"
    assert d["working_dir"] == "~/src/foo"
    assert d["name"] == "Fix the bug"
    assert d["status"] == "idle"
    assert "session_id" in d
    assert "claude_session_id" in d


def test_session_from_dict() -> None:
    s = Session(role_id="x", working_dir="~/src", model="claude-sonnet-4-6")
    s.name = "Test"
    d = s.to_dict()
    restored = Session.from_dict(d)
    assert restored.session_id == s.session_id
    assert restored.name == "Test"
    assert restored.role_id == "x"
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_session.py -v
```
Expected: FAIL (missing fields, no `to_dict`/`from_dict`)

- [ ] **Step 3: Rewrite `harness_claw/session.py`**:

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from harness_claw.pricing import get_cost


@dataclass
class Session:
    role_id: str
    working_dir: str
    model: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    status: str = "idle"  # idle | running | killed
    claude_session_id: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        return get_cost(self.model, self.input_tokens, self.output_tokens)

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_assistant_message(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "role_id": self.role_id,
            "working_dir": self.working_dir,
            "model": self.model,
            "name": self.name,
            "status": self.status,
            "claude_session_id": self.claude_session_id,
            "messages": self.messages,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        s = cls(
            role_id=data["role_id"],
            working_dir=data["working_dir"],
            model=data["model"],
            session_id=data["session_id"],
        )
        s.name = data.get("name", "")
        s.status = data.get("status", "idle")
        s.claude_session_id = data.get("claude_session_id")
        s.messages = data.get("messages", [])
        s.input_tokens = data.get("input_tokens", 0)
        s.output_tokens = data.get("output_tokens", 0)
        return s
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_session.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add harness_claw/session.py tests/test_session.py
git commit -m "feat: update Session model with role_id, working_dir, name, status, claude_session_id"
```

---

### Task 3: RoleRegistry

Load role templates from `agents.yaml`. Rename the `agents:` key to `roles:` in the YAML.

**Files:**
- Create: `harness_claw/role_registry.py`
- Create: `tests/test_role_registry.py`
- Modify: `agents.yaml`

- [ ] **Step 1: Write `tests/test_role_registry.py`**:

```python
from pathlib import Path
import pytest
from harness_claw.role_registry import RoleConfig, RoleRegistry


def test_load_roles(tmp_path: Path) -> None:
    yaml_file = tmp_path / "agents.yaml"
    yaml_file.write_text("""
roles:
  - id: general-purpose
    name: General Purpose
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You are a helpful assistant."
    max_tokens: 8192
  - id: code-writer
    name: Code Writer
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You write clean code."
    max_tokens: 8192
""")
    registry = RoleRegistry(yaml_file)
    roles = registry.all()
    assert len(roles) == 2
    assert roles[0].id == "general-purpose"
    assert roles[1].id == "code-writer"


def test_get_role(tmp_path: Path) -> None:
    yaml_file = tmp_path / "agents.yaml"
    yaml_file.write_text("""
roles:
  - id: general-purpose
    name: General Purpose
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You are a helpful assistant."
    max_tokens: 8192
""")
    registry = RoleRegistry(yaml_file)
    role = registry.get("general-purpose")
    assert role is not None
    assert role.name == "General Purpose"
    assert role.model == "claude-sonnet-4-6"
    assert role.system_prompt == "You are a helpful assistant."
    assert role.max_tokens == 8192


def test_get_missing_role(tmp_path: Path) -> None:
    yaml_file = tmp_path / "agents.yaml"
    yaml_file.write_text("roles: []")
    registry = RoleRegistry(yaml_file)
    assert registry.get("nonexistent") is None
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_role_registry.py -v
```
Expected: FAIL (module not found)

- [ ] **Step 3: Create `harness_claw/role_registry.py`**:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class RoleConfig:
    id: str
    name: str
    provider: str
    model: str
    system_prompt: str
    max_tokens: int = 8192


class RoleRegistry:
    def __init__(self, path: Path) -> None:
        self._roles: dict[str, RoleConfig] = {}
        data = yaml.safe_load(path.read_text())
        for item in data.get("roles", []):
            role = RoleConfig(
                id=item["id"],
                name=item["name"],
                provider=item.get("provider", "claude-code"),
                model=item["model"],
                system_prompt=item["system_prompt"],
                max_tokens=item.get("max_tokens", 8192),
            )
            self._roles[role.id] = role

    def all(self) -> list[RoleConfig]:
        return list(self._roles.values())

    def get(self, role_id: str) -> RoleConfig | None:
        return self._roles.get(role_id)
```

- [ ] **Step 4: Update `agents.yaml`**:

```yaml
roles:
  - id: general-purpose
    name: General Purpose
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You are a helpful assistant."
    max_tokens: 8192

  - id: code-writer
    name: Code Writer
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You write clean, well-tested code."
    max_tokens: 8192

  - id: reviewer
    name: Code Reviewer
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You review code for correctness, clarity, and security."
    max_tokens: 4096
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_role_registry.py -v
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add harness_claw/role_registry.py tests/test_role_registry.py agents.yaml
git commit -m "feat: add RoleRegistry, convert agents.yaml to roles format"
```

---

### Task 4: SessionStore

Persist sessions to `sessions.json` on disk.

**Files:**
- Create: `harness_claw/session_store.py`
- Create: `tests/test_session_store.py`

- [ ] **Step 1: Write `tests/test_session_store.py`**:

```python
from pathlib import Path
from harness_claw.session import Session
from harness_claw.session_store import SessionStore


def make_session(role_id: str = "general-purpose") -> Session:
    return Session(role_id=role_id, working_dir="~/src", model="claude-sonnet-4-6")


def test_save_and_load(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.json")
    s = make_session()
    s.name = "Hello world"
    store.save(s)

    store2 = SessionStore(tmp_path / "sessions.json")
    loaded = store2.get(s.session_id)
    assert loaded is not None
    assert loaded.name == "Hello world"
    assert loaded.session_id == s.session_id


def test_all_sessions(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.json")
    a = make_session("code-writer")
    a.working_dir = "~/src/proj-a"
    b = make_session("reviewer")
    b.working_dir = "~/src/proj-b"
    store.save(a)
    store.save(b)

    all_sessions = store.all()
    assert len(all_sessions) == 2


def test_delete_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.json")
    s = make_session()
    store.save(s)
    store.delete(s.session_id)
    assert store.get(s.session_id) is None


def test_grouped_by_working_dir(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.json")
    a = make_session()
    a.working_dir = "~/src/alpha"
    b = make_session()
    b.working_dir = "~/src/alpha"
    c = make_session()
    c.working_dir = "~/src/beta"
    for s in [a, b, c]:
        store.save(s)

    grouped = store.grouped_by_dir()
    assert len(grouped["~/src/alpha"]) == 2
    assert len(grouped["~/src/beta"]) == 1


def test_empty_store(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.json")
    assert store.all() == []
    assert store.get("nonexistent") is None
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_session_store.py -v
```
Expected: FAIL

- [ ] **Step 3: Create `harness_claw/session_store.py`**:

```python
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from harness_claw.session import Session


class SessionStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._sessions: dict[str, Session] = {}
        if path.exists():
            data = json.loads(path.read_text())
            for item in data:
                s = Session.from_dict(item)
                self._sessions[s.session_id] = s

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    def save(self, session: Session) -> None:
        self._sessions[session.session_id] = session
        self._flush()

    def delete(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._flush()

    def grouped_by_dir(self) -> dict[str, list[Session]]:
        result: dict[str, list[Session]] = defaultdict(list)
        for s in self._sessions.values():
            result[s.working_dir].append(s)
        return dict(result)

    def _flush(self) -> None:
        self._path.write_text(
            json.dumps([s.to_dict() for s in self._sessions.values()], indent=2)
        )
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_session_store.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add harness_claw/session_store.py tests/test_session_store.py
git commit -m "feat: add SessionStore with disk persistence"
```

---

### Task 5: Update BaseProvider + AnthropicProvider

Add optional `cwd` and `claude_session_id` params to `stream_chat`. `AnthropicProvider` accepts and ignores them.

**Files:**
- Modify: `harness_claw/providers/base.py`
- Modify: `harness_claw/providers/anthropic.py`

- [ ] **Step 1: Update `harness_claw/providers/base.py`**:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Callable, Awaitable


class BaseProvider:
    """
    Base class for AI providers.

    stream_chat yields dicts with type "token" (delta: str), "usage"
    (input_tokens: int, output_tokens: int), or "session_init"
    (claude_session_id: str) for ClaudeCodeProvider.

    cwd: working directory for the subprocess (ClaudeCodeProvider only).
    claude_session_id: existing Claude Code session ID to resume.
    """

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        max_tokens: int,
        cwd: str | None = None,
        claude_session_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError
        yield  # noqa: unreachable

    async def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_tokens: int,
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError
        yield  # noqa: unreachable
```

- [ ] **Step 2: Update `harness_claw/providers/anthropic.py`** — add `cwd` and `claude_session_id` to `stream_chat` signature (ignore them):

```python
    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        max_tokens: int,
        cwd: str | None = None,
        claude_session_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        async for event in self._stream_once(messages, system, model, [], max_tokens):
            if event["type"] in ("token", "usage"):
                yield event
```

(Only the `stream_chat` signature changes. The rest of `anthropic.py` is unchanged.)

- [ ] **Step 3: Run all tests**

```bash
uv run pytest tests/ -v
```
Expected: 18 passed (existing tests still pass)

- [ ] **Step 4: Commit**

```bash
git add harness_claw/providers/base.py harness_claw/providers/anthropic.py
git commit -m "feat: add cwd and claude_session_id params to BaseProvider"
```

---

### Task 6: ClaudeCodeProvider

Implement `ClaudeCodeProvider` using `asyncio.create_subprocess_exec`. Parse stream-json JSONL output. Handle `session_init`, `token`, `usage`, `permission_request`, and `error` events. Block on permission requests using `asyncio.Event`.

**Files:**
- Create: `harness_claw/providers/claude_code.py`
- Create: `tests/test_claude_code_provider.py`

- [ ] **Step 1: Write `tests/test_claude_code_provider.py`**:

```python
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from harness_claw.providers.claude_code import ClaudeCodeProvider


def make_jsonl(*events: dict) -> bytes:
    return b"\n".join(json.dumps(e).encode() for e in events) + b"\n"


async def mock_subprocess(stdout_data: bytes):
    """Returns a mock asyncio subprocess whose stdout yields lines."""
    proc = MagicMock()
    proc.returncode = 0

    lines = [line + b"\n" for line in stdout_data.split(b"\n") if line.strip()]

    async def readline():
        if lines:
            return lines.pop(0)
        return b""

    proc.stdout = MagicMock()
    proc.stdout.readline = readline

    async def wait():
        return 0

    proc.wait = wait
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.terminate = MagicMock()
    return proc


async def test_stream_chat_yields_tokens() -> None:
    stdout = make_jsonl(
        {"type": "system", "subtype": "init", "session_id": "abc-123", "tools": []},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hello"}]}},
        {"type": "result", "subtype": "success", "result": "Hello", "usage": {"input_tokens": 10, "output_tokens": 5}, "cost_usd": 0.001},
    )
    proc = await mock_subprocess(stdout)

    provider = ClaudeCodeProvider()
    events = []

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        async for event in provider.stream_chat(
            messages=[{"role": "user", "content": "Hi"}],
            system="You are helpful.",
            model="claude-sonnet-4-6",
            max_tokens=1024,
            cwd="/tmp",
            claude_session_id=None,
        ):
            events.append(event)

    types = [e["type"] for e in events]
    assert "session_init" in types
    assert "token" in types
    assert "usage" in types

    session_init = next(e for e in events if e["type"] == "session_init")
    assert session_init["claude_session_id"] == "abc-123"

    token = next(e for e in events if e["type"] == "token")
    assert token["delta"] == "Hello"

    usage = next(e for e in events if e["type"] == "usage")
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 5


async def test_stream_chat_uses_resume_flag() -> None:
    stdout = make_jsonl(
        {"type": "system", "subtype": "init", "session_id": "existing-id", "tools": []},
        {"type": "result", "subtype": "success", "result": "Done", "usage": {"input_tokens": 1, "output_tokens": 1}, "cost_usd": 0.0},
    )
    proc = await mock_subprocess(stdout)

    provider = ClaudeCodeProvider()
    captured_args = []

    async def capture(*args, **kwargs):
        captured_args.extend(args)
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=capture):
        async for _ in provider.stream_chat(
            messages=[],
            system="sys",
            model="claude-sonnet-4-6",
            max_tokens=1024,
            cwd="/tmp",
            claude_session_id="existing-id",
        ):
            pass

    assert "--resume" in captured_args
    idx = captured_args.index("--resume")
    assert captured_args[idx + 1] == "existing-id"


async def test_permission_request_flow() -> None:
    stdout = make_jsonl(
        {"type": "system", "subtype": "init", "session_id": "sess-1", "tools": []},
        {"type": "tool_input", "request_id": "req-1", "tool": {"name": "Bash"}, "input": {"command": "ls"}},
        {"type": "result", "subtype": "success", "result": "done", "usage": {"input_tokens": 1, "output_tokens": 1}, "cost_usd": 0.0},
    )
    proc = await mock_subprocess(stdout)
    provider = ClaudeCodeProvider()
    events = []

    async def resolve_after_start():
        await asyncio.sleep(0.05)
        provider.resolve_permission("req-1", approved=True)

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        task = asyncio.create_task(resolve_after_start())
        async for event in provider.stream_chat(
            messages=[{"role": "user", "content": "run ls"}],
            system="sys",
            model="claude-sonnet-4-6",
            max_tokens=1024,
            cwd="/tmp",
            claude_session_id=None,
        ):
            events.append(event)
        await task

    perm_events = [e for e in events if e["type"] == "permission_request"]
    assert len(perm_events) == 1
    assert perm_events[0]["tool_name"] == "Bash"
    assert perm_events[0]["request_id"] == "req-1"
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/test_claude_code_provider.py -v
```
Expected: FAIL

- [ ] **Step 3: Create `harness_claw/providers/claude_code.py`**:

```python
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
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--include-partial-messages",
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

        elif event_type == "tool_input":
            request_id = event.get("request_id", "")
            tool_name = event.get("tool", {}).get("name", "")
            tool_input = event.get("input", {})

            done_event = asyncio.Event()
            self._pending[request_id] = (done_event, None)

            yield {
                "type": "permission_request",
                "request_id": request_id,
                "tool_name": tool_name,
                "input": tool_input,
            }

            await done_event.wait()
            _, approved = self._pending.pop(request_id)

            response = json.dumps({"approved": bool(approved)}) + "\n"
            assert proc.stdin is not None
            proc.stdin.write(response.encode())
            await proc.stdin.drain()

        elif event_type == "result":
            usage = event.get("usage", {})
            yield {
                "type": "usage",
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cost_usd": event.get("cost_usd", 0.0),
            }
            if event.get("subtype") == "error":
                yield {"type": "error", "message": event.get("error", "Unknown error")}
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_claude_code_provider.py -v
```
Expected: all pass

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest tests/ -v
```
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add harness_claw/providers/claude_code.py tests/test_claude_code_provider.py
git commit -m "feat: add ClaudeCodeProvider with permission flow"
```

---

### Task 7: Update JobRunner

Replace agent-centric logic with session-centric logic. Use `SessionStore` instead of `AgentRegistry`. Support kill, resume, delete. Route permission events back to browser via a callback.

**Files:**
- Modify: `harness_claw/job_runner.py`
- Modify: `tests/test_job_runner.py`

- [ ] **Step 1: Rewrite `harness_claw/job_runner.py`**:

```python
from __future__ import annotations

import asyncio
import os
import shutil
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
            await send({"type": "session_update", "session_id": session_id, "name": session.name, "status": "running"})

        session.add_user_message(text)
        session.status = "running"
        self._store.save(session)

        await send({"type": "job_update", "job_id": job_id, "session_id": session_id, "status": "running", "progress": None, "title": text[:40]})

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
                    await send({"type": "token", "job_id": job_id, "delta": event["delta"]})
                elif event["type"] == "usage":
                    session.input_tokens += event["input_tokens"]
                    session.output_tokens += event["output_tokens"]
                    await send({
                        "type": "usage",
                        "job_id": job_id,
                        "input_tokens": session.input_tokens,
                        "output_tokens": session.output_tokens,
                        "cost_usd": session.cost_usd,
                    })
                elif event["type"] == "session_init":
                    session.claude_session_id = event["claude_session_id"]
                elif event["type"] == "permission_request":
                    await send({
                        "type": "permission_request",
                        "session_id": session_id,
                        "request_id": event["request_id"],
                        "tool_name": event["tool_name"],
                        "input": event["input"],
                    })
                elif event["type"] == "error":
                    await send({"type": "error", "job_id": job_id, "message": event["message"]})

        except asyncio.CancelledError:
            session.status = "killed"
            self._store.save(session)
            await send({"type": "job_update", "job_id": job_id, "session_id": session_id, "status": "failed", "progress": None, "title": text[:40]})
            await send({"type": "session_update", "session_id": session_id, "name": session.name, "status": "killed"})
            return ""

        session.add_assistant_message(full_response)
        session.status = "idle"
        self._store.save(session)

        await send({"type": "job_update", "job_id": job_id, "session_id": session_id, "status": "completed", "progress": 100, "title": text[:40]})
        await send({"type": "session_update", "session_id": session_id, "name": session.name, "status": "idle"})
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
        # Claude Code stores sessions at ~/.claude/projects/<encoded_cwd>/<session_id>.jsonl
        cwd = os.path.expanduser(session.working_dir)
        encoded = cwd.replace("/", "-").lstrip("-")
        claude_dir = Path.home() / ".claude" / "projects" / encoded
        session_file = claude_dir / f"{session.claude_session_id}.jsonl"
        if session_file.exists():
            session_file.unlink()
```

- [ ] **Step 2: Rewrite `tests/test_job_runner.py`** — update to use session-centric API with MockProvider:

```python
import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock
from pathlib import Path

import pytest

from harness_claw.job_runner import JobRunner, PROVIDERS
from harness_claw.providers.base import BaseProvider
from harness_claw.role_registry import RoleRegistry
from harness_claw.session import Session
from harness_claw.session_store import SessionStore


class MockProvider(BaseProvider):
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    async def stream_chat(self, messages, system, model, max_tokens, cwd=None, claude_session_id=None) -> AsyncIterator[dict[str, Any]]:
        for event in self._events:
            yield event


def make_registry(tmp_path: Path) -> RoleRegistry:
    yaml_file = tmp_path / "agents.yaml"
    yaml_file.write_text("""
roles:
  - id: general-purpose
    name: General Purpose
    provider: mock
    model: claude-sonnet-4-6
    system_prompt: "You are helpful."
    max_tokens: 1024
""")
    return RoleRegistry(yaml_file)


def make_runner(tmp_path: Path, events: list[dict[str, Any]]) -> tuple[JobRunner, SessionStore]:
    registry = make_registry(tmp_path)
    store = SessionStore(tmp_path / "sessions.json")
    PROVIDERS["mock"] = MockProvider(events)
    runner = JobRunner(registry, store)
    return runner, store


async def test_run_job_streams_tokens(tmp_path: Path) -> None:
    events = [
        {"type": "token", "delta": "Hello"},
        {"type": "token", "delta": " world"},
        {"type": "usage", "input_tokens": 5, "output_tokens": 10},
    ]
    runner, store = make_runner(tmp_path, events)
    session = Session(role_id="general-purpose", working_dir="~/src", model="claude-sonnet-4-6")
    store.save(session)

    sent = []
    await runner.run_job(session.session_id, "Hi", sent.append)

    token_events = [e for e in sent if e["type"] == "token"]
    assert len(token_events) == 2
    assert token_events[0]["delta"] == "Hello"
    assert token_events[1]["delta"] == " world"


async def test_run_job_sets_session_name(tmp_path: Path) -> None:
    runner, store = make_runner(tmp_path, [{"type": "token", "delta": "ok"}])
    session = Session(role_id="general-purpose", working_dir="~/src", model="claude-sonnet-4-6")
    store.save(session)

    sent = []
    await runner.run_job(session.session_id, "Write a sorting algorithm", sent.append)

    updated = store.get(session.session_id)
    assert updated.name == "Write a sorting algorithm"


async def test_run_job_status_lifecycle(tmp_path: Path) -> None:
    runner, store = make_runner(tmp_path, [{"type": "token", "delta": "done"}])
    session = Session(role_id="general-purpose", working_dir="~/src", model="claude-sonnet-4-6")
    store.save(session)

    sent = []
    await runner.run_job(session.session_id, "hello", sent.append)

    job_updates = [e for e in sent if e["type"] == "job_update"]
    statuses = [e["status"] for e in job_updates]
    assert "running" in statuses
    assert "completed" in statuses


async def test_run_job_accumulates_usage(tmp_path: Path) -> None:
    events = [
        {"type": "usage", "input_tokens": 10, "output_tokens": 20},
    ]
    runner, store = make_runner(tmp_path, events)
    session = Session(role_id="general-purpose", working_dir="~/src", model="claude-sonnet-4-6")
    store.save(session)

    sent = []
    await runner.run_job(session.session_id, "Hi", sent.append)

    updated = store.get(session.session_id)
    assert updated.input_tokens == 10
    assert updated.output_tokens == 20


async def test_delete_session(tmp_path: Path) -> None:
    runner, store = make_runner(tmp_path, [])
    session = Session(role_id="general-purpose", working_dir="~/src", model="claude-sonnet-4-6")
    store.save(session)

    runner.delete_session(session.session_id)
    assert store.get(session.session_id) is None
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/test_job_runner.py -v
```
Expected: all pass

- [ ] **Step 4: Run full suite**

```bash
uv run pytest tests/ -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add harness_claw/job_runner.py tests/test_job_runner.py
git commit -m "feat: update JobRunner to session-centric with kill/delete/permission flow"
```

---

### Task 8: Update server.py

Replace agent endpoints with session + role endpoints. Handle `permission_response`, `resume`, `cancel` WebSocket messages. Wire up `SessionStore` and `RoleRegistry`.

**Files:**
- Modify: `harness_claw/server.py`

- [ ] **Step 1: Rewrite `harness_claw/server.py`**:

```python
from __future__ import annotations

import asyncio
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
def create_session(req: CreateSessionRequest) -> dict[str, Any]:
    role = registry.get(req.role_id)
    if role is None:
        raise HTTPException(status_code=404, detail=f"Role {req.role_id!r} not found")
    session = Session(
        role_id=req.role_id,
        working_dir=req.working_dir,
        model=role.model,
    )
    store.save(session)
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

    async def sender() -> None:
        while True:
            msg = await queue.get()
            await ws.send_json(msg)

    sender_task = asyncio.create_task(sender())
    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")

            if msg_type == "chat":
                session_id = data["session_id"]
                text = data["text"]
                asyncio.create_task(runner.run_job(session_id, text, send))

            elif msg_type == "cancel":
                session_id = data.get("session_id", "")
                runner.kill_job(session_id)

            elif msg_type == "resume":
                session_id = data["session_id"]
                session = store.get(session_id)
                if session:
                    session.status = "idle"
                    store.save(session)
                    await send({"type": "session_update", "session_id": session_id, "name": session.name, "status": "idle"})

            elif msg_type == "permission_response":
                runner.resolve_permission(data["request_id"], approved=data["approved"])

    except WebSocketDisconnect:
        pass
    finally:
        sender_task.cancel()


# --- SPA ---

_dist = _root / "ui" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        return FileResponse(str(_dist / "index.html"))
```

- [ ] **Step 2: Run full backend tests**

```bash
uv run pytest tests/ -v
```
Expected: all pass

- [ ] **Step 3: Commit**

```bash
git add harness_claw/server.py
git commit -m "feat: update server with session/role endpoints and permission_response WS handling"
```

---

### Task 9: Update types.ts

Replace agent types with session types. Add new WS message shapes.

**Files:**
- Modify: `ui/src/types.ts`

- [ ] **Step 1: Rewrite `ui/src/types.ts`**:

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
  messages: Array<{ role: string; content: string }>
  input_tokens: number
  output_tokens: number
}

// Job/Task tracked in UI
export type JobStatus = 'queued' | 'running' | 'completed' | 'failed'

export interface Job {
  job_id: string
  session_id: string
  title: string
  status: JobStatus
  progress: number | null
}

// Pending permission request
export interface PendingPermission {
  request_id: string
  tool_name: string
  input: Record<string, unknown>
}

// UI-side session state (extends server data with local UI state)
export interface SessionState {
  session_id: string
  role_id: string
  working_dir: string
  model: string
  name: string
  status: 'idle' | 'running' | 'killed'
  messages: Message[]
  streamingMessages: Record<string, string>
  jobs: Job[]
  input_tokens: number
  output_tokens: number
  cost_usd: number
  tools: ToolInfo[]
  pendingPermissions: PendingPermission[]
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  tool_calls?: ToolCallEvent[]
}

export interface ToolCallEvent {
  tool_id: string
  name: string
  input: Record<string, unknown>
}

export interface ToolInfo {
  name: string
  description: string
}

// WebSocket: server → client
export type WSIncoming =
  | { type: 'token'; job_id: string; delta: string }
  | { type: 'job_update'; job_id: string; session_id: string; status: JobStatus; progress: number | null; title: string }
  | { type: 'tool_call'; job_id: string; tool_name: string; input: Record<string, unknown> }
  | { type: 'usage'; job_id: string; input_tokens: number; output_tokens: number; cost_usd: number }
  | { type: 'error'; job_id: string; message: string }
  | { type: 'permission_request'; session_id: string; request_id: string; tool_name: string; input: Record<string, unknown> }
  | { type: 'session_update'; session_id: string; name: string; status: 'idle' | 'running' | 'killed' }
  | { type: 'session_deleted'; session_id: string }

// WebSocket: client → server
export type WSSend =
  | { type: 'chat'; session_id: string; text: string }
  | { type: 'cancel'; session_id: string }
  | { type: 'resume'; session_id: string }
  | { type: 'permission_response'; request_id: string; approved: boolean }
```

- [ ] **Step 2: Verify build compiles** (check for type errors only):

```bash
cd ui && npx tsc --noEmit
```
Expected: errors about missing components (that's fine — they get fixed in later tasks)

- [ ] **Step 3: Commit**

```bash
git add ui/src/types.ts
git commit -m "feat: update types for session-centric model with permission and session_update messages"
```

---

### Task 10: SessionSidebar + SessionCreatePanel

Replace `AgentSidebar.tsx` with `SessionSidebar.tsx`. Add `SessionCreatePanel.tsx`.

**Files:**
- Create: `ui/src/components/SessionSidebar.tsx`
- Create: `ui/src/components/SessionCreatePanel.tsx`
- Delete: `ui/src/components/AgentSidebar.tsx`

- [ ] **Step 1: Create `ui/src/components/SessionSidebar.tsx`**:

```tsx
import type { SessionState } from '../types'

interface Props {
  sessions: Record<string, SessionState>
  activeSessionId: string | null
  onSelect: (sessionId: string) => void
  onNewSession: () => void
  onDelete: (sessionId: string) => void
  onKill: (sessionId: string) => void
}

function statusDot(status: string): string {
  if (status === 'running') return '●'
  if (status === 'killed') return '✕'
  return '○'
}

function statusColor(status: string): string {
  if (status === 'running') return 'text-blue-400'
  if (status === 'killed') return 'text-red-400'
  return 'text-gray-500'
}

export function SessionSidebar({ sessions, activeSessionId, onSelect, onNewSession, onDelete, onKill }: Props) {
  // Group sessions by working_dir
  const grouped: Record<string, SessionState[]> = {}
  for (const s of Object.values(sessions)) {
    if (!grouped[s.working_dir]) grouped[s.working_dir] = []
    grouped[s.working_dir].push(s)
  }

  return (
    <div className="w-56 flex-shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col h-full">
      <div className="flex-1 overflow-y-auto py-2">
        {Object.entries(grouped).map(([dir, dirSessions]) => (
          <div key={dir} className="mb-3">
            <div className="px-3 py-1 text-xs text-gray-500 font-medium truncate" title={dir}>
              {dir.replace(/^~\/src\//, '')}
            </div>
            {dirSessions.map((s) => (
              <div
                key={s.session_id}
                className={`group flex items-center px-3 py-2 cursor-pointer text-sm hover:bg-gray-800 ${
                  s.session_id === activeSessionId ? 'bg-gray-800 text-white' : 'text-gray-400'
                }`}
                onClick={() => onSelect(s.session_id)}
              >
                <span className={`mr-1.5 text-xs ${statusColor(s.status)}`}>{statusDot(s.status)}</span>
                <span className="flex-1 truncate">{s.name || 'New session'}</span>
                {s.status === 'running' && (
                  <button
                    className="hidden group-hover:block text-gray-500 hover:text-red-400 ml-1 text-xs"
                    onClick={(e) => { e.stopPropagation(); onKill(s.session_id) }}
                    title="Kill"
                  >■</button>
                )}
                {s.status !== 'running' && (
                  <button
                    className="hidden group-hover:block text-gray-500 hover:text-red-400 ml-1 text-xs"
                    onClick={(e) => { e.stopPropagation(); onDelete(s.session_id) }}
                    title="Delete"
                  >✕</button>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="p-3 border-t border-gray-800">
        <button
          onClick={onNewSession}
          className="w-full text-left text-sm text-gray-400 hover:text-white py-1"
        >
          + New Session
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create `ui/src/components/SessionCreatePanel.tsx`**:

```tsx
import { useState } from 'react'
import type { RoleConfig } from '../types'

interface Props {
  roles: RoleConfig[]
  onCreate: (roleId: string, workingDir: string) => void
  onCancel: () => void
}

export function SessionCreatePanel({ roles, onCreate, onCancel }: Props) {
  const [roleId, setRoleId] = useState(roles.find(r => r.id === 'general-purpose')?.id ?? roles[0]?.id ?? '')
  const [workingDir, setWorkingDir] = useState('~/src')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (roleId && workingDir.trim()) {
      onCreate(roleId, workingDir.trim())
    }
  }

  return (
    <div className="flex-1 flex items-center justify-center bg-gray-950">
      <form onSubmit={handleSubmit} className="w-96 bg-gray-900 rounded-lg p-6 flex flex-col gap-4 border border-gray-800">
        <h2 className="text-white text-lg font-semibold">New Session</h2>

        <div className="flex flex-col gap-1">
          <label className="text-gray-400 text-sm">Directory</label>
          <input
            type="text"
            value={workingDir}
            onChange={(e) => setWorkingDir(e.target.value)}
            className="bg-gray-800 text-white text-sm rounded px-3 py-2 border border-gray-700 focus:outline-none focus:border-blue-500"
            placeholder="~/src/my-project"
          />
          <span className="text-gray-600 text-xs">Path within ~/src</span>
        </div>

        <div className="flex flex-col gap-1">
          <label className="text-gray-400 text-sm">Role</label>
          <select
            value={roleId}
            onChange={(e) => setRoleId(e.target.value)}
            className="bg-gray-800 text-white text-sm rounded px-3 py-2 border border-gray-700 focus:outline-none focus:border-blue-500"
          >
            {roles.map((r) => (
              <option key={r.id} value={r.id}>{r.name}</option>
            ))}
          </select>
        </div>

        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 text-sm text-gray-400 hover:text-white"
          >
            Cancel
          </button>
          <button
            type="submit"
            className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded"
          >
            Create
          </button>
        </div>
      </form>
    </div>
  )
}
```

- [ ] **Step 3: Delete old file**

```bash
git rm ui/src/components/AgentSidebar.tsx
```

- [ ] **Step 4: Commit**

```bash
git add ui/src/components/SessionSidebar.tsx ui/src/components/SessionCreatePanel.tsx
git commit -m "feat: add SessionSidebar grouped by directory and SessionCreatePanel"
```

---

### Task 11: PermissionDialog + WorkTab + TasksTab

Create `PermissionDialog.tsx` (inline approve/deny card). Create `WorkTab.tsx` (replaces `ChatPanel.tsx`) with permission dialogs embedded. Create `TasksTab.tsx` (replaces `JobsPanel.tsx`).

**Files:**
- Create: `ui/src/components/PermissionDialog.tsx`
- Create: `ui/src/components/WorkTab.tsx`
- Create: `ui/src/components/TasksTab.tsx`
- Delete: `ui/src/components/ChatPanel.tsx`
- Delete: `ui/src/components/JobsPanel.tsx`

- [ ] **Step 1: Create `ui/src/components/PermissionDialog.tsx`**:

```tsx
import type { PendingPermission } from '../types'

interface Props {
  permission: PendingPermission
  onAllow: (requestId: string) => void
  onDeny: (requestId: string) => void
}

export function PermissionDialog({ permission, onAllow, onDeny }: Props) {
  const inputStr = Object.entries(permission.input)
    .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join('\n')

  return (
    <div className="mx-4 my-2 bg-gray-800 border border-yellow-600 rounded-lg p-3 flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <span className="text-yellow-500 text-sm">🔧</span>
        <span className="text-yellow-400 text-sm font-medium">{permission.tool_name}</span>
      </div>
      {inputStr && (
        <pre className="text-gray-300 text-xs bg-gray-900 rounded p-2 overflow-x-auto whitespace-pre-wrap">
          {inputStr}
        </pre>
      )}
      <div className="flex gap-2 justify-end">
        <button
          onClick={() => onDeny(permission.request_id)}
          className="px-3 py-1 text-xs text-gray-400 hover:text-white border border-gray-700 hover:border-gray-500 rounded"
        >
          Deny
        </button>
        <button
          onClick={() => onAllow(permission.request_id)}
          className="px-3 py-1 text-xs bg-green-700 hover:bg-green-600 text-white rounded"
        >
          Allow
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create `ui/src/components/WorkTab.tsx`**:

```tsx
import { useEffect, useRef, useCallback } from 'react'
import type { Message, PendingPermission } from '../types'
import { PermissionDialog } from './PermissionDialog'

interface Props {
  messages: Message[]
  streamingMessages: Record<string, string>
  pendingPermissions: PendingPermission[]
  onSend: (text: string) => void
  onAllow: (requestId: string) => void
  onDeny: (requestId: string) => void
}

export function WorkTab({ messages, streamingMessages, pendingPermissions, onSend, onAllow, onDeny }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingMessages, pendingPermissions])

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      const text = e.currentTarget.value.trim()
      if (text) {
        onSend(text)
        e.currentTarget.value = ''
      }
    }
  }, [onSend])

  const streamingEntries = Object.entries(streamingMessages)

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-3">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`max-w-3xl ${msg.role === 'user' ? 'self-end' : 'self-start'}`}
          >
            {msg.tool_calls?.length ? (
              <div className="bg-gray-800 border border-gray-700 rounded-lg p-3 text-sm text-gray-300">
                {msg.tool_calls.map((tc) => (
                  <div key={tc.tool_id} className="text-yellow-400">→ {tc.name}</div>
                ))}
              </div>
            ) : (
              <div
                className={`rounded-lg px-4 py-2 text-sm whitespace-pre-wrap ${
                  msg.role === 'user'
                    ? 'bg-blue-700 text-white'
                    : 'bg-gray-800 text-gray-100'
                }`}
              >
                {msg.content}
              </div>
            )}
          </div>
        ))}

        {pendingPermissions.map((p) => (
          <PermissionDialog key={p.request_id} permission={p} onAllow={onAllow} onDeny={onDeny} />
        ))}

        {streamingEntries.map(([jobId, text]) => (
          <div key={jobId} className="self-start max-w-3xl bg-gray-800 text-gray-100 rounded-lg px-4 py-2 text-sm whitespace-pre-wrap">
            {text}
            <span className="inline-block w-1.5 h-4 bg-gray-400 ml-0.5 animate-pulse align-middle" />
          </div>
        ))}

        <div ref={bottomRef} />
      </div>

      <div className="p-3 border-t border-gray-800 flex gap-2">
        <textarea
          ref={textareaRef}
          rows={1}
          placeholder="Message..."
          className="flex-1 bg-gray-800 text-white text-sm rounded px-3 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-blue-500"
          onKeyDown={handleKeyDown}
        />
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Create `ui/src/components/TasksTab.tsx`**:

```tsx
import type { Job } from '../types'

interface Props {
  jobs: Job[]
}

function statusBadge(status: string): string {
  if (status === 'running') return '● Running'
  if (status === 'completed') return '✓ Done'
  if (status === 'failed') return '✕ Failed'
  return '◌ Queued'
}

function statusColor(status: string): string {
  if (status === 'running') return 'text-blue-400'
  if (status === 'completed') return 'text-green-400'
  if (status === 'failed') return 'text-red-400'
  return 'text-gray-500'
}

export function TasksTab({ jobs }: Props) {
  if (jobs.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
        No tasks yet
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-2">
      {[...jobs].reverse().map((job) => (
        <div key={job.job_id} className="bg-gray-800 rounded-lg p-3 flex flex-col gap-1.5 border border-gray-700">
          <div className="text-sm text-gray-200 truncate">{job.title || job.job_id}</div>
          <div className={`text-xs ${statusColor(job.status)}`}>{statusBadge(job.status)}</div>
          {job.status === 'running' && (
            <div className="h-1 bg-gray-700 rounded-full overflow-hidden">
              {job.progress !== null ? (
                <div
                  className="h-full bg-blue-500 rounded-full transition-all"
                  style={{ width: `${job.progress}%` }}
                />
              ) : (
                <div className="h-full bg-blue-500 rounded-full animate-pulse w-1/2" />
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 4: Delete old files**

```bash
git rm ui/src/components/ChatPanel.tsx ui/src/components/JobsPanel.tsx
```

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/PermissionDialog.tsx ui/src/components/WorkTab.tsx ui/src/components/TasksTab.tsx
git commit -m "feat: add PermissionDialog, WorkTab, TasksTab"
```

---

### Task 12: AgentTab + ToolsTab + TabPanel

**Files:**
- Create: `ui/src/components/AgentTab.tsx`
- Create: `ui/src/components/ToolsTab.tsx`
- Create: `ui/src/components/TabPanel.tsx`

- [ ] **Step 1: Create `ui/src/components/AgentTab.tsx`**:

```tsx
import type { RoleConfig, SessionState } from '../types'

interface Props {
  session: SessionState
  role: RoleConfig | undefined
}

export function AgentTab({ session, role }: Props) {
  return (
    <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-4 text-sm">
      <div className="flex flex-col gap-1">
        <span className="text-gray-500 text-xs uppercase tracking-wide">Role</span>
        <span className="text-white">{role?.name ?? session.role_id}</span>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-gray-500 text-xs uppercase tracking-wide">Model</span>
        <span className="text-gray-300">{session.model}</span>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-gray-500 text-xs uppercase tracking-wide">Working Directory</span>
        <span className="text-gray-300 font-mono text-xs">{session.working_dir}</span>
      </div>
      {session.claude_session_id && (
        <div className="flex flex-col gap-1">
          <span className="text-gray-500 text-xs uppercase tracking-wide">Session ID</span>
          <span className="text-gray-500 font-mono text-xs truncate" title={session.claude_session_id}>
            {session.claude_session_id}
          </span>
        </div>
      )}
      {role?.system_prompt && (
        <div className="flex flex-col gap-1">
          <span className="text-gray-500 text-xs uppercase tracking-wide">System Prompt</span>
          <pre className="text-gray-300 text-xs bg-gray-800 rounded p-3 whitespace-pre-wrap border border-gray-700">
            {role.system_prompt}
          </pre>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Create `ui/src/components/ToolsTab.tsx`**:

```tsx
import type { ToolInfo } from '../types'

interface Props {
  tools: ToolInfo[]
}

export function ToolsTab({ tools }: Props) {
  if (tools.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
        Tools will appear once a session starts
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-2">
      {tools.map((tool) => (
        <div key={tool.name} className="bg-gray-800 rounded-lg p-3 border border-gray-700 flex flex-col gap-1">
          <span className="text-sm text-white font-medium">{tool.name}</span>
          {tool.description && (
            <span className="text-xs text-gray-400">{tool.description}</span>
          )}
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 3: Create `ui/src/components/TabPanel.tsx`**:

```tsx
import { useState } from 'react'

export type TabId = 'work' | 'tasks' | 'agent' | 'tools'

interface Tab {
  id: TabId
  label: string
}

const TABS: Tab[] = [
  { id: 'work', label: 'Work' },
  { id: 'tasks', label: 'Tasks' },
  { id: 'agent', label: 'Agent' },
  { id: 'tools', label: 'Tools' },
]

interface Props {
  children: (activeTab: TabId) => React.ReactNode
}

export function TabPanel({ children }: Props) {
  const [activeTab, setActiveTab] = useState<TabId>('work')

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <div className="flex border-b border-gray-800 bg-gray-900">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-2 text-sm border-b-2 transition-colors ${
              tab.id === activeTab
                ? 'border-blue-500 text-white'
                : 'border-transparent text-gray-500 hover:text-gray-300'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="flex flex-1 min-h-0 flex-col">
        {children(activeTab)}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Commit**

```bash
git add ui/src/components/AgentTab.tsx ui/src/components/ToolsTab.tsx ui/src/components/TabPanel.tsx
git commit -m "feat: add AgentTab, ToolsTab, TabPanel"
```

---

### Task 13: Update SessionCostBar + delete AgentConfigPanel

**Files:**
- Modify: `ui/src/components/SessionCostBar.tsx`
- Delete: `ui/src/components/AgentConfigPanel.tsx`

- [ ] **Step 1: Rewrite `ui/src/components/SessionCostBar.tsx`**:

```tsx
interface Props {
  model: string
  inputTokens: number
  outputTokens: number
  costUsd: number
  sessionName: string
  status: 'idle' | 'running' | 'killed'
}

export function SessionCostBar({ model, inputTokens, outputTokens, costUsd, sessionName, status }: Props) {
  const totalTokens = inputTokens + outputTokens

  return (
    <div className="flex items-center justify-between px-4 py-2 border-b border-gray-800 bg-gray-900 text-xs text-gray-400">
      <div className="flex items-center gap-2">
        <span className={status === 'running' ? 'text-blue-400' : status === 'killed' ? 'text-red-400' : 'text-gray-500'}>
          {status === 'running' ? '●' : status === 'killed' ? '✕' : '○'}
        </span>
        <span className="text-gray-300 font-medium truncate max-w-xs">{sessionName || 'New session'}</span>
        <span className="text-gray-600">·</span>
        <span>{model}</span>
      </div>
      <div className="flex items-center gap-3">
        <span>{totalTokens.toLocaleString()} tokens</span>
        <span className="text-green-400">${costUsd.toFixed(4)}</span>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Delete old file**

```bash
git rm ui/src/components/AgentConfigPanel.tsx
```

- [ ] **Step 3: Commit**

```bash
git add ui/src/components/SessionCostBar.tsx
git commit -m "feat: update SessionCostBar with session name and status"
```

---

### Task 14: Rewrite App.tsx

Wire everything together with session-centric state management.

**Files:**
- Modify: `ui/src/App.tsx`

- [ ] **Step 1: Rewrite `ui/src/App.tsx`**:

```tsx
import { useEffect, useRef, useState, useCallback } from 'react'
import { WsClient } from './ws'
import type { RoleConfig, SessionState, Job, Message, WSIncoming, ToolInfo } from './types'
import { SessionSidebar } from './components/SessionSidebar'
import { SessionCreatePanel } from './components/SessionCreatePanel'
import { SessionCostBar } from './components/SessionCostBar'
import { TabPanel } from './components/TabPanel'
import { WorkTab } from './components/WorkTab'
import { TasksTab } from './components/TasksTab'
import { AgentTab } from './components/AgentTab'
import { ToolsTab } from './components/ToolsTab'

function emptySessionState(data: { session_id: string; role_id: string; working_dir: string; model: string; name: string; status: 'idle' | 'running' | 'killed' }): SessionState {
  return {
    ...data,
    messages: [],
    streamingMessages: {},
    jobs: [],
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    tools: [],
    pendingPermissions: [],
  }
}

let msgCounter = 0
function nextId() { return String(++msgCounter) }

export default function App() {
  const [roles, setRoles] = useState<RoleConfig[]>([])
  const [sessions, setSessions] = useState<Record<string, SessionState>>({})
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const wsRef = useRef<WsClient | null>(null)
  const jobSessionMap = useRef<Record<string, string>>({})  // job_id → session_id

  // Load roles + sessions from REST
  useEffect(() => {
    fetch('/api/roles').then(r => r.json()).then(setRoles).catch(console.error)
    fetch('/api/sessions').then(r => r.json()).then((grouped: Record<string, Array<{
      session_id: string; role_id: string; working_dir: string; model: string;
      name: string; status: 'idle' | 'running' | 'killed'; claude_session_id: string | null;
      input_tokens: number; output_tokens: number;
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

    if (msg.type === 'job_update') {
      const sessionId = msg.session_id
      jobSessionMap.current[msg.job_id] = sessionId
      setSessions(prev => {
        const existing = prev[sessionId]
        if (!existing) return prev
        const existingJob = existing.jobs.find(j => j.job_id === msg.job_id)
        let updatedJobs: Job[]
        if (!existingJob) {
          updatedJobs = [...existing.jobs, { job_id: msg.job_id, session_id: sessionId, title: msg.title ?? '', status: msg.status, progress: msg.progress }]
        } else {
          updatedJobs = existing.jobs.map(j => j.job_id === msg.job_id ? { ...j, status: msg.status, progress: msg.progress } : j)
        }
        let updatedMessages = existing.messages
        let updatedStreaming = existing.streamingMessages
        if (msg.status === 'completed' && existing.streamingMessages[msg.job_id]) {
          const text = existing.streamingMessages[msg.job_id]
          updatedMessages = [...existing.messages, { id: nextId(), role: 'assistant' as const, content: text }]
          const { [msg.job_id]: _, ...rest } = existing.streamingMessages
          updatedStreaming = rest
        }
        return { ...prev, [sessionId]: { ...existing, jobs: updatedJobs, messages: updatedMessages, streamingMessages: updatedStreaming } }
      })
    } else if (msg.type === 'token') {
      const sessionId = jobSessionMap.current[msg.job_id]
      if (!sessionId) return
      setSessions(prev => {
        const existing = prev[sessionId]
        if (!existing) return prev
        return { ...prev, [sessionId]: { ...existing, streamingMessages: { ...existing.streamingMessages, [msg.job_id]: (existing.streamingMessages[msg.job_id] ?? '') + msg.delta } } }
      })
    } else if (msg.type === 'usage') {
      const sessionId = jobSessionMap.current[msg.job_id]
      if (!sessionId) return
      setSessions(prev => {
        const existing = prev[sessionId]
        if (!existing) return prev
        return { ...prev, [sessionId]: { ...existing, input_tokens: existing.input_tokens + msg.input_tokens, output_tokens: existing.output_tokens + msg.output_tokens, cost_usd: existing.cost_usd + msg.cost_usd } }
      })
    } else if (msg.type === 'error') {
      const sessionId = jobSessionMap.current[msg.job_id]
      if (!sessionId) return
      setSessions(prev => {
        const existing = prev[sessionId]
        if (!existing) return prev
        return { ...prev, [sessionId]: { ...existing, messages: [...existing.messages, { id: nextId(), role: 'assistant' as const, content: `⚠ Error: ${msg.message}` }] } }
      })
    } else if (msg.type === 'permission_request') {
      setSessions(prev => {
        const existing = prev[msg.session_id]
        if (!existing) return prev
        return { ...prev, [msg.session_id]: { ...existing, pendingPermissions: [...existing.pendingPermissions, { request_id: msg.request_id, tool_name: msg.tool_name, input: msg.input }] } }
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
    } else if (msg.type === 'tool_call') {
      const sessionId = jobSessionMap.current[msg.job_id]
      if (!sessionId) return
      setSessions(prev => {
        const existing = prev[sessionId]
        if (!existing) return prev
        return { ...prev, [sessionId]: { ...existing, messages: [...existing.messages, { id: nextId(), role: 'assistant' as const, content: `→ Calling: ${msg.tool_name}`, tool_calls: [{ tool_id: msg.job_id, name: msg.tool_name, input: msg.input }] }] } }
      })
    }
  }, [])

  useEffect(() => {
    wsRef.current = new WsClient(handleWsMessage)
    return () => wsRef.current?.destroy()
  }, [handleWsMessage])

  const handleSend = useCallback((text: string) => {
    if (!activeSessionId) return
    setSessions(prev => {
      const existing = prev[activeSessionId]
      if (!existing) return prev
      return { ...prev, [activeSessionId]: { ...existing, messages: [...existing.messages, { id: nextId(), role: 'user', content: text }] } }
    })
    wsRef.current?.send({ type: 'chat', session_id: activeSessionId, text })
  }, [activeSessionId])

  const handleAllow = useCallback((requestId: string) => {
    wsRef.current?.send({ type: 'permission_response', request_id: requestId, approved: true })
    setSessions(prev => {
      if (!activeSessionId) return prev
      const existing = prev[activeSessionId]
      if (!existing) return prev
      return { ...prev, [activeSessionId]: { ...existing, pendingPermissions: existing.pendingPermissions.filter(p => p.request_id !== requestId) } }
    })
  }, [activeSessionId])

  const handleDeny = useCallback((requestId: string) => {
    wsRef.current?.send({ type: 'permission_response', request_id: requestId, approved: false })
    setSessions(prev => {
      if (!activeSessionId) return prev
      const existing = prev[activeSessionId]
      if (!existing) return prev
      return { ...prev, [activeSessionId]: { ...existing, pendingPermissions: existing.pendingPermissions.filter(p => p.request_id !== requestId) } }
    })
  }, [activeSessionId])

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
                  <WorkTab
                    messages={activeSession.messages}
                    streamingMessages={activeSession.streamingMessages}
                    pendingPermissions={activeSession.pendingPermissions}
                    onSend={handleSend}
                    onAllow={handleAllow}
                    onDeny={handleDeny}
                  />
                )
                if (activeTab === 'tasks') return <TasksTab jobs={activeSession.jobs} />
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
cd ui && npm run build
```
Expected: success, no TypeScript errors

- [ ] **Step 3: Commit**

```bash
git add ui/src/App.tsx
git commit -m "feat: rewrite App with session-centric state, tabs, and permission flow"
```

---

### Task 15: Verify full stack + update README

- [ ] **Step 1: Run all backend tests**

```bash
uv run pytest tests/ -v
```
Expected: all pass

- [ ] **Step 2: Build frontend**

```bash
cd ui && npm run build
```
Expected: success

- [ ] **Step 3: Update README.md**

```markdown
# HarnessClaw

A locally-run multi-agent dashboard powered by Claude Code. Chat with AI agents in your browser, with file system access, tool execution, and live permission approval.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Node 18+
- [Claude Code CLI](https://claude.ai/code) installed and authenticated

## Setup

```bash
# Install Python dependencies
uv sync

# Install frontend dependencies
cd ui && npm install && cd ..
```

## Run

```bash
uv run harnessclaw run
```

Opens:
- Backend: http://localhost:8000
- Frontend: http://localhost:5173

## Test

```bash
uv run pytest tests/ -v
```

## Sessions

Sessions are persisted in `sessions.json`. Each session runs in a working directory and uses Claude Code's built-in tools (Bash, Edit, Read, etc.). Permission requests appear inline in the Work tab — click Allow or Deny before execution continues.
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: update README for uv and harnessclaw run"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| ClaudeCodeProvider (subprocess, stream-json) | Task 6 |
| BaseProvider cwd + claude_session_id params | Task 5 |
| Session model (role_id, working_dir, name, status, claude_session_id) | Task 2 |
| RoleRegistry (loads agents.yaml roles) | Task 3 |
| SessionStore (disk persistence, grouped_by_dir) | Task 4 |
| JobRunner session-centric, kill, delete, permission routing | Task 7 |
| Server session/role REST endpoints | Task 8 |
| permission_response WS message handling | Task 8 |
| session_update / session_deleted WS messages | Task 7, 8 |
| uv project + harnessclaw run CLI | Task 1 |
| types.ts session types + new WS messages | Task 9 |
| SessionSidebar grouped by directory | Task 10 |
| SessionCreatePanel (role picker + dir input) | Task 10 |
| Work/Tasks/Agent/Tools tabs | Task 11, 12, 13 |
| PermissionDialog inline in Work tab | Task 11 |
| SessionCostBar with session name + status | Task 13 |
| App.tsx session-centric state + permission flow | Task 14 |

All spec requirements covered. No placeholders remain.
