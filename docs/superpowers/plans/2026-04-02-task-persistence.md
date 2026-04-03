# Task Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist tasks to SQLite so they survive server restarts, with 7-day expiry and a retry button for failed tasks.

**Architecture:** `SqliteTaskStore` lives in a new `task_store.py` alongside `Task` and the in-memory `TaskStore` (moved from `broker.py`). `Broker` accepts an optional `task_store` param, defaulting to the in-memory store so all existing tests continue to pass. On startup, expired tasks are pruned and in-flight tasks are marked `failed` with reason `"server_restart"`. A `GET /api/tasks` endpoint hydrates the dashboard on load; `POST /api/tasks/{task_id}/retry` re-delegates failed tasks. The UI loads task history on mount and shows a retry button on expanded failed-task rows.

**Tech Stack:** Python 3.12, sqlite3 (stdlib), pytest, pytest-asyncio (asyncio_mode = "auto"), React + TypeScript

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `harness_claw/gateway/task_store.py` | `Task` dataclass, `TaskStore` (in-memory), `SqliteTaskStore` |
| `tests/gateway/test_task_store.py` | Unit tests for `SqliteTaskStore` |
| `tests/test_role_registry.py` | Tests for `GatewayConfig.task_retention_days` |

### Modified Files

| File | Changes |
|------|---------|
| `harness_claw/gateway/broker.py` | Remove `Task`+`TaskStore` definitions; import from `task_store`; add `task_store` param to `Broker.__init__` |
| `harness_claw/role_registry.py` | Add `task_retention_days: int = 7` to `GatewayConfig`; parse `tasks.retention_days` |
| `agents.yaml` | Add `tasks:\n  retention_days: 7` |
| `harness_claw/server.py` | Import `SqliteTaskStore`; add `_tasks_db`; wire startup; add `GET /api/tasks` and `POST /api/tasks/{task_id}/retry` |
| `ui/src/types.ts` | Add `task.failed` event to `WSIncoming` |
| `ui/src/App.tsx` | Fetch `/api/tasks` on mount; handle `task.failed` WS event; pass `onRetry` to `TasksTab` |
| `ui/src/components/TasksTab.tsx` | Accept `onRetry` prop; render retry button for failed tasks |

---

### Task 1: Extract Task + TaskStore into task_store.py, add SqliteTaskStore

**Files:**
- Create: `harness_claw/gateway/task_store.py`
- Create: `tests/gateway/test_task_store.py`
- Modify: `harness_claw/gateway/broker.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/gateway/test_task_store.py
from __future__ import annotations

import sqlite3

import pytest

from harness_claw.gateway.task_store import SqliteTaskStore, Task


def make_task(**kwargs) -> Task:
    defaults = dict(
        task_id="t1",
        delegated_by="agent-a",
        delegated_to="agent-b",
        instructions="do the thing",
        caps_requested=["python"],
    )
    defaults.update(kwargs)
    return Task(**defaults)


def test_save_and_get(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    store.save(make_task())
    result = store.get("t1")
    assert result is not None
    assert result.task_id == "t1"
    assert result.delegated_by == "agent-a"
    assert result.caps_requested == ["python"]


def test_save_updates_existing(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    task = make_task()
    store.save(task)
    task.status = "completed"
    task.progress_pct = 100
    store.save(task)
    result = store.get("t1")
    assert result.status == "completed"
    assert result.progress_pct == 100


def test_get_missing_returns_none(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    assert store.get("nonexistent") is None


def test_all_returns_all_tasks(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    store.save(make_task(task_id="t1"))
    store.save(make_task(task_id="t2"))
    tasks = store.all()
    assert len(tasks) == 2
    assert {t.task_id for t in tasks} == {"t1", "t2"}


def test_mark_stale_as_failed(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    store.save(make_task(task_id="q", status="queued"))
    store.save(make_task(task_id="r", status="running"))
    store.save(make_task(task_id="d", status="completed"))
    count = store.mark_stale_as_failed()
    assert count == 2
    assert store.get("q").status == "failed"
    assert store.get("r").status == "failed"
    assert store.get("q").result == "server_restart"
    assert store.get("d").status == "completed"  # unchanged


def test_expire_removes_old_tasks(tmp_path):
    from datetime import datetime, timedelta, timezone
    store = SqliteTaskStore(tmp_path / "tasks.db")
    store.save(make_task(task_id="old", status="completed"))
    old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        conn.execute("UPDATE tasks SET updated_at = ? WHERE task_id = 'old'", (old_time,))
    store.save(make_task(task_id="new", status="completed"))
    count = store.expire(days=7)
    assert count == 1
    assert store.get("old") is None
    assert store.get("new") is not None


def test_roundtrip_context_and_result(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    task = make_task(
        context={"key": "value", "num": 42},
        result={"output": "done"},
        callback=True,
    )
    store.save(task)
    loaded = store.get(task.task_id)
    assert loaded.context == {"key": "value", "num": 42}
    assert loaded.result == {"output": "done"}
    assert loaded.callback is True


def test_string_result_roundtrip(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    task = make_task(result="server_restart")
    store.save(task)
    loaded = store.get(task.task_id)
    assert loaded.result == "server_restart"


def test_persists_across_instances(tmp_path):
    db_path = tmp_path / "tasks.db"
    store1 = SqliteTaskStore(db_path)
    store1.save(make_task(task_id="persistent"))
    store2 = SqliteTaskStore(db_path)
    assert store2.get("persistent") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_task_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness_claw.gateway.task_store'`

