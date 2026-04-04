# Observability Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add real-time agent observability — current tool + elapsed time, recent activity timeline, and stuck detection — with a provider abstraction so non-Claude runtimes can be added later.

**Architecture:** A new `harness_claw/observability/` module holds models, an in-memory store, and a provider protocol. `ClaudeCodeProvider` injects Claude Code hooks into the agent's working directory; hooks POST to a new `/agent/tool-event` endpoint. `CostPoller` is extended to backfill the timeline from JSONL. The UI shows a live tool badge in the sidebar and an activity timeline in `AgentTab`.

**Tech Stack:** Python dataclasses, `collections.deque`, FastAPI, React + TypeScript, Tailwind CSS

---

## File Map

| Action | Path | Notes |
|--------|------|-------|
| Create | `harness_claw/observability/__init__.py` | empty |
| Create | `harness_claw/observability/models.py` | `ToolEvent`, `AgentStatus` |
| Create | `harness_claw/observability/store.py` | `AgentObservabilityStore` |
| Create | `harness_claw/observability/provider.py` | protocol, `NullProvider`, `ClaudeCodeProvider` |
| Modify | `harness_claw/runtime/cost_poller.py` | add JSONL tool-event backfill |
| Modify | `harness_claw/runtime/job_runner.py` | integrate provider + store |
| Modify | `harness_claw/server.py` | new endpoints, wire store into runner |
| Modify | `ui/src/types.ts` | `ToolEvent`, `AgentStatus`, WS message type |
| Modify | `ui/src/App.tsx` | handle `agent_status` WS messages |
| Modify | `ui/src/components/SessionSidebar.tsx` | live tool badge |
| Modify | `ui/src/components/AgentTab.tsx` | activity timeline |
| Create | `tests/observability/__init__.py` | empty |
| Create | `tests/observability/test_store.py` | store + stuck detection |
| Create | `tests/observability/test_provider.py` | hook injection |
| Modify | `tests/runtime/test_cost_poller.py` | JSONL tool-event tests |

---

## Task 1: Observability models

**Files:**
- Create: `harness_claw/observability/__init__.py`
- Create: `harness_claw/observability/models.py`
- Create: `tests/observability/__init__.py`

- [ ] **Step 1: Create the empty `__init__` files**

```bash
touch harness_claw/observability/__init__.py tests/observability/__init__.py
mkdir -p tests/observability
```

- [ ] **Step 2: Write `harness_claw/observability/models.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ToolEvent:
    tool_name: str
    started_at: datetime
    ended_at: datetime | None   # None if still in progress
    duration_ms: int | None
    session_id: str


@dataclass
class AgentStatus:
    session_id: str
    current_tool: str | None
    current_tool_started_at: datetime | None
    last_output_at: datetime | None
    recent_activity: list[ToolEvent]   # newest first, max 50
    is_stuck: bool
    stuck_reason: str | None           # "tool_timeout" | "tool_loop" | "output_silence" | None

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "current_tool": self.current_tool,
            "current_tool_started_at": (
                self.current_tool_started_at.isoformat() if self.current_tool_started_at else None
            ),
            "last_output_at": (
                self.last_output_at.isoformat() if self.last_output_at else None
            ),
            "recent_activity": [
                {
                    "tool_name": e.tool_name,
                    "started_at": e.started_at.isoformat(),
                    "ended_at": e.ended_at.isoformat() if e.ended_at else None,
                    "duration_ms": e.duration_ms,
                    "session_id": e.session_id,
                }
                for e in self.recent_activity
            ],
            "is_stuck": self.is_stuck,
            "stuck_reason": self.stuck_reason,
        }
```

- [ ] **Step 3: Commit**

```bash
git add harness_claw/observability/__init__.py harness_claw/observability/models.py tests/observability/__init__.py
git commit -m "feat(observability): add ToolEvent and AgentStatus models"
```

---

## Task 2: AgentObservabilityStore

