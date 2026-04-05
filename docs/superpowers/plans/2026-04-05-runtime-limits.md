# Runtime Limits & Pause Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cost (USD), time (seconds), and tool-permission limits to agent sessions; violations pause the task (resumable) rather than failing it.

**Architecture:** `RoleConfig` and `Task` gain limit fields. A new `LimitEnforcer` class checks thresholds on every `CostPoller` tick. On violation, `JobRunner.pause_session` kills the PTY, marks the task `paused` with a reason, stores the `provider_session_id` (JSONL filename) for resume-with-history, and re-enqueues. `ClaudeCodeProvider.configure_session` writes `permissions` to the session's `settings.json` when `allowed_tools` is set. A new `POST /api/tasks/{id}/resume` endpoint re-queues a paused task. The dashboard shows a yellow paused badge with reason and a Resume button.

**Tech Stack:** Python 3.12, asyncio, SQLite, React/TypeScript, Tailwind CSS

---

## File Map

| File | Change |
|------|--------|
| `harness_claw/role_registry.py` | Add `max_cost`, `max_time`, `allowed_tools` to `RoleConfig`; parse from yaml |
| `harness_claw/gateway/task_store.py` | Add `max_cost`, `max_time`, `allowed_tools`, `provider_session_id`, `pause_reason` to `Task`; SQLite migration; `"paused"` status |
| `harness_claw/runtime/limit_enforcer.py` | New: `LimitEnforcer` class |
| `harness_claw/runtime/cost_poller.py` | Expose `provider_session_id` property (JSONL stem on first poll) |
| `harness_claw/runtime/job_runner.py` | Create `LimitEnforcer` per session; add `pause_session`; wire into cost callback |
| `harness_claw/observability/provider.py` | Write/remove `permissions` block when `allowed_tools` is set |
| `harness_claw/gateway/broker.py` | Add limit params to `Broker.delegate` |
| `harness_claw/gateway/mcp_server.py` | Add limit params to `agent_delegate` |
| `harness_claw/server.py` | Update MCP tool schema; add `POST /api/tasks/{id}/resume` |
| `ui/src/types.ts` | Add `"paused"` to status union; add `pause_reason`, `provider_session_id` |
| `ui/src/components/TasksTab.tsx` | Yellow paused badge, reason text, Resume button |
| `tests/runtime/test_limit_enforcer.py` | New: LimitEnforcer unit tests |
| `tests/gateway/test_task_store.py` | New field roundtrip tests |
| `tests/observability/test_provider.py` | Tool policy tests |

---

## Task 1: RoleConfig limit fields

**Files:**
- Modify: `harness_claw/role_registry.py`
- Test: `tests/test_role_registry.py` (create if missing)

- [ ] **Step 1: Write failing tests**

Create `tests/test_role_registry.py`:

```python
from pathlib import Path
import pytest
import yaml
from harness_claw.role_registry import RoleRegistry


def _write_yaml(tmp_path: Path, roles: list[dict]) -> Path:
    p = tmp_path / "agents.yaml"
    p.write_text(yaml.dump({"roles": roles}))
    return p


def test_role_defaults_no_limits(tmp_path):
    p = _write_yaml(tmp_path, [{"id": "r1", "name": "R1", "model": "claude-sonnet-4-5-20251001",
                                 "system_prompt": "hi"}])
    reg = RoleRegistry(p)
    role = reg.get("r1")
    assert role.max_cost is None
    assert role.max_time is None
    assert role.allowed_tools is None


def test_role_parses_limits(tmp_path):
    p = _write_yaml(tmp_path, [{"id": "r1", "name": "R1", "model": "claude-sonnet-4-5-20251001",
                                 "system_prompt": "hi", "max_cost": 0.5,
                                 "max_time": 3600, "allowed_tools": ["Read", "Bash"]}])
    reg = RoleRegistry(p)
    role = reg.get("r1")
    assert role.max_cost == 0.5
    assert role.max_time == 3600
    assert role.allowed_tools == ["Read", "Bash"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/juichanglu/src/HarnessClaw
.venv/bin/pytest tests/test_role_registry.py -v
```

Expected: FAIL — `RoleConfig` has no attribute `max_cost`

- [ ] **Step 3: Add limit fields to RoleConfig and parse from yaml**

In `harness_claw/role_registry.py`, update `RoleConfig`:

```python
@dataclass
class RoleConfig:
    id: str
    name: str
    provider: str
    model: str
    system_prompt: str
    max_tokens: int = 8192
    scopes: list[str] = field(default_factory=list)
    caps: list[str] = field(default_factory=list)
    max_cost: float | None = None
    max_time: int | None = None
    allowed_tools: list[str] | None = None
```

In `RoleRegistry.__init__`, update the role construction inside the `for item in data.get("roles", []):` loop:

```python
role = RoleConfig(
    id=item["id"],
    name=item["name"],
    provider=item.get("provider", "claude-code"),
    model=item["model"],
    system_prompt=item["system_prompt"],
    max_tokens=item.get("max_tokens", 8192),
    scopes=scopes,
    caps=list(item.get("caps", [])),
    max_cost=item.get("max_cost"),
    max_time=item.get("max_time"),
    allowed_tools=list(item["allowed_tools"]) if item.get("allowed_tools") else None,
)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_role_registry.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add harness_claw/role_registry.py tests/test_role_registry.py
git commit -m "feat: add max_cost/max_time/allowed_tools limit fields to RoleConfig"
```

---

## Task 2: Task limit fields + paused status + SQLite migration

**Files:**
- Modify: `harness_claw/gateway/task_store.py`
- Test: `tests/gateway/test_task_store.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/gateway/test_task_store.py`:

```python
def test_task_limit_field_defaults():
    task = make_task()
    assert task.max_cost is None
    assert task.max_time is None
    assert task.allowed_tools is None
    assert task.provider_session_id is None
    assert task.pause_reason is None


def test_task_limit_fields_in_to_dict():
    task = make_task()
    task.max_cost = 0.5
    task.max_time = 3600
    task.allowed_tools = ["Read", "Bash"]
    task.provider_session_id = "abc123"
    task.pause_reason = "Cost $0.52 exceeded limit $0.50"
    d = task.to_dict()
    assert d["max_cost"] == 0.5
    assert d["max_time"] == 3600
    assert d["allowed_tools"] == ["Read", "Bash"]
    assert d["provider_session_id"] == "abc123"
    assert d["pause_reason"] == "Cost $0.52 exceeded limit $0.50"


def test_sqlite_roundtrip_limit_fields(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    task = make_task(task_id="t1")
    task.max_cost = 1.0
    task.max_time = 60
    task.allowed_tools = ["Read"]
    task.provider_session_id = "sess-abc"
    task.pause_reason = "Time 61s exceeded limit 60s"
    task.status = "paused"
    store.save(task)
    loaded = store.get("t1")
    assert loaded.max_cost == 1.0
    assert loaded.max_time == 60
    assert loaded.allowed_tools == ["Read"]
    assert loaded.provider_session_id == "sess-abc"
    assert loaded.pause_reason == "Time 61s exceeded limit 60s"
    assert loaded.status == "paused"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/gateway/test_task_store.py::test_task_limit_field_defaults tests/gateway/test_task_store.py::test_sqlite_roundtrip_limit_fields -v
```

Expected: FAIL

- [ ] **Step 3: Update Task dataclass**

In `harness_claw/gateway/task_store.py`, update `Task`:

```python
@dataclass
class Task:
    task_id: str
    delegated_by: str
    delegated_to: str
    instructions: str
    caps_requested: list[str]
    context: dict[str, Any] | None = None
    status: str = "queued"       # queued | running | completed | failed | paused
    progress_pct: int = 0
    progress_msg: str = ""
    result: dict[str, Any] | str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    callback: bool = False
    priority: int = 2
    resume: bool = False
    max_cost: float | None = None
    max_time: int | None = None
    allowed_tools: list[str] | None = None
    provider_session_id: str | None = None
    pause_reason: str | None = None
```

Update `to_dict()` to include the new fields:

```python
def to_dict(self) -> dict[str, Any]:
    return {
        "task_id": self.task_id,
        "delegated_by": self.delegated_by,
        "delegated_to": self.delegated_to,
        "instructions": self.instructions,
        "caps_requested": self.caps_requested,
        "context": self.context,
        "status": self.status,
        "progress_pct": self.progress_pct,
        "progress_msg": self.progress_msg,
        "result": self.result,
        "created_at": self.created_at,
        "updated_at": self.updated_at,
        "callback": self.callback,
        "priority": self.priority,
        "resume": self.resume,
        "max_cost": self.max_cost,
        "max_time": self.max_time,
        "allowed_tools": self.allowed_tools,
        "provider_session_id": self.provider_session_id,
        "pause_reason": self.pause_reason,
    }
```

- [ ] **Step 4: Update _CREATE_TABLE, _MIGRATE_SQL, _row_to_task, and save**

Replace `_CREATE_TABLE`:

```python
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id            TEXT PRIMARY KEY,
    delegated_by       TEXT NOT NULL,
    delegated_to       TEXT NOT NULL,
    instructions       TEXT NOT NULL,
    caps_requested     TEXT NOT NULL,
    context            TEXT,
    status             TEXT NOT NULL,
    progress_pct       INTEGER NOT NULL DEFAULT 0,
    progress_msg       TEXT NOT NULL DEFAULT '',
    result             TEXT,
    callback           INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    priority           INTEGER NOT NULL DEFAULT 2,
    resume             INTEGER NOT NULL DEFAULT 0,
    max_cost           REAL,
    max_time           INTEGER,
    allowed_tools      TEXT,
    provider_session_id TEXT,
    pause_reason       TEXT
)
"""
```