- [ ] **Step 3: Create task_store.py**

```python
# harness_claw/gateway/task_store.py
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass
class Task:
    task_id: str
    delegated_by: str
    delegated_to: str
    instructions: str
    caps_requested: list[str]
    context: dict[str, Any] | None = None
    status: str = "queued"       # queued | running | completed | failed
    progress_pct: int = 0
    progress_msg: str = ""
    result: dict[str, Any] | str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    callback: bool = False

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
        }


class TaskStore:
    """In-memory task store. Default fallback; used in tests."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def save(self, task: Task) -> None:
        task.updated_at = datetime.now(timezone.utc).isoformat()
        self._tasks[task.task_id] = task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def all(self) -> list[Task]:
        return list(self._tasks.values())


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id        TEXT PRIMARY KEY,
    delegated_by   TEXT NOT NULL,
    delegated_to   TEXT NOT NULL,
    instructions   TEXT NOT NULL,
    caps_requested TEXT NOT NULL,
    context        TEXT,
    status         TEXT NOT NULL,
    progress_pct   INTEGER NOT NULL DEFAULT 0,
    progress_msg   TEXT NOT NULL DEFAULT '',
    result         TEXT,
    callback       INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
)
"""


def _row_to_task(row: sqlite3.Row) -> Task:
    result_raw = row["result"]
    if result_raw is None:
        result: dict[str, Any] | str | None = None
    else:
        try:
            result = json.loads(result_raw)
        except (json.JSONDecodeError, TypeError):
            result = result_raw

    return Task(
        task_id=row["task_id"],
        delegated_by=row["delegated_by"],
        delegated_to=row["delegated_to"],
        instructions=row["instructions"],
        caps_requested=json.loads(row["caps_requested"]),
        context=json.loads(row["context"]) if row["context"] else None,
        status=row["status"],
        progress_pct=row["progress_pct"],
        progress_msg=row["progress_msg"],
        result=result,
        callback=bool(row["callback"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SqliteTaskStore:
    """SQLite-backed task store. Same save/get/all interface as TaskStore."""

    def __init__(self, path: Path) -> None:
        self._path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, task: Task) -> None:
        task.updated_at = datetime.now(timezone.utc).isoformat()
        result_json = json.dumps(task.result) if task.result is not None else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks
                    (task_id, delegated_by, delegated_to, instructions, caps_requested,
                     context, status, progress_pct, progress_msg, result, callback,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status       = excluded.status,
                    progress_pct = excluded.progress_pct,
                    progress_msg = excluded.progress_msg,
                    result       = excluded.result,
                    callback     = excluded.callback,
                    updated_at   = excluded.updated_at
                """,
                (
                    task.task_id, task.delegated_by, task.delegated_to, task.instructions,
                    json.dumps(task.caps_requested),
                    json.dumps(task.context) if task.context is not None else None,
                    task.status, task.progress_pct, task.progress_msg,
                    result_json, int(task.callback),
                    task.created_at, task.updated_at,
                ),
            )

    def get(self, task_id: str) -> Task | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return _row_to_task(row) if row else None

    def all(self) -> list[Task]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at ASC"
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def mark_stale_as_failed(self) -> int:
        """Mark queued/running tasks failed with reason 'server_restart'. Returns count updated."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'failed', result = ?, updated_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (json.dumps("server_restart"), now),
            )
        return cursor.rowcount

    def expire(self, days: int) -> int:
        """Delete tasks with updated_at older than `days` days. Returns count deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM tasks WHERE updated_at < ?", (cutoff,)
            )
        return cursor.rowcount
```