**Files:**
- Create: `harness_claw/observability/store.py`
- Create: `tests/observability/test_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/observability/test_store.py
from __future__ import annotations

from datetime import datetime, timedelta
import pytest

from harness_claw.observability.store import AgentObservabilityStore


def make_store() -> AgentObservabilityStore:
    s = AgentObservabilityStore()
    s.init_session("s1")
    return s


def test_tool_start_sets_current_tool():
    store = make_store()
    t = datetime(2026, 1, 1, 12, 0, 0)
    store.update_tool_start("s1", "Bash", t)
    status = store.get_status("s1")
    assert status is not None
    assert status.current_tool == "Bash"
    assert status.current_tool_started_at == t


def test_tool_end_clears_current_tool_and_appends_event():
    store = make_store()
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    t1 = datetime(2026, 1, 1, 12, 0, 2)
    store.update_tool_start("s1", "Bash", t0)
    store.update_tool_end("s1", "Bash", t1, 2000)
    status = store.get_status("s1")
    assert status.current_tool is None
    assert len(status.recent_activity) == 1
    ev = status.recent_activity[0]
    assert ev.tool_name == "Bash"
    assert ev.duration_ms == 2000


def test_recent_activity_capped_at_50():
    store = make_store()
    for i in range(55):
        t0 = datetime(2026, 1, 1, 12, 0, i)
        t1 = datetime(2026, 1, 1, 12, 0, i, 500000)
        store.update_tool_start("s1", f"Tool{i}", t0)
        store.update_tool_end("s1", f"Tool{i}", t1, 500)
    status = store.get_status("s1")
    assert len(status.recent_activity) == 50


def test_stuck_tool_timeout():
    store = make_store()
    old = datetime.utcnow() - timedelta(minutes=6)
    store.update_tool_start("s1", "Bash", old)
    status = store.get_status("s1")
    assert status.is_stuck is True
    assert status.stuck_reason == "tool_timeout"


def test_stuck_tool_loop():
    store = make_store()
    for i in range(10):
        t0 = datetime(2026, 1, 1, 12, 0, i)
        t1 = datetime(2026, 1, 1, 12, 0, i, 500000)
        store.update_tool_start("s1", "Bash", t0)
        store.update_tool_end("s1", "Bash", t1, 500)
    status = store.get_status("s1")
    assert status.is_stuck is True
    assert status.stuck_reason == "tool_loop"


def test_stuck_output_silence():
    store = make_store()
    old = datetime.utcnow() - timedelta(minutes=6)
    store.update_output("s1", old)
    status = store.get_status("s1")
    assert status.is_stuck is True
    assert status.stuck_reason == "output_silence"


def test_not_stuck_after_recent_output():
    store = make_store()
    old = datetime.utcnow() - timedelta(minutes=6)
    store.update_output("s1", old)
    store.update_output("s1", datetime.utcnow())
    status = store.get_status("s1")
    assert status.is_stuck is False


def test_remove_session():
    store = make_store()
    store.remove_session("s1")
    assert store.get_status("s1") is None
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/observability/test_store.py -v
```