Add to `_MIGRATE_SQL`:

```python
_MIGRATE_SQL = [
    "ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 2",
    "ALTER TABLE tasks ADD COLUMN resume   INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE tasks ADD COLUMN max_cost           REAL",
    "ALTER TABLE tasks ADD COLUMN max_time           INTEGER",
    "ALTER TABLE tasks ADD COLUMN allowed_tools      TEXT",
    "ALTER TABLE tasks ADD COLUMN provider_session_id TEXT",
    "ALTER TABLE tasks ADD COLUMN pause_reason       TEXT",
]
```

Update `_row_to_task` to read the new fields. Add after `resume=bool(row["resume"]) if "resume" in keys else False,`:

```python
        max_cost=row["max_cost"] if "max_cost" in keys else None,
        max_time=row["max_time"] if "max_time" in keys else None,
        allowed_tools=json.loads(row["allowed_tools"]) if row["allowed_tools"] else None,
        provider_session_id=row["provider_session_id"] if "provider_session_id" in keys else None,
        pause_reason=row["pause_reason"] if "pause_reason" in keys else None,
```

Update `save` to include the new columns. Replace the existing `save` method:

```python
def save(self, task: Task) -> None:
    task.updated_at = datetime.now(timezone.utc).isoformat()
    result_json = json.dumps(task.result) if task.result is not None else None
    with self._connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks
                (task_id, delegated_by, delegated_to, instructions, caps_requested,
                 context, status, progress_pct, progress_msg, result, callback,
                 created_at, updated_at, priority, resume,
                 max_cost, max_time, allowed_tools, provider_session_id, pause_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                status              = excluded.status,
                progress_pct        = excluded.progress_pct,
                progress_msg        = excluded.progress_msg,
                result              = excluded.result,
                callback            = excluded.callback,
                updated_at          = excluded.updated_at,
                priority            = excluded.priority,
                resume              = excluded.resume,
                max_cost            = excluded.max_cost,
                max_time            = excluded.max_time,
                allowed_tools       = excluded.allowed_tools,
                provider_session_id = excluded.provider_session_id,
                pause_reason        = excluded.pause_reason
            """,
            (
                task.task_id, task.delegated_by, task.delegated_to, task.instructions,
                json.dumps(task.caps_requested),
                json.dumps(task.context) if task.context is not None else None,
                task.status, task.progress_pct, task.progress_msg,
                result_json, int(task.callback),
                task.created_at, task.updated_at,
                task.priority, int(task.resume),
                task.max_cost,
                task.max_time,
                json.dumps(task.allowed_tools) if task.allowed_tools is not None else None,
                task.provider_session_id,
                task.pause_reason,
            ),
        )
```

- [ ] **Step 5: Run all task_store tests**

```bash
.venv/bin/pytest tests/gateway/test_task_store.py -v
```

Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add harness_claw/gateway/task_store.py tests/gateway/test_task_store.py
git commit -m "feat: add limit fields and paused status to Task dataclass and SqliteTaskStore"
```

---

## Task 3: LimitEnforcer

**Files:**
- Create: `harness_claw/runtime/limit_enforcer.py`
- Create: `tests/runtime/test_limit_enforcer.py`

- [ ] **Step 1: Write failing tests**

Create `tests/runtime/test_limit_enforcer.py`:

```python
import asyncio
import pytest
from harness_claw.runtime.limit_enforcer import LimitEnforcer


@pytest.mark.asyncio
async def test_cost_limit_fires_callback():
    fired = []

    async def on_exceeded(reason: str, detail: str) -> None:
        fired.append((reason, detail))

    enforcer = LimitEnforcer("s1", max_cost=1.0, max_time=None, on_exceeded=on_exceeded)
    enforcer.check_cost(0.5)
    await asyncio.sleep(0)
    assert fired == []

    enforcer.check_cost(1.01)
    await asyncio.sleep(0)
    assert len(fired) == 1
    assert fired[0][0] == "cost"
    assert "1.01" in fired[0][1]
    assert "1.0" in fired[0][1]