- [ ] **Step 4: Update broker.py — remove Task + TaskStore, import from task_store**

In `harness_claw/gateway/broker.py`:

1. Replace this import block at the top:
```python
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol
```
With:
```python
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol
```

2. Add this import after the existing gateway imports:
```python
from harness_claw.gateway.task_store import Task, TaskStore
```

3. Delete the `@dataclass class Task` block (lines 13–44) and the `class TaskStore` block (lines 47–59) entirely. The `TaskStore` and `Task` names are now re-exported via the import above, so existing tests importing them from `harness_claw.gateway.broker` continue to work.

- [ ] **Step 5: Run tests**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_task_store.py tests/gateway/test_broker.py -v`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add harness_claw/gateway/task_store.py tests/gateway/test_task_store.py harness_claw/gateway/broker.py
git commit -m "feat: extract Task+TaskStore to task_store.py, add SqliteTaskStore"
```

---

### Task 2: Wire SqliteTaskStore into Broker

**Files:**
- Modify: `harness_claw/gateway/broker.py`
- Modify: `tests/gateway/test_broker.py`

- [ ] **Step 1: Write the failing test**

Add to the bottom of `tests/gateway/test_broker.py`:

```python
async def test_task_persists_across_broker_instances(tmp_path):
    from harness_claw.gateway.task_store import SqliteTaskStore

    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    store = SqliteTaskStore(tmp_path / "tasks.db")
    broker1 = Broker(connectors=[conn], dispatcher=AsyncMock(), task_store=store)

    task_id = await broker1.delegate(
        delegated_by="orchestrator-1",
        caps=["python"],
        instructions="survive the restart",
    )

    # Simulate restart: new Broker instance reusing same store
    broker2 = Broker(connectors=[conn], dispatcher=AsyncMock(), task_store=store)
    task = broker2.get_task(task_id)
    assert task is not None
    assert task.task_id == task_id
    assert task.instructions == "survive the restart"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_broker.py::test_task_persists_across_broker_instances -v`
Expected: FAIL — `TypeError: Broker.__init__() got an unexpected keyword argument 'task_store'`

- [ ] **Step 3: Add task_store param to Broker.__init__**

In `harness_claw/gateway/broker.py`, update `Broker.__init__` signature and body:

```python
def __init__(
    self,
    connectors: list[CapabilityConnector],
    dispatcher: TaskDispatcher,
    event_bus: EventBus | None = None,
    task_store: TaskStore | None = None,
) -> None:
    self._connectors = connectors
    self._dispatcher = dispatcher
    self._event_bus = event_bus
    self._store = task_store or TaskStore()
    self._listeners: list[Any] = []
    self._callback_handlers: dict[str, Any] = {}
    self._callback_subs: dict[str, list[Any]] = {}
```

- [ ] **Step 4: Run all broker tests**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_broker.py -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/broker.py tests/gateway/test_broker.py
git commit -m "feat: broker accepts optional task_store for pluggable persistence"
```

---

### Task 3: Config — task_retention_days in agents.yaml + GatewayConfig

**Files:**
- Modify: `agents.yaml`
- Modify: `harness_claw/role_registry.py`
- Create: `tests/test_role_registry.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_role_registry.py
from __future__ import annotations

from pathlib import Path

import pytest

from harness_claw.role_registry import RoleRegistry


def _write_yaml(tmp_path: Path, extra: str = "") -> Path:
    p = tmp_path / "agents.yaml"
    p.write_text(f"""
policy:
  engine: local
memory:
  backend: sqlite
  path: ./memory.db
broker:
  dispatcher: local
event_bus:
  backend: local
{extra}
connectors: []
roles: []
""")
    return p