Expected: `ImportError` or `ModuleNotFoundError` (store doesn't exist yet).

- [ ] **Step 3: Write `harness_claw/observability/store.py`**

```python
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Deque

from harness_claw.observability.models import AgentStatus, ToolEvent

_TOOL_TIMEOUT_SECONDS = 300   # 5 minutes
_OUTPUT_SILENCE_SECONDS = 300  # 5 minutes
_LOOP_THRESHOLD = 10           # same tool N times in a row


@dataclass
class _SessionObs:
    session_id: str
    current_tool: str | None = None
    current_tool_started_at: datetime | None = None
    last_output_at: datetime | None = None
    recent_activity: Deque[ToolEvent] = field(default_factory=lambda: deque(maxlen=50))


def _compute_stuck(obs: _SessionObs) -> tuple[bool, str | None]:
    now = datetime.utcnow()

    if obs.current_tool and obs.current_tool_started_at:
        elapsed = (now - obs.current_tool_started_at).total_seconds()
        if elapsed > _TOOL_TIMEOUT_SECONDS:
            return True, "tool_timeout"

    recent = list(obs.recent_activity)
    if len(recent) >= _LOOP_THRESHOLD:
        names = [e.tool_name for e in recent[:_LOOP_THRESHOLD]]
        if len(set(names)) == 1:
            return True, "tool_loop"

    if obs.last_output_at and not obs.current_tool:
        silence = (now - obs.last_output_at).total_seconds()
        if silence > _OUTPUT_SILENCE_SECONDS:
            return True, "output_silence"

    return False, None


class AgentObservabilityStore:
    def __init__(self) -> None:
        self._sessions: dict[str, _SessionObs] = {}

    def init_session(self, session_id: str) -> None:
        self._sessions[session_id] = _SessionObs(session_id=session_id)

    def remove_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def update_tool_start(self, session_id: str, tool_name: str, timestamp: datetime) -> None:
        obs = self._sessions.get(session_id)
        if obs is None:
            return
        obs.current_tool = tool_name
        obs.current_tool_started_at = timestamp

    def update_tool_end(
        self, session_id: str, tool_name: str, ended_at: datetime, duration_ms: int
    ) -> None:
        obs = self._sessions.get(session_id)
        if obs is None:
            return
        started_at = obs.current_tool_started_at or ended_at - timedelta(milliseconds=duration_ms)
        event = ToolEvent(
            tool_name=tool_name,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            session_id=session_id,
        )
        obs.recent_activity.appendleft(event)
        if obs.current_tool == tool_name:
            obs.current_tool = None
            obs.current_tool_started_at = None

    def update_output(self, session_id: str, at: datetime) -> None:
        obs = self._sessions.get(session_id)
        if obs is None:
            return
        obs.last_output_at = at

    def add_backfill_event(self, session_id: str, event: ToolEvent) -> None:
        """Add a completed ToolEvent from JSONL backfill if not already present."""
        obs = self._sessions.get(session_id)
        if obs is None:
            return
        existing_starts = {e.started_at for e in obs.recent_activity}
        if event.started_at not in existing_starts:
            obs.recent_activity.append(event)

    def get_status(self, session_id: str) -> AgentStatus | None:
        obs = self._sessions.get(session_id)
        if obs is None:
            return None
        is_stuck, stuck_reason = _compute_stuck(obs)
        return AgentStatus(
            session_id=session_id,
            current_tool=obs.current_tool,
            current_tool_started_at=obs.current_tool_started_at,
            last_output_at=obs.last_output_at,
            recent_activity=list(obs.recent_activity),
            is_stuck=is_stuck,
            stuck_reason=stuck_reason,
        )

    def all_statuses(self) -> list[AgentStatus]:
        return [s for sid in self._sessions if (s := self.get_status(sid)) is not None]
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/observability/test_store.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/observability/store.py tests/observability/test_store.py
git commit -m "feat(observability): add AgentObservabilityStore with stuck detection"
```

---

## Task 3: ObservabilityProvider — protocol, NullProvider, ClaudeCodeProvider

**Files:**
- Create: `harness_claw/observability/provider.py`
- Create: `tests/observability/test_provider.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/observability/test_provider.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_claw.observability.provider import ClaudeCodeProvider, NullProvider


def test_null_provider_configure_does_nothing(tmp_path):
    p = NullProvider()
    env: dict = {}
    p.configure_session("s1", str(tmp_path), env)
    assert env == {}


def test_claude_provider_creates_settings_file(tmp_path):
    provider = ClaudeCodeProvider(base_url="http://localhost:8000")
    env: dict = {}
    provider.configure_session("s1", str(tmp_path), env)

    settings_path = tmp_path / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "hooks" in data
    assert "PreToolUse" in data["hooks"]
    assert "PostToolUse" in data["hooks"]


def test_claude_provider_merges_existing_settings(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    settings_path.write_text(json.dumps({"someOtherSetting": True}))

    provider = ClaudeCodeProvider(base_url="http://localhost:8000")
    provider.configure_session("s1", str(tmp_path), {})

    data = json.loads(settings_path.read_text())
    assert data["someOtherSetting"] is True
    assert "hooks" in data


def test_claude_provider_injects_env_vars(tmp_path):
    provider = ClaudeCodeProvider(base_url="http://localhost:8000")
    env: dict = {}
    provider.configure_session("s1", str(tmp_path), env)
    assert env["HARNESS_SESSION_ID"] == "s1"
    assert env["HARNESS_BASE_URL"] == "http://localhost:8000"


def test_claude_provider_unconfigure_removes_hooks(tmp_path):
    provider = ClaudeCodeProvider(base_url="http://localhost:8000")
    provider.configure_session("s1", str(tmp_path), {})
    provider.unconfigure_session("s1", str(tmp_path))

    settings_path = tmp_path / ".claude" / "settings.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text())
        hooks = data.get("hooks", {})
        # harnessclaw hooks removed
        for event_hooks in hooks.values():
            for hook_group in event_hooks:
                assert hook_group.get("_harnessclaw") is not True


def test_claude_provider_unconfigure_removes_file_if_empty(tmp_path):
    provider = ClaudeCodeProvider(base_url="http://localhost:8000")
    provider.configure_session("s1", str(tmp_path), {})
    provider.unconfigure_session("s1", str(tmp_path))
    # File should be gone (only had harnessclaw hooks)
    settings_path = tmp_path / ".claude" / "settings.json"
    # Either removed or has no harnessclaw content
    if settings_path.exists():
        data = json.loads(settings_path.read_text())
        assert data.get("hooks", {}).get("PreToolUse", []) == [] or \
               all(not h.get("_harnessclaw") for h in data.get("hooks", {}).get("PreToolUse", []))
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
pytest tests/observability/test_provider.py -v
```

Expected: `ImportError`.

- [ ] **Step 3: Write `harness_claw/observability/provider.py`**

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, runtime_checkable


_PRE_HOOK_CMD = (
    "jq --arg sid \"$HARNESS_SESSION_ID\" "
    "'{harness_session_id: $sid, event: \"tool_start\", tool_name: .tool_name}' "
    "| curl -s -X POST \"$HARNESS_BASE_URL/agent/tool-event\" "
    "-H 'Content-Type: application/json' -d @-"
)

_POST_HOOK_CMD = (
    "jq --arg sid \"$HARNESS_SESSION_ID\" "
    "'{harness_session_id: $sid, event: \"tool_end\", tool_name: .tool_name}' "
    "| curl -s -X POST \"$HARNESS_BASE_URL/agent/tool-event\" "
    "-H 'Content-Type: application/json' -d @-"
)

_HOOK_MARKER = "_harnessclaw"


@runtime_checkable
class ObservabilityProvider(Protocol):
    def configure_session(
        self, session_id: str, working_dir: str, extra_env: dict[str, str]
    ) -> None: ...

    def unconfigure_session(self, session_id: str, working_dir: str) -> None: ...


class NullProvider:
    def configure_session(
        self, session_id: str, working_dir: str, extra_env: dict[str, str]
    ) -> None:
        pass

    def unconfigure_session(self, session_id: str, working_dir: str) -> None:
        pass


class ClaudeCodeProvider:
    def __init__(self, base_url: str = "http://localhost:8000") -> None:
        self._base_url = base_url

    def _settings_path(self, working_dir: str) -> Path:
        return Path(working_dir).expanduser() / ".claude" / "settings.json"

    def _read_settings(self, path: Path) -> dict:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return {}

    def _write_settings(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    def configure_session(
        self, session_id: str, working_dir: str, extra_env: dict[str, str]
    ) -> None:
        extra_env["HARNESS_SESSION_ID"] = session_id
        extra_env["HARNESS_BASE_URL"] = self._base_url

        path = self._settings_path(working_dir)
        data = self._read_settings(path)

        hooks = data.setdefault("hooks", {})

        pre_hooks = hooks.setdefault("PreToolUse", [])
        if not any(h.get(_HOOK_MARKER) for h in pre_hooks):
            pre_hooks.append({
                _HOOK_MARKER: True,
                "matcher": ".*",
                "hooks": [{"type": "command", "command": _PRE_HOOK_CMD}],
            })

        post_hooks = hooks.setdefault("PostToolUse", [])
        if not any(h.get(_HOOK_MARKER) for h in post_hooks):
            post_hooks.append({
                _HOOK_MARKER: True,
                "matcher": ".*",
                "hooks": [{"type": "command", "command": _POST_HOOK_CMD}],
            })

        self._write_settings(path, data)

    def unconfigure_session(self, session_id: str, working_dir: str) -> None:
        path = self._settings_path(working_dir)
        if not path.exists():
            return

        data = self._read_settings(path)
        hooks = data.get("hooks", {})

        for event in ("PreToolUse", "PostToolUse"):
            hooks[event] = [h for h in hooks.get(event, []) if not h.get(_HOOK_MARKER)]
            if not hooks[event]:
                hooks.pop(event, None)

        if not hooks:
            data.pop("hooks", None)

        if not data:
            path.unlink(missing_ok=True)
        else:
            self._write_settings(path, data)
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
pytest tests/observability/test_provider.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/observability/provider.py tests/observability/test_provider.py
git commit -m "feat(observability): add ObservabilityProvider protocol and ClaudeCodeProvider"
```

---

## Task 4: CostPoller JSONL backfill

**Files:**
- Modify: `harness_claw/runtime/cost_poller.py`
- Modify: `tests/runtime/test_cost_poller.py`

The Claude Code JSONL format for tool use looks like:
```json
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","id":"toolu_abc","name":"Bash","input":{}}]}}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_abc","content":[{"type":"text","text":"output"}]}]}}
```

- [ ] **Step 1: Add new tests to `tests/runtime/test_cost_poller.py`**

Append these tests to the existing file:

```python
import json
from datetime import datetime
from harness_claw.observability.models import ToolEvent
from harness_claw.observability.store import AgentObservabilityStore