@pytest.mark.asyncio
async def test_cost_limit_fires_only_once():
    fired = []

    async def on_exceeded(reason: str, detail: str) -> None:
        fired.append(reason)

    enforcer = LimitEnforcer("s1", max_cost=1.0, max_time=None, on_exceeded=on_exceeded)
    enforcer.check_cost(1.01)
    enforcer.check_cost(1.50)
    enforcer.check_cost(2.00)
    await asyncio.sleep(0)
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_time_limit_fires_callback(monkeypatch):
    import time
    fired = []

    async def on_exceeded(reason: str, detail: str) -> None:
        fired.append((reason, detail))

    enforcer = LimitEnforcer("s1", max_cost=None, max_time=10, on_exceeded=on_exceeded)

    # Simulate 11 seconds elapsed
    monkeypatch.setattr(time, "monotonic", lambda: enforcer._started_at + 11)
    enforcer.check_time()
    await asyncio.sleep(0)
    assert len(fired) == 1
    assert fired[0][0] == "time"


@pytest.mark.asyncio
async def test_no_limit_never_fires():
    fired = []

    async def on_exceeded(reason: str, detail: str) -> None:
        fired.append(reason)

    enforcer = LimitEnforcer("s1", max_cost=None, max_time=None, on_exceeded=on_exceeded)
    enforcer.check_cost(9999.0)
    enforcer.check_time()
    await asyncio.sleep(0)
    assert fired == []


@pytest.mark.asyncio
async def test_time_limit_does_not_fire_before_exceeded(monkeypatch):
    import time
    fired = []

    async def on_exceeded(reason: str, detail: str) -> None:
        fired.append(reason)

    enforcer = LimitEnforcer("s1", max_cost=None, max_time=60, on_exceeded=on_exceeded)
    monkeypatch.setattr(time, "monotonic", lambda: enforcer._started_at + 30)
    enforcer.check_time()
    await asyncio.sleep(0)
    assert fired == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/runtime/test_limit_enforcer.py -v
```

Expected: FAIL — `No module named 'harness_claw.runtime.limit_enforcer'`

- [ ] **Step 3: Implement LimitEnforcer**

Create `harness_claw/runtime/limit_enforcer.py`:

```python
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Awaitable