def test_task_retention_days_from_yaml(tmp_path):
    p = _write_yaml(tmp_path, "tasks:\n  retention_days: 14")
    registry = RoleRegistry(p)
    assert registry.gateway_config.task_retention_days == 14


def test_task_retention_days_defaults_to_7(tmp_path):
    p = _write_yaml(tmp_path)
    registry = RoleRegistry(p)
    assert registry.gateway_config.task_retention_days == 7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/test_role_registry.py -v`
Expected: FAIL — `AttributeError: 'GatewayConfig' object has no attribute 'task_retention_days'`

- [ ] **Step 3: Update GatewayConfig**

In `harness_claw/role_registry.py`, add `task_retention_days` to `GatewayConfig`:

```python
@dataclass
class GatewayConfig:
    policy_engine: str = "local"
    memory_backend: str = "sqlite"
    memory_path: str = "./memory.db"
    dispatcher: str = "local"
    event_bus_backend: str = "local"
    gateway_bootstrap_token: str = ""
    gateway_heartbeat_ttl: int = 30
    task_retention_days: int = 7
```

- [ ] **Step 4: Parse tasks section in RoleRegistry.__init__**

In `RoleRegistry.__init__`, add `tasks` parsing and pass it to `GatewayConfig`. The current block reads:

```python
policy = data.get("policy", {})
memory = data.get("memory", {})
broker = data.get("broker", {})
event_bus = data.get("event_bus", {})
gateway_connector = next(
    (c for c in data.get("connectors", []) if c.get("type") == "gateway"),
    {}
)
self.gateway_config = GatewayConfig(
    policy_engine=policy.get("engine", "local"),
    memory_backend=memory.get("backend", "sqlite"),
    memory_path=memory.get("path", "./memory.db"),
    dispatcher=broker.get("dispatcher", "local"),
    event_bus_backend=event_bus.get("backend", "local"),
    gateway_bootstrap_token=gateway_connector.get("bootstrap_token", ""),
    gateway_heartbeat_ttl=gateway_connector.get("heartbeat_ttl", 30),
)
```

Replace with:

```python
policy = data.get("policy", {})
memory = data.get("memory", {})
broker = data.get("broker", {})
event_bus = data.get("event_bus", {})
tasks = data.get("tasks", {})
gateway_connector = next(
    (c for c in data.get("connectors", []) if c.get("type") == "gateway"),
    {}
)
self.gateway_config = GatewayConfig(
    policy_engine=policy.get("engine", "local"),
    memory_backend=memory.get("backend", "sqlite"),
    memory_path=memory.get("path", "./memory.db"),
    dispatcher=broker.get("dispatcher", "local"),
    event_bus_backend=event_bus.get("backend", "local"),
    gateway_bootstrap_token=gateway_connector.get("bootstrap_token", ""),
    gateway_heartbeat_ttl=gateway_connector.get("heartbeat_ttl", 30),
    task_retention_days=tasks.get("retention_days", 7),
)
```

- [ ] **Step 5: Add tasks section to agents.yaml**

Add after the `event_bus` section in `agents.yaml`:

```yaml
tasks:
  retention_days: 7
```

- [ ] **Step 6: Run tests**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/test_role_registry.py -v`
Expected: Both pass

- [ ] **Step 7: Commit**

```bash
git add harness_claw/role_registry.py agents.yaml tests/test_role_registry.py
git commit -m "feat: add task_retention_days to GatewayConfig, parse from agents.yaml"
```

---

### Task 4: Server wiring — SqliteTaskStore, startup lifecycle, REST endpoints

**Files:**
- Modify: `harness_claw/server.py`

- [ ] **Step 1: Import SqliteTaskStore and add _tasks_db**

In `harness_claw/server.py`, add to the imports:

```python
from harness_claw.gateway.task_store import SqliteTaskStore
```

Add alongside the other path constants (after `_memory_db`):

```python
_tasks_db = _root / "tasks.db"
```

- [ ] **Step 2: Instantiate SqliteTaskStore and pass to Broker**

Replace:

```python
broker = Broker(connectors=[connector, gateway_connector], dispatcher=dispatcher, event_bus=event_bus)
```

With:

```python
task_store = SqliteTaskStore(_tasks_db)
broker = Broker(connectors=[connector, gateway_connector], dispatcher=dispatcher, event_bus=event_bus, task_store=task_store)
```

- [ ] **Step 3: Wire startup lifecycle in the startup handler**

In the `startup` handler, add these two lines at the very top of the function body, before the existing broker listener setup:

```python
@app.on_event("startup")
async def startup() -> None:
    task_store.expire(cfg.task_retention_days)
    task_store.mark_stale_as_failed()

    # Wire broker task events into WebSocket broadcast  (rest unchanged)
    async def on_task_event(event: str, task_dict: dict[str, Any]) -> None:
        await runner._broadcast({"type": event, "task": task_dict})

    broker.add_listener(on_task_event)
    # ... rest of startup unchanged
```

- [ ] **Step 4: Add GET /api/tasks endpoint**

Add after the existing `get_audit` endpoint:

```python
@app.get("/api/tasks")
def list_tasks_endpoint() -> list[dict[str, Any]]:
    return [t.to_dict() for t in broker.list_tasks()]
```

- [ ] **Step 5: Add POST /api/tasks/{task_id}/retry endpoint**

Add immediately after `list_tasks_endpoint`:

```python
@app.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: str) -> dict[str, Any]:
    task = broker.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status != "failed":
        raise HTTPException(status_code=400, detail=f"task status is {task.status!r}, not 'failed'")
    new_task_id = await broker.delegate(
        delegated_by=task.delegated_by,
        caps=task.caps_requested,
        instructions=task.instructions,
        context=task.context,
        callback=task.callback,
    )
    return {"task_id": new_task_id}
```

- [ ] **Step 6: Verify server imports cleanly**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -c "from harness_claw.server import app; print('OK')"`
Expected: `OK`

- [ ] **Step 7: Run all tests**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/ -v`
Expected: All pass (104+ tests)

- [ ] **Step 8: Commit**

```bash
git add harness_claw/server.py
git commit -m "feat: wire SqliteTaskStore into server, add GET /api/tasks and POST /api/tasks/{id}/retry"
```

---

### Task 5: UI — load task history on mount, retry button for failed tasks

**Files:**
- Modify: `ui/src/types.ts`
- Modify: `ui/src/App.tsx`
- Modify: `ui/src/components/TasksTab.tsx`

- [ ] **Step 1: Add task.failed to WSIncoming in types.ts**