async def test_poll_extracts_tool_events(tmp_path):
    project_dir = tmp_path / "projects" / "tmp-myproject"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / "session-abc.jsonl"
    jsonl.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}],
            },
        }) + "\n"
        + json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": []}],
            },
        }) + "\n"
    )

    obs_store = AgentObservabilityStore()
    obs_store.init_session("sess-1")
    updates = []

    async def on_update(session_id, cost, input_tokens, output_tokens):
        updates.append(cost)

    from harness_claw.runtime.cost_poller import CostPoller
    poller = CostPoller("sess-1", "/tmp/myproject", on_update, claude_home=tmp_path, obs_store=obs_store)
    await poller._poll()

    status = obs_store.get_status("sess-1")
    assert len(status.recent_activity) == 1
    assert status.recent_activity[0].tool_name == "Bash"


async def test_poll_does_not_duplicate_tool_events(tmp_path):
    project_dir = tmp_path / "projects" / "tmp-myproject"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / "session-abc.jsonl"
    entry = (
        json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_2", "name": "Read", "input": {}}]},
        }) + "\n"
        + json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_2", "content": []}]},
        }) + "\n"
    )
    jsonl.write_text(entry)

    obs_store = AgentObservabilityStore()
    obs_store.init_session("sess-1")

    async def noop(sid, cost, inp, out): pass

    from harness_claw.runtime.cost_poller import CostPoller
    poller = CostPoller("sess-1", "/tmp/myproject", noop, claude_home=tmp_path, obs_store=obs_store)
    await poller._poll()
    await poller._poll()  # second poll — same data

    status = obs_store.get_status("sess-1")
    assert len(status.recent_activity) == 1