class LimitEnforcer:
    """Checks cost and time limits for an agent session.

    Call check_cost() and check_time() on each CostPoller tick.
    on_exceeded is called at most once across all checks.
    """

    def __init__(
        self,
        session_id: str,
        max_cost: float | None,
        max_time: int | None,
        on_exceeded: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self._session_id = session_id
        self._max_cost = max_cost
        self._max_time = max_time
        self._on_exceeded = on_exceeded
        self._fired = False
        self._started_at = time.monotonic()

    def check_cost(self, current_cost: float) -> None:
        if self._fired or self._max_cost is None:
            return
        if current_cost >= self._max_cost:
            self._fire("cost", f"Cost ${current_cost:.4f} exceeded limit ${self._max_cost:.4f}")

    def check_time(self) -> None:
        if self._fired or self._max_time is None:
            return
        elapsed = int(time.monotonic() - self._started_at)
        if elapsed >= self._max_time:
            self._fire("time", f"Time {elapsed}s exceeded limit {self._max_time}s")

    def _fire(self, reason: str, detail: str) -> None:
        self._fired = True
        try:
            asyncio.create_task(self._on_exceeded(reason, detail))
        except RuntimeError:
            pass  # no event loop (tests or shutdown)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/runtime/test_limit_enforcer.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add harness_claw/runtime/limit_enforcer.py tests/runtime/test_limit_enforcer.py
git commit -m "feat: add LimitEnforcer for cost and time threshold detection"
```

---

## Task 4: CostPoller exposes provider_session_id

**Files:**
- Modify: `harness_claw/runtime/cost_poller.py`
- Test: `tests/runtime/test_cost_poller.py`

- [ ] **Step 1: Write failing test**

Add to `tests/runtime/test_cost_poller.py`:

```python
def test_provider_session_id_captured_from_jsonl_filename(tmp_path):
    """provider_session_id is the stem of the most-recent JSONL file."""
    project_dir = tmp_path / "projects" / "test-project"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / "my-claude-session-id.jsonl"
    jsonl.write_text("")

    called = []

    async def on_cost(sid, cost, inp, out):
        called.append((sid, cost))

    poller = CostPoller(
        session_id="s1",
        working_dir="test/project",
        on_cost_update=on_cost,
        claude_home=tmp_path,
    )
    assert poller.provider_session_id is None

    import asyncio
    asyncio.get_event_loop().run_until_complete(poller._poll())
    assert poller.provider_session_id == "my-claude-session-id"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/runtime/test_cost_poller.py::test_provider_session_id_captured_from_jsonl_filename -v
```

Expected: FAIL — `CostPoller has no attribute 'provider_session_id'`

- [ ] **Step 3: Add provider_session_id property to CostPoller**

In `harness_claw/runtime/cost_poller.py`, update `__init__`:

```python
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
    self.provider_session_id: str | None = None
```

In `_poll`, after `jsonl_files = sorted(...)` and the early return `if not jsonl_files: return`, add:

```python
        if self.provider_session_id is None:
            self.provider_session_id = jsonl_files[0].stem
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/runtime/test_cost_poller.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add harness_claw/runtime/cost_poller.py tests/runtime/test_cost_poller.py
git commit -m "feat: expose provider_session_id on CostPoller from JSONL filename"
```

---

## Task 5: JobRunner — pause_session and LimitEnforcer wiring

**Files:**
- Modify: `harness_claw/runtime/job_runner.py`
- Test: `tests/runtime/test_job_runner.py` (create if missing)

- [ ] **Step 1: Write failing tests**

Create `tests/runtime/test_job_runner.py`:

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from harness_claw.gateway.task_store import Task, TaskStore
from harness_claw.gateway.broker import Broker
from harness_claw.runtime.limit_enforcer import LimitEnforcer


def make_running_task(session_id: str) -> Task:
    return Task(
        task_id="t1",
        delegated_by="orch",
        delegated_to=session_id,
        instructions="do it",
        caps_requested=["python"],
        status="running",
    )


@pytest.mark.asyncio
async def test_pause_session_marks_task_paused():
    store = TaskStore()
    task = make_running_task("s1")
    store.save(task)

    broker = MagicMock()
    broker.list_tasks.return_value = [task]
    broker.scheduler = MagicMock()
    broker._store = store

    from harness_claw.runtime.job_runner import JobRunner
    runner = JobRunner.__new__(JobRunner)
    runner._broker = broker
    runner._pty_sessions = {}
    runner._cost_pollers = {}
    runner._session_tokens = {}
    runner._token_store = None
    runner._connector = None
    runner._dispatcher = None
    runner._obs_working_dirs = {}
    runner._obs_store = MagicMock()
    runner._obs_provider = MagicMock()
    runner._broadcast = AsyncMock()

    await runner.pause_session("s1", "cost", "Cost $0.51 exceeded limit $0.50")

    reloaded = store.get("t1")
    assert reloaded.status == "paused"
    assert reloaded.pause_reason == "Cost $0.51 exceeded limit $0.50"
    assert reloaded.resume is True
    broker.scheduler.push.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/runtime/test_job_runner.py -v
```

Expected: FAIL — `JobRunner has no method 'pause_session'`

- [ ] **Step 3: Add _limit_enforcers dict to JobRunner.__init__**

In `harness_claw/runtime/job_runner.py`, add import at top:

```python
from harness_claw.runtime.limit_enforcer import LimitEnforcer
```

In `JobRunner.__init__`, add:

```python
self._limit_enforcers: dict[str, LimitEnforcer] = {}
```

- [ ] **Step 4: Add pause_session method to JobRunner**

Add after `kill_session`:

```python
async def pause_session(self, session_id: str, reason: str, detail: str) -> None:
    """Kill a session and mark its running task as paused (resumable)."""
    # Capture provider_session_id before killing (kill_session stops the poller)
    poller = self._cost_pollers.get(session_id)
    provider_session_id = poller.provider_session_id if poller else None

    # Find the task running on this session
    task = next(
        (t for t in self._broker.list_tasks()
         if t.delegated_to == session_id and t.status == "running"),
        None,
    )

    self.kill_session(session_id)
    self._limit_enforcers.pop(session_id, None)

    if task is not None:
        task.status = "paused"
        task.pause_reason = detail
        task.resume = True
        if provider_session_id:
            task.provider_session_id = provider_session_id
        self._broker._store.save(task)
        self._broker.scheduler.push(task)
        try:
            asyncio.create_task(self._broker._notify("task.updated", task))
        except RuntimeError:
            pass
        await self._broadcast({
            "type": "task.updated",
            "task": task.to_dict(),
        })
```

- [ ] **Step 5: Create LimitEnforcer per session in start_session**

In `JobRunner.start_session`, after the `poller = CostPoller(...)` line, add:

```python
            enforcer = LimitEnforcer(
                session_id=session_id,
                max_cost=role.max_cost,
                max_time=role.max_time,
                on_exceeded=lambda r, d, sid=session_id: self.pause_session(sid, r, d),
            )
            self._limit_enforcers[session_id] = enforcer
```

Update `on_cost_update` to call the enforcer. Replace the existing `on_cost_update` inner function:

```python
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
                # Check limits
                lim = self._limit_enforcers.get(sid)
                if lim is not None:
                    lim.check_cost(cost)
                    lim.check_time()
```

Also clean up enforcer in `kill_session`. After `self._cost_pollers.pop(session_id, None)`, add:

```python
        self._limit_enforcers.pop(session_id, None)
```

- [ ] **Step 6: Run tests**

```bash
.venv/bin/pytest tests/runtime/test_job_runner.py tests/runtime/test_limit_enforcer.py -v
```

Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add harness_claw/runtime/job_runner.py tests/runtime/test_job_runner.py
git commit -m "feat: add pause_session to JobRunner and wire LimitEnforcer into cost polling"
```

---

## Task 6: Tool policy in ClaudeCodeProvider

**Files:**
- Modify: `harness_claw/observability/provider.py`
- Test: `tests/observability/test_provider.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/observability/test_provider.py`:

```python
def test_configure_session_writes_permissions_when_allowed_tools_set(tmp_path):
    provider = ClaudeCodeProvider(base_url="http://localhost:8000")
    extra_env: dict[str, str] = {}
    provider.configure_session("s1", str(tmp_path), extra_env, allowed_tools=["Read", "Bash"])

    settings_path = tmp_path / ".claude" / "settings.json"
    data = json.loads(settings_path.read_text())
    assert data["permissions"]["allow"] == ["Read", "Bash"]
    assert data["permissions"]["deny"] == ["*"]


def test_configure_session_no_permissions_when_allowed_tools_none(tmp_path):
    provider = ClaudeCodeProvider(base_url="http://localhost:8000")
    extra_env: dict[str, str] = {}
    provider.configure_session("s1", str(tmp_path), extra_env, allowed_tools=None)

    settings_path = tmp_path / ".claude" / "settings.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text())
        assert "permissions" not in data


def test_unconfigure_session_removes_permissions(tmp_path):
    provider = ClaudeCodeProvider(base_url="http://localhost:8000")
    extra_env: dict[str, str] = {}
    provider.configure_session("s1", str(tmp_path), extra_env, allowed_tools=["Read"])
    provider.unconfigure_session("s1", str(tmp_path))

    settings_path = tmp_path / ".claude" / "settings.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text())
        assert "permissions" not in data
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/observability/test_provider.py::test_configure_session_writes_permissions_when_allowed_tools_set -v
```

Expected: FAIL — `configure_session() got unexpected keyword argument 'allowed_tools'`

- [ ] **Step 3: Update configure_session and unconfigure_session**

In `harness_claw/observability/provider.py`, update `configure_session` signature and body:

```python
def configure_session(
    self, session_id: str, working_dir: str, extra_env: dict[str, str],
    allowed_tools: list[str] | None = None,
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

    if allowed_tools is not None:
        data["permissions"] = {
            "allow": list(allowed_tools),
            "deny": ["*"],
        }

    self._write_settings(path, data)
```

Update `unconfigure_session` to also remove `permissions`:

```python
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

    data.pop("permissions", None)

    if not data:
        path.unlink(missing_ok=True)
    else:
        self._write_settings(path, data)
```

Also update the call site in `job_runner.py` to pass `allowed_tools`. Find the `configure_session` call and update it:

```python
            self._obs_provider.configure_session(
                session_id, session.working_dir, extra_env,
                allowed_tools=role.allowed_tools,
            )
```

And update `NullProvider.configure_session` signature to match:

```python
class NullProvider:
    def configure_session(
        self, session_id: str, working_dir: str, extra_env: dict[str, str],
        allowed_tools: list[str] | None = None,
    ) -> None:
        pass
```

And update the `ObservabilityProvider` Protocol:

```python
@runtime_checkable
class ObservabilityProvider(Protocol):
    def configure_session(
        self, session_id: str, working_dir: str, extra_env: dict[str, str],
        allowed_tools: list[str] | None = None,
    ) -> None: ...

    def unconfigure_session(self, session_id: str, working_dir: str) -> None: ...
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/observability/test_provider.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add harness_claw/observability/provider.py harness_claw/runtime/job_runner.py tests/observability/test_provider.py
git commit -m "feat: write permissions to session settings.json when allowed_tools is configured"
```

---

## Task 7: Broker.delegate and MCP tool limit params

**Files:**
- Modify: `harness_claw/gateway/broker.py`
- Modify: `harness_claw/gateway/mcp_server.py`
- Modify: `harness_claw/server.py`
- Test: `tests/gateway/test_broker.py`

- [ ] **Step 1: Write failing test**

Add to `tests/gateway/test_broker.py`:

```python
async def test_delegate_stores_limit_fields():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    task_id = await broker.delegate(
        "orch-1", caps=["python"], instructions="do it",
        max_cost=0.5, max_time=60, allowed_tools=["Read"],
    )
    task = broker.get_task(task_id)
    assert task.max_cost == 0.5
    assert task.max_time == 60
    assert task.allowed_tools == ["Read"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/pytest tests/gateway/test_broker.py::test_delegate_stores_limit_fields -v
```

Expected: FAIL — `delegate() got unexpected keyword argument 'max_cost'`

- [ ] **Step 3: Update Broker.delegate**

In `harness_claw/gateway/broker.py`, update `delegate` signature:

```python
async def delegate(
    self,
    delegated_by: str,
    caps: list[str],
    instructions: str,
    context: dict[str, Any] | None = None,
    callback: bool = False,
    priority: int = 2,
    max_cost: float | None = None,
    max_time: int | None = None,
    allowed_tools: list[str] | None = None,
) -> str:
```

Update the `Task(...)` constructor call to include the new fields:

```python
        task = Task(
            task_id=str(uuid.uuid4()),
            delegated_by=delegated_by,
            delegated_to="",
            instructions=instructions,
            caps_requested=caps,
            context=context,
            status="queued",
            callback=callback,
            priority=priority,
            max_cost=max_cost,
            max_time=max_time,
            allowed_tools=allowed_tools,
        )
```

- [ ] **Step 4: Update GatewayMCP.agent_delegate**

In `harness_claw/gateway/mcp_server.py`, update `agent_delegate`:

```python
async def agent_delegate(
    self,
    token: str,
    caps: list[str],
    instructions: str,
    context: dict[str, Any] | None = None,
    callback: bool = False,
    priority: int = 2,
    max_cost: float | None = None,
    max_time: int | None = None,
    allowed_tools: list[str] | None = None,
) -> dict[str, Any]:
    subject = self._auth(token, "agent:delegate")
    try:
        task_id = await self._broker.delegate(
            delegated_by=subject,
            caps=caps,
            instructions=instructions,
            context=context,
            callback=callback,
            priority=priority,
            max_cost=max_cost,
            max_time=max_time,
            allowed_tools=allowed_tools,
        )
    except ValueError as e:
        self._audit.log(AuditEvent(
            subject=subject, operation="agent.delegate", resource="",
            outcome="error", details={"error": str(e)},
        ))
        raise
    task = self._broker.get_task(task_id)
    self._audit.log(AuditEvent(
        subject=subject, operation="agent.delegate", resource=task_id,
        outcome="allowed", details={
            "caps": caps,
            "callback": callback,
            "priority": priority,
            "max_cost": max_cost,
            "max_time": max_time,
            "delegated_to": task.delegated_to if task else None,
        },
    ))
    return {"task_id": task_id}
```

- [ ] **Step 5: Update agent.delegate MCP tool schema in server.py**

Find the `agent.delegate` tool in `_MCP_TOOLS` and update `inputSchema`:

```python
mcp_types.Tool(name="agent.delegate", description="Delegate a task to the best-matched agent; returns task_id",
    inputSchema={"type": "object", "properties": {
        "caps": {"type": "array", "items": {"type": "string"}},
        "instructions": {"type": "string"},
        "context": {"type": "object"},
        "callback": {"type": "boolean"},
        "priority": {"type": "integer", "description": "1=high, 2=normal (default), 3=low"},
        "max_cost": {"type": "number", "description": "USD cost cap; pauses agent when exceeded"},
        "max_time": {"type": "integer", "description": "Time cap in seconds; pauses agent when exceeded"},
        "allowed_tools": {"type": "array", "items": {"type": "string"}, "description": "Tool allow-list; omit for all tools"},
    }, "required": ["caps", "instructions"]}),
```

Also update the `/mcp/tools/call` handler in `server.py`. The `agent.delegate` handler already passes `**a` so new fields come through automatically — no change needed there.

- [ ] **Step 6: Run tests**

```bash
.venv/bin/pytest tests/gateway/test_broker.py -v
```

Expected: ALL PASS

- [ ] **Step 7: Verify server imports cleanly**

```bash
.venv/bin/python -c "from harness_claw.server import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add harness_claw/gateway/broker.py harness_claw/gateway/mcp_server.py harness_claw/server.py tests/gateway/test_broker.py
git commit -m "feat: add max_cost/max_time/allowed_tools params to agent.delegate"
```

---

## Task 8: Resume endpoint

**Files:**
- Modify: `harness_claw/server.py`
- Test: `tests/test_server_endpoints.py` (create if missing)

- [ ] **Step 1: Write failing test**

Create `tests/test_server_endpoints.py`:

```python
import pytest
from fastapi.testclient import TestClient
from harness_claw.server import app, broker
from harness_claw.gateway.task_store import Task


@pytest.fixture
def client():
    return TestClient(app)


def make_paused_task() -> Task:
    task = Task(
        task_id="paused-1",
        delegated_by="orch",
        delegated_to="s1",
        instructions="do it",
        caps_requested=["python"],
        status="paused",
        pause_reason="Cost $0.51 exceeded limit $0.50",
        resume=True,
    )
    return task


def test_resume_paused_task(client):
    task = make_paused_task()
    broker._store.save(task)

    resp = client.post("/api/tasks/paused-1/resume")
    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data

    reloaded = broker.get_task("paused-1")
    assert reloaded.status == "queued"
    assert reloaded.pause_reason is None


def test_resume_non_paused_task_returns_400(client):
    task = Task(
        task_id="running-1",
        delegated_by="orch",
        delegated_to="s1",
        instructions="do it",
        caps_requested=["python"],
        status="running",
    )
    broker._store.save(task)
    resp = client.post("/api/tasks/running-1/resume")
    assert resp.status_code == 400


def test_resume_missing_task_returns_404(client):
    resp = client.post("/api/tasks/nonexistent/resume")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_server_endpoints.py::test_resume_paused_task tests/test_server_endpoints.py::test_resume_non_paused_task_returns_400 -v
```

Expected: FAIL — 404 (endpoint not found)

- [ ] **Step 3: Add POST /api/tasks/{task_id}/resume endpoint**

In `harness_claw/server.py`, add after the existing `/api/tasks/{task_id}/retry` endpoint:

```python
@app.post("/api/tasks/{task_id}/resume")
async def resume_task(task_id: str) -> dict[str, Any]:
    task = broker.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status != "paused":
        raise HTTPException(status_code=400, detail=f"task status is {task.status!r}, not 'paused'")
    task.status = "queued"
    task.pause_reason = None
    broker._store.save(task)
    broker.scheduler.push(task)
    try:
        asyncio.create_task(broker.scheduler.drain())
    except RuntimeError:
        pass
    return {"task_id": task_id}
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/pytest tests/test_server_endpoints.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add harness_claw/server.py tests/test_server_endpoints.py
git commit -m "feat: add POST /api/tasks/{id}/resume endpoint for paused tasks"
```

---

## Task 9: UI — paused badge, reason, Resume button

**Files:**
- Modify: `ui/src/types.ts`
- Modify: `ui/src/components/TasksTab.tsx`

- [ ] **Step 1: Update TaskRecord in types.ts**

In `ui/src/types.ts`, find the `TaskRecord` interface and update the `status` union and add new fields:

```typescript
export interface TaskRecord {
  task_id: string
  delegated_by: string
  delegated_to: string
  instructions: string
  caps_requested: string[]
  status: 'queued' | 'running' | 'completed' | 'failed' | 'paused'
  progress_pct: number
  progress_msg: string
  result: string | Record<string, unknown> | null
  context: Record<string, unknown> | null
  callback: boolean
  created_at: string
  updated_at: string
  priority: number
  resume: boolean
  max_cost?: number | null
  max_time?: number | null
  allowed_tools?: string[] | null
  provider_session_id?: string | null
  pause_reason?: string | null
}
```

- [ ] **Step 2: Add paused badge color and Resume button to TasksTab.tsx**

In `ui/src/components/TasksTab.tsx`, update `statusColor` to handle `paused`:

```typescript
function statusColor(status: string): string {
  if (status === 'running') return 'text-blue-400'
  if (status === 'completed') return 'text-green-400'
  if (status === 'failed') return 'text-red-400'
  if (status === 'paused') return 'text-yellow-400'
  return 'text-gray-400'
}
```

Update `statusBadge` to handle `paused`:

```typescript
function statusBadge(status: string): string {
  if (status === 'running') return '⟳ running'
  if (status === 'completed') return '✓ done'
  if (status === 'failed') return '✗ failed'
  if (status === 'paused') return '⏸ paused'
  return '· queued'
}
```

Add a `resumeTask` handler function (after the existing handler functions, before the return):

```typescript
const resumeTask = async (taskId: string) => {
  await fetch(`/api/tasks/${taskId}/resume`, { method: 'POST' })
}
```

In the task card JSX, after the pause_reason display (add after the `progress_msg` section), add:

```tsx
{task.pause_reason && (
  <p className="text-xs text-yellow-400 mt-1">{task.pause_reason}</p>
)}
{task.status === 'paused' && (
  <button
    onClick={() => resumeTask(task.task_id)}
    className="mt-2 text-xs px-2 py-1 rounded bg-yellow-500 hover:bg-yellow-400 text-black font-medium"
  >
    Resume
  </button>
)}
```

- [ ] **Step 3: Build UI to verify no TypeScript errors**

```bash
cd /Users/juichanglu/src/HarnessClaw/ui
npm run build 2>&1 | tail -20
```

Expected: build succeeds with no TypeScript errors

- [ ] **Step 4: Commit**

```bash
cd /Users/juichanglu/src/HarnessClaw
git add ui/src/types.ts ui/src/components/TasksTab.tsx
git commit -m "feat: add paused badge, pause reason, and Resume button to TasksTab"
```

---

## Final Verification

- [ ] **Run full test suite**

```bash
.venv/bin/pytest tests/ --ignore=tests/runtime/test_pty_session.py -v 2>&1 | tail -30
```

Expected: ALL PASS

- [ ] **Verify server imports cleanly**

```bash
.venv/bin/python -c "from harness_claw.server import app; print('OK')"
```

Expected: `OK`