In `ui/src/types.ts`, update `WSIncoming`:

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
```

- [ ] **Step 2: Fetch tasks on mount in App.tsx**

In `ui/src/App.tsx`, inside the existing `useEffect` that loads roles, add a fetch for tasks:

```typescript
useEffect(() => {
  fetch('/api/roles').then(r => r.json()).then(setRoles).catch(console.error)
  fetch('/api/mcp/tools').then(r => r.json()).then(setMcpTools).catch(console.error)
  fetch('/api/tasks')
    .then(r => r.json())
    .then((taskList: TaskRecord[]) => {
      const taskMap: Record<string, TaskRecord> = {}
      for (const t of taskList) taskMap[t.task_id] = t
      setTasks(taskMap)
    })
    .catch(console.error)
  fetch('/api/sessions').then(r => r.json()).then(/* ... existing handler unchanged ... */)
}, [])
```

- [ ] **Step 3: Handle task.failed in handleWsMessage in App.tsx**

In `handleWsMessage`, update the task event condition from:

```typescript
} else if (msg.type === 'task.created' || msg.type === 'task.updated' || msg.type === 'task.completed') {
```

To:

```typescript
} else if (
  msg.type === 'task.created' ||
  msg.type === 'task.updated' ||
  msg.type === 'task.completed' ||
  msg.type === 'task.failed'
) {
```

- [ ] **Step 4: Add handleRetry and pass to TasksTab in App.tsx**

Add `handleRetry` alongside the other callbacks:

```typescript
const handleRetry = useCallback(async (taskId: string) => {
  await fetch(`/api/tasks/${taskId}/retry`, { method: 'POST' })
  // new task arrives via WS task.created — no additional state update needed
}, [])
```

Update the `TasksTab` render in the JSX:

```tsx
{tab === 'tasks' && (
  <TasksTab
    tasks={Object.values(tasks)}
    sessions={sessions}
    terminalWriters={terminalWriters}
    onInput={(sessionId, data) => wsRef.current?.send({ type: 'input', session_id: sessionId, data })}
    onResize={(sessionId, cols, rows) => wsRef.current?.send({ type: 'resize', session_id: sessionId, cols, rows })}
    onRetry={handleRetry}
  />
)}
```

- [ ] **Step 5: Add onRetry prop and retry button to TasksTab.tsx**

Update `Props` interface to include `onRetry`:

```typescript
interface Props {
  tasks: TaskRecord[]
  sessions: Record<string, SessionState>
  terminalWriters: MutableRefObject<Record<string, (data: Uint8Array) => void>>
  onInput: (sessionId: string, data: string) => void
  onResize: (sessionId: string, cols: number, rows: number) => void
  onRetry: (taskId: string) => void
}
```

Update `TaskRow` to accept and use `onRetry`:

```typescript
function TaskRow({ task, sessions, terminalWriters, expanded, onToggle, onRetry }: {
  task: TaskRecord
  sessions: Record<string, SessionState>
  terminalWriters: MutableRefObject<Record<string, (data: Uint8Array) => void>>
  expanded: boolean
  onToggle: () => void
  onRetry: (taskId: string) => void
}) {
  const agentSession = sessions[task.delegated_to]
  const agentName = agentSession?.name || task.delegated_to.slice(0, 8)

  return (
    <div className="border border-gray-700 rounded-lg overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 p-3 bg-gray-800 hover:bg-gray-750 text-left"
      >
        <span className="text-gray-500 text-xs w-4">{expanded ? '▼' : '▶'}</span>
        <span className="text-xs text-gray-400 font-mono w-20 truncate">{task.task_id.slice(0, 8)}</span>
        <span className="text-sm text-gray-200 flex-1 truncate">{agentName}</span>
        <div className="w-24">
          {task.status === 'running' && <ProgressBar pct={task.progress_pct} />}
        </div>
        <span className={`text-xs w-20 text-right ${statusColor(task.status)}`}>
          {statusBadge(task.status)}
        </span>
      </button>

      {expanded && (
        <div className="p-3 bg-gray-900 border-t border-gray-700 flex flex-col gap-2">
          <div className="flex gap-4 text-xs text-gray-500">
            <span>from: {task.delegated_by.slice(0, 8)}</span>
            <span>caps: {task.caps_requested.join(', ')}</span>
            {task.progress_msg && <span>{task.progress_msg}</span>}
          </div>
          <TaskTerminalPanel sessionId={task.delegated_to} terminalWriters={terminalWriters} />
          {task.result && (
            <div className="text-xs text-green-400 bg-gray-800 rounded p-2 whitespace-pre-wrap">
              {typeof task.result === 'string' ? task.result : JSON.stringify(task.result, null, 2)}
            </div>
          )}
          {task.status === 'failed' && (
            <button
              onClick={() => onRetry(task.task_id)}
              className="self-start text-xs text-yellow-400 hover:text-yellow-300 px-2 py-1 border border-yellow-800 rounded"
            >
              ↺ Retry
            </button>
          )}
        </div>
      )}
    </div>
  )
}
```

Update `TasksTab` to destructure and forward `onRetry`:

```typescript
export function TasksTab({ tasks, sessions, terminalWriters, onInput: _onInput, onResize: _onResize, onRetry }: Props) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())

  const toggle = useCallback((id: string) => {
    setExpandedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  if (tasks.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
        No tasks yet
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-2">
      {[...tasks].reverse().map(task => (
        <TaskRow
          key={task.task_id}
          task={task}
          sessions={sessions}
          terminalWriters={terminalWriters}
          expanded={expandedIds.has(task.task_id)}
          onToggle={() => toggle(task.task_id)}
          onRetry={onRetry}
        />
      ))}
    </div>
  )
}
```

- [ ] **Step 6: Build UI to verify no TypeScript errors**

Run: `cd /Users/juichanglu/src/HarnessClaw/ui && npm run build 2>&1 | tail -20`
Expected: Build succeeds with no TypeScript errors

- [ ] **Step 7: Run all Python tests**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add ui/src/types.ts ui/src/App.tsx ui/src/components/TasksTab.tsx
git commit -m "feat: load task history on mount, add retry button for failed tasks"
```