```

- [ ] **Step 2: Run new tests — verify they fail**

```bash
pytest tests/runtime/test_cost_poller.py::test_poll_extracts_tool_events tests/runtime/test_cost_poller.py::test_poll_does_not_duplicate_tool_events -v
```

Expected: FAIL — `CostPoller.__init__` doesn't accept `obs_store`.

- [ ] **Step 3: Modify `harness_claw/runtime/cost_poller.py`**

Add `obs_store` parameter and JSONL tool-event parsing to `_poll`:

```python
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable, Awaitable
from datetime import datetime
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from harness_claw.observability.store import AgentObservabilityStore

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
        obs_store: "AgentObservabilityStore | None" = None,
    ) -> None:
        self.session_id = session_id
        self._working_dir = working_dir
        self._on_cost_update = on_cost_update
        self._poll_interval = poll_interval
        self._claude_home = claude_home or Path.home() / ".claude"
        self._last_cost: float = -1.0
        self._seen_tool_ids: set[str] = set()
        self._obs_store = obs_store
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

        # First pass: collect tool_use start times by id
        tool_use_names: dict[str, str] = {}

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

            # Collect tool_use ids from assistant messages
            if event.get("type") == "assistant":
                msg = event.get("message", {})
                for block in msg.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tid = block.get("id", "")
                        name = block.get("name", "unknown")
                        if tid:
                            tool_use_names[tid] = name

            # Match tool_result to tool_use → backfill completed event
            if event.get("type") == "user" and self._obs_store is not None:
                msg = event.get("message", {})
                for block in msg.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tid = block.get("tool_use_id", "")
                        if tid and tid not in self._seen_tool_ids and tid in tool_use_names:
                            self._seen_tool_ids.add(tid)
                            from harness_claw.observability.models import ToolEvent
                            ev = ToolEvent(
                                tool_name=tool_use_names[tid],
                                started_at=datetime.utcnow(),
                                ended_at=datetime.utcnow(),
                                duration_ms=None,
                                session_id=self.session_id,
                            )
                            self._obs_store.add_backfill_event(self.session_id, ev)

        if abs(total_cost - self._last_cost) > 1e-9:
            self._last_cost = total_cost
            await self._on_cost_update(
                self.session_id, total_cost, total_input, total_output
            )
```

- [ ] **Step 4: Run all cost poller tests**

```bash
pytest tests/runtime/test_cost_poller.py -v
```

Expected: all tests PASS (including the original 4).

- [ ] **Step 5: Commit**

```bash
git add harness_claw/runtime/cost_poller.py tests/runtime/test_cost_poller.py
git commit -m "feat(observability): extend CostPoller with JSONL tool-event backfill"
```

---

## Task 5: JobRunner integration

**Files:**
- Modify: `harness_claw/runtime/job_runner.py`

- [ ] **Step 1: Read current `job_runner.py` lines 1–50 to confirm imports and `__init__` signature**

```bash
# Already read in planning — key facts:
# - __init__ accepts: registry, store, token_store, connector, dispatcher, broker, mcp_base_url
# - start_session: builds extra_env, calls pty.start, registers connector + dispatcher
# - on_output callback: calls self._broadcast(...)
# - kill_session: kills pty, revokes token, deregisters
```

- [ ] **Step 2: Add `obs_store` and `obs_provider` to `__init__`**

In `harness_claw/runtime/job_runner.py`, add the imports and new parameters:

```python
# At top of file, add after existing imports:
from datetime import datetime
from harness_claw.observability.store import AgentObservabilityStore
from harness_claw.observability.provider import ObservabilityProvider, NullProvider
```

Replace the `__init__` signature and body (add two new params with defaults):

```python
def __init__(
    self,
    registry: RoleRegistry,
    store: SessionStore,
    token_store: Any | None = None,
    connector: Any | None = None,
    dispatcher: Any | None = None,
    broker: Any | None = None,
    mcp_base_url: str = "http://localhost:8000",
    obs_store: AgentObservabilityStore | None = None,
    obs_provider: ObservabilityProvider | None = None,
) -> None:
    self._registry = registry
    self._store = store
    self._token_store = token_store
    self._connector = connector
    self._dispatcher = dispatcher
    self._broker = broker
    self._mcp_base_url = mcp_base_url
    self._obs_store = obs_store or AgentObservabilityStore()
    self._obs_provider = obs_provider or NullProvider()
    self._pty_sessions: dict[str, PtySession] = {}
    self._cost_pollers: dict[str, CostPoller] = {}
    self._session_tokens: dict[str, str] = {}
    self._senders: set[Send] = set()
    self._pty_callback_handler_factory: Any | None = None
```

- [ ] **Step 3: Add provider calls and `update_output` in `start_session`**

In `start_session`, after the `extra_env` dict is built and before `pty.start`, add:

```python
# Configure observability (injects HARNESS_SESSION_ID + HARNESS_BASE_URL if Claude provider)
if not is_terminal:
    self._obs_store.init_session(session_id)
    self._obs_provider.configure_session(session_id, session.working_dir, extra_env)
```

In the `on_output` callback, add an `update_output` call:

```python
async def on_output(data: bytes) -> None:
    if not is_terminal:
        self._obs_store.update_output(session_id, datetime.utcnow())
    await self._broadcast({
        "type": "output",
        "session_id": session_id,
        "data": base64.b64encode(data).decode(),
    })
```

In the `CostPoller` instantiation, pass `obs_store`:

```python
poller = CostPoller(
    session_id, session.working_dir, on_cost_update,
    obs_store=self._obs_store,
)
```

- [ ] **Step 4: Add provider cleanup in `kill_session`**

At the end of `kill_session`, after the existing cleanup, add:

```python
# Observability cleanup
session_obj = self._store.get(session_id)
if session_obj:
    self._obs_provider.unconfigure_session(session_id, session_obj.working_dir)
self._obs_store.remove_session(session_id)
```

- [ ] **Step 5: Run existing job runner tests**

```bash
pytest tests/runtime/test_job_runner.py -v
```

Expected: all existing tests PASS.

- [ ] **Step 6: Commit**

```bash
git add harness_claw/runtime/job_runner.py
git commit -m "feat(observability): integrate provider and store into JobRunner"
```

---

## Task 6: Server endpoints

**Files:**
- Modify: `harness_claw/server.py`

- [ ] **Step 1: Add imports and instantiate observability objects**

At the top of `harness_claw/server.py`, add after existing imports:

```python
from harness_claw.observability.store import AgentObservabilityStore
from harness_claw.observability.provider import ClaudeCodeProvider, NullProvider
```

After the `runner = JobRunner(...)` block, add:

```python
obs_store = AgentObservabilityStore()
obs_provider = ClaudeCodeProvider(base_url="http://localhost:8000")
```

Update the `runner = JobRunner(...)` call to pass them:

```python
runner = JobRunner(
    registry=registry,
    store=store,
    token_store=token_store,
    connector=connector,
    dispatcher=dispatcher,
    broker=broker,
    mcp_base_url="http://localhost:8000",
    obs_store=obs_store,
    obs_provider=obs_provider,
)
```

- [ ] **Step 2: Add `POST /agent/tool-event` endpoint**

Add after the `mcp_tool_call` endpoint:

```python
class ToolEventRequest(BaseModel):
    harness_session_id: str
    event: str           # "tool_start" | "tool_end"
    tool_name: str


@app.post("/agent/tool-event", status_code=204)
async def agent_tool_event(req: ToolEventRequest) -> None:
    now = datetime.utcnow()
    if req.event == "tool_start":
        obs_store.update_tool_start(req.harness_session_id, req.tool_name, now)
    elif req.event == "tool_end":
        # Duration not available from hook; use 0 as placeholder (JSONL backfill has actuals)
        obs_store.update_tool_end(req.harness_session_id, req.tool_name, now, 0)

    status = obs_store.get_status(req.harness_session_id)
    if status is not None:
        await runner._broadcast({"type": "agent_status", **status.to_dict()})
```

Add this import at the top of server.py:

```python
from datetime import datetime
```

- [ ] **Step 3: Add `GET /agent/{session_id}/status` endpoint**

```python
@app.get("/agent/{session_id}/status")
def get_agent_status(session_id: str) -> dict[str, Any]:
    status = obs_store.get_status(session_id)
    if status is None:
        raise HTTPException(status_code=404, detail="session not found")
    return status.to_dict()
```

- [ ] **Step 4: Verify server starts without errors**

```bash
python -c "from harness_claw.server import app; print('OK')"
```

Expected: prints `OK`.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/server.py
git commit -m "feat(observability): add /agent/tool-event and /agent/{id}/status endpoints"
```

---

## Task 7: UI — types and WebSocket handling

**Files:**
- Modify: `ui/src/types.ts`
- Modify: `ui/src/App.tsx`

- [ ] **Step 1: Add `ToolEvent`, `AgentStatus`, and WS message type to `ui/src/types.ts`**

Add after the `TaskRecord` interface:

```typescript
export interface ToolEvent {
  tool_name: string
  started_at: string        // ISO8601
  ended_at: string | null
  duration_ms: number | null
  session_id: string
}

export interface AgentStatus {
  session_id: string
  current_tool: string | null
  current_tool_started_at: string | null   // ISO8601
  last_output_at: string | null
  recent_activity: ToolEvent[]
  is_stuck: boolean
  stuck_reason: 'tool_timeout' | 'tool_loop' | 'output_silence' | null
}
```

Add `agent_status` to the `WSIncoming` union:

```typescript
export type WSIncoming =
  | { type: 'output'; session_id: string; data: string }
  | { type: 'cost_update'; session_id: string; cost_usd: number; input_tokens: number; output_tokens: number }
  | { type: 'session_update'; session_id: string; name: string; status: 'idle' | 'running' | 'killed' }
  | { type: 'session_deleted'; session_id: string }
  | { type: 'task.created'; task: TaskRecord }
  | { type: 'task.updated'; task: TaskRecord }
  | { type: 'task.completed'; task: TaskRecord }
  | { type: 'task.failed'; task: TaskRecord }
  | { type: 'agent_status' } & AgentStatus   // spread so session_id + all fields are top-level
  | { type: 'workflow.started'; run_id: string; workflow_id: string; step_id: string; input: string; initiated_by: string }
  | { type: 'workflow.step'; run_id: string; step_id: string; status: 'completed' | 'failed'; result: unknown }
  | { type: 'workflow.completed'; run_id: string }
  | { type: 'workflow.failed'; run_id: string; reason: string }
```

- [ ] **Step 2: Add `agentStatuses` state and WS handler in `ui/src/App.tsx`**

Add import at top:

```typescript
import type { RoleConfig, SessionState, WSIncoming, TaskRecord, ToolInfo, WorkflowRun, AgentStatus } from './types'
```

Add state after the existing `useState` calls:

```typescript
const [agentStatuses, setAgentStatuses] = useState<Record<string, AgentStatus>>({})
```

In `handleWsMessage`, add a branch before the closing brace:

```typescript
} else if (msg.type === 'agent_status') {
  const { type: _, ...status } = msg as WSIncoming & { type: 'agent_status' }
  setAgentStatuses(prev => ({ ...prev, [msg.session_id]: status as AgentStatus }))
}
```

Pass `agentStatuses` to `SessionSidebar` and `AgentTab` in the JSX:

```tsx
<SessionSidebar
  sessions={sessions}
  agentStatuses={agentStatuses}
  activeSessionId={activeSessionId}
  onSelect={(id) => { setActiveSessionId(id); setShowCreate(false) }}
  onNewSession={() => setShowCreate(true)}
  onDelete={handleDelete}
  onKill={handleKill}
/>
```

```tsx
{tab === 'agent' && (
  <AgentTab
    session={activeSession}
    role={activeRole}
    agentStatus={agentStatuses[activeSession.session_id] ?? null}
  />
)}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd ui && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add ui/src/types.ts ui/src/App.tsx
git commit -m "feat(observability): add AgentStatus types and WS handler in App"
```

---

## Task 8: UI — live tool badge in SessionSidebar

**Files:**
- Modify: `ui/src/components/SessionSidebar.tsx`

- [ ] **Step 1: Update `SessionSidebar` props and add tool badge**

Replace the entire file content:

```tsx
import { useEffect, useRef, useState } from 'react'
import type { SessionState, AgentStatus } from '../types'

interface Props {
  sessions: Record<string, SessionState>
  agentStatuses: Record<string, AgentStatus>
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

function useElapsedSeconds(startedAt: string | null): number {
  const [elapsed, setElapsed] = useState(0)
  const startedAtRef = useRef(startedAt)
  startedAtRef.current = startedAt

  useEffect(() => {
    if (!startedAt) { setElapsed(0); return }
    const tick = () => {
      const diff = Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000)
      setElapsed(Math.max(0, diff))
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [startedAt])

  return elapsed
}

function ToolBadge({ status }: { status: AgentStatus }) {
  const elapsed = useElapsedSeconds(status.current_tool_started_at)
  if (!status.current_tool) return null
  return (
    <span
      className={`text-xs px-1.5 py-0.5 rounded font-mono ml-1 ${
        status.is_stuck ? 'bg-amber-900 text-amber-300' : 'bg-gray-700 text-gray-300'
      }`}
      title={status.stuck_reason ?? undefined}
    >
      {status.current_tool} {elapsed}s
      {status.is_stuck && ' ⚠'}
    </span>
  )
}

export function SessionSidebar({
  sessions, agentStatuses, activeSessionId, onSelect, onNewSession, onDelete, onKill,
}: Props) {
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
            {dirSessions.map((s) => {
              const agentStatus = agentStatuses[s.session_id] ?? null
              return (
                <div
                  key={s.session_id}
                  className={`group flex items-center px-3 py-2 cursor-pointer text-sm hover:bg-gray-800 ${
                    s.session_id === activeSessionId ? 'bg-gray-800 text-white' : 'text-gray-400'
                  }`}
                  onClick={() => onSelect(s.session_id)}
                >
                  <span className={`mr-1.5 text-xs ${statusColor(s.status)}`}>{statusDot(s.status)}</span>
                  <span className="flex-1 truncate min-w-0">{s.name || 'New session'}</span>
                  {agentStatus && <ToolBadge status={agentStatus} />}
                  {s.status === 'running' && (
                    <button
                      className="hidden group-hover:block text-gray-500 hover:text-red-400 ml-1 text-xs flex-shrink-0"
                      onClick={(e) => { e.stopPropagation(); onKill(s.session_id) }}
                      title="Kill"
                    >■</button>
                  )}
                  {s.status !== 'running' && (
                    <button
                      className="hidden group-hover:block text-gray-500 hover:text-red-400 ml-1 text-xs flex-shrink-0"
                      onClick={(e) => { e.stopPropagation(); onDelete(s.session_id) }}
                      title="Delete"
                    >✕</button>
                  )}
                </div>
              )
            })}
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

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd ui && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add ui/src/components/SessionSidebar.tsx
git commit -m "feat(observability): add live tool badge with elapsed time to SessionSidebar"
```

---

## Task 9: UI — activity timeline in AgentTab

**Files:**
- Modify: `ui/src/components/AgentTab.tsx`

- [ ] **Step 1: Update `AgentTab` to accept and display `agentStatus`**

Replace the entire file content:

```tsx
import type { RoleConfig, SessionState, AgentStatus, ToolEvent } from '../types'

interface Props {
  session: SessionState
  role: RoleConfig | undefined
  agentStatus: AgentStatus | null
}

function formatDuration(ms: number | null): string {
  if (ms === null) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function ActivityTimeline({ events }: { events: ToolEvent[] }) {
  if (events.length === 0) {
    return <span className="text-gray-600 text-xs">No activity yet</span>
  }
  return (
    <div className="flex flex-col gap-0.5">
      {events.map((ev, i) => (
        <div key={i} className="flex items-center gap-2 text-xs font-mono">
          <span className="text-gray-300 w-24 truncate">{ev.tool_name}</span>
          <span className="text-gray-500 w-12 text-right">{formatDuration(ev.duration_ms)}</span>
          <span className="text-gray-600">{formatTime(ev.started_at)}</span>
        </div>
      ))}
    </div>
  )
}

export function AgentTab({ session, role, agentStatus }: Props) {
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
      {role?.system_prompt && (
        <div className="flex flex-col gap-1">
          <span className="text-gray-500 text-xs uppercase tracking-wide">System Prompt</span>
          <pre className="text-gray-300 text-xs bg-gray-800 rounded p-3 whitespace-pre-wrap border border-gray-700">
            {role.system_prompt}
          </pre>
        </div>
      )}
      <div className="flex flex-col gap-2">
        <span className="text-gray-500 text-xs uppercase tracking-wide">
          Activity
          {agentStatus?.is_stuck && (
            <span className="ml-2 text-amber-400 normal-case">
              ⚠ {agentStatus.stuck_reason?.replace('_', ' ')}
            </span>
          )}
        </span>
        {agentStatus ? (
          <ActivityTimeline events={agentStatus.recent_activity} />
        ) : (
          <span className="text-gray-600 text-xs">Observability not available</span>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd ui && npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Build the UI**

```bash
cd ui && npm run build
```

Expected: build succeeds with no TypeScript errors.

- [ ] **Step 4: Run all Python tests**

```bash
cd .. && pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/AgentTab.tsx
git commit -m "feat(observability): add activity timeline and stuck indicator to AgentTab"
```
