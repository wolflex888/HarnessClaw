# Run Object Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a `Run` dataclass and `SqliteRunStore` that record every agent execution as a first-class object, with a `RunManager` that listens to EventBus and keeps run state current.

**Architecture:** `Run` is a top-level envelope persisted in `runs.db`; it parents many `Task`s via `task_ids`. `RunManager` subscribes to broad `task.*` EventBus topics (published by `Broker`) and a direct `on_session_ended()` call from `JobRunner` to drive status transitions. `run_manager` is wired into `JobRunner` and `Broker` as an optional dependency so all existing tests require zero changes.

**Tech Stack:** Python 3.12, SQLite (sqlite3 stdlib), asyncio, FastAPI, existing EventBus / Broker patterns.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `harness_claw/runtime/run_store.py` | **Create** | `Run` dataclass + `SqliteRunStore` |
| `harness_claw/runtime/run_manager.py` | **Create** | `RunManager`: create_run, event subs, broadcast |
| `harness_claw/role_registry.py` | **Modify** | Add `RoleConfig.to_dict()` |
| `harness_claw/gateway/broker.py` | **Modify** | Publish broad `task.*` events to EventBus; accept `run_manager` in `delegate()` |
| `harness_claw/runtime/job_runner.py` | **Modify** | Accept `run_manager`; call `create_run()` in `start_session()`; call `on_session_ended()` in `kill_session()` |
| `harness_claw/server.py` | **Modify** | Instantiate `SqliteRunStore` + `RunManager`; add REST endpoints |
| `tests/runtime/test_run_store.py` | **Create** | `Run` roundtrip + `SqliteRunStore` CRUD |
| `tests/runtime/test_run_manager.py` | **Create** | `RunManager` unit + integration tests |

---

## Task 1: `Run` dataclass and `SqliteRunStore`

**Files:**
- Create: `harness_claw/runtime/run_store.py`
- Create: `tests/runtime/test_run_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/runtime/test_run_store.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_claw.runtime.run_store import Run, SqliteRunStore


def make_run(**kwargs) -> Run:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    defaults = dict(
        run_id="run-1",
        agent_id="session-1",
        trigger_type="manual",
        caller="user",
        goal="Do the thing",
        input_payload={"key": "val"},
        budget_usd=1.0,
        timeout_secs=300,
        permissions_snapshot={"scopes": ["memory:read"], "role": {"id": "assistant"}},
        status="running",
        task_ids=[],
        checkpoint=None,
        created_at=now,
        updated_at=now,
    )
    defaults.update(kwargs)
    return Run(**defaults)


def test_run_to_dict_roundtrip():
    run = make_run()
    d = run.to_dict()
    assert d["run_id"] == "run-1"
    assert d["agent_id"] == "session-1"
    assert d["trigger_type"] == "manual"
    assert d["input_payload"] == {"key": "val"}
    assert d["task_ids"] == []
    assert d["checkpoint"] is None


def test_run_from_dict_roundtrip():
    run = make_run(task_ids=["t1", "t2"], checkpoint={"last_task_id": "t2"})
    restored = Run.from_dict(run.to_dict())
    assert restored.run_id == run.run_id
    assert restored.task_ids == ["t1", "t2"]
    assert restored.checkpoint == {"last_task_id": "t2"}


def test_sqlite_run_store_save_and_get(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    run = make_run()
    store.save(run)
    fetched = store.get("run-1")
    assert fetched is not None
    assert fetched.run_id == "run-1"
    assert fetched.input_payload == {"key": "val"}


def test_sqlite_run_store_upsert(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    run = make_run()
    store.save(run)
    run.status = "completed"
    run.task_ids = ["t1"]
    store.save(run)
    fetched = store.get("run-1")
    assert fetched.status == "completed"
    assert fetched.task_ids == ["t1"]


def test_sqlite_run_store_get_by_agent(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    store.save(make_run(run_id="r1", agent_id="agent-A"))
    store.save(make_run(run_id="r2", agent_id="agent-B"))
    store.save(make_run(run_id="r3", agent_id="agent-A"))
    runs = store.get_by_agent("agent-A")
    assert len(runs) == 2
    assert {r.run_id for r in runs} == {"r1", "r3"}


def test_sqlite_run_store_get_by_status(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    store.save(make_run(run_id="r1", status="running"))
    store.save(make_run(run_id="r2", status="completed"))
    store.save(make_run(run_id="r3", status="running"))
    runs = store.get_by_status("running")
    assert len(runs) == 2

def test_sqlite_run_store_all(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    store.save(make_run(run_id="r1"))
    store.save(make_run(run_id="r2"))
    assert len(store.all()) == 2


def test_sqlite_run_store_null_checkpoint(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    run = make_run(checkpoint=None)
    store.save(run)
    fetched = store.get("run-1")
    assert fetched.checkpoint is None


def test_sqlite_run_store_with_checkpoint(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    cp = {"provider_session_id": "abc", "last_task_id": "t1", "progress_pct": 42, "pause_reason": None}
    run = make_run(checkpoint=cp)
    store.save(run)
    fetched = store.get("run-1")
    assert fetched.checkpoint == cp
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/juichanglu/src/HarnessClaw
python -m pytest tests/runtime/test_run_store.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'harness_claw.runtime.run_store'`

- [ ] **Step 3: Write `run_store.py`**

```python
# harness_claw/runtime/run_store.py
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Run:
    run_id: str
    agent_id: str
    trigger_type: str          # manual|delegate|spawn|workflow|schedule|event|recovery
    caller: str                # "user", task_id, workflow_id, etc.
    goal: str
    input_payload: dict
    budget_usd: float | None
    timeout_secs: int | None
    permissions_snapshot: dict  # {"scopes": [...], "role": {...}}
    status: str                 # pending|running|paused|completed|failed
    task_ids: list[str]
    checkpoint: dict | None     # {provider_session_id, last_task_id, progress_pct, pause_reason}
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "trigger_type": self.trigger_type,
            "caller": self.caller,
            "goal": self.goal,
            "input_payload": self.input_payload,
            "budget_usd": self.budget_usd,
            "timeout_secs": self.timeout_secs,
            "permissions_snapshot": self.permissions_snapshot,
            "status": self.status,
            "task_ids": self.task_ids,
            "checkpoint": self.checkpoint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Run:
        return cls(
            run_id=data["run_id"],
            agent_id=data["agent_id"],
            trigger_type=data["trigger_type"],
            caller=data["caller"],
            goal=data["goal"],
            input_payload=data["input_payload"],
            budget_usd=data.get("budget_usd"),
            timeout_secs=data.get("timeout_secs"),
            permissions_snapshot=data["permissions_snapshot"],
            status=data["status"],
            task_ids=data.get("task_ids", []),
            checkpoint=data.get("checkpoint"),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS runs (
    run_id               TEXT PRIMARY KEY,
    agent_id             TEXT NOT NULL,
    trigger_type         TEXT NOT NULL,
    caller               TEXT NOT NULL,
    goal                 TEXT NOT NULL,
    input_payload        TEXT NOT NULL,
    budget_usd           REAL,
    timeout_secs         INTEGER,
    permissions_snapshot TEXT NOT NULL,
    status               TEXT NOT NULL,
    task_ids             TEXT NOT NULL,
    checkpoint           TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
)
"""


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        run_id=row["run_id"],
        agent_id=row["agent_id"],
        trigger_type=row["trigger_type"],
        caller=row["caller"],
        goal=row["goal"],
        input_payload=json.loads(row["input_payload"]),
        budget_usd=row["budget_usd"],
        timeout_secs=row["timeout_secs"],
        permissions_snapshot=json.loads(row["permissions_snapshot"]),
        status=row["status"],
        task_ids=json.loads(row["task_ids"]),
        checkpoint=json.loads(row["checkpoint"]) if row["checkpoint"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SqliteRunStore:
    def __init__(self, path: Path) -> None:
        self._path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, run: Run) -> None:
        run.updated_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs
                    (run_id, agent_id, trigger_type, caller, goal, input_payload,
                     budget_usd, timeout_secs, permissions_snapshot, status,
                     task_ids, checkpoint, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status               = excluded.status,
                    task_ids             = excluded.task_ids,
                    checkpoint           = excluded.checkpoint,
                    updated_at           = excluded.updated_at
                """,
                (
                    run.run_id, run.agent_id, run.trigger_type, run.caller,
                    run.goal, json.dumps(run.input_payload),
                    run.budget_usd, run.timeout_secs,
                    json.dumps(run.permissions_snapshot), run.status,
                    json.dumps(run.task_ids),
                    json.dumps(run.checkpoint) if run.checkpoint is not None else None,
                    run.created_at, run.updated_at,
                ),
            )

    def get(self, run_id: str) -> Run | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return _row_to_run(row) if row else None

    def all(self) -> list[Run]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at ASC"
            ).fetchall()
        return [_row_to_run(r) for r in rows]

    def get_by_agent(self, agent_id: str) -> list[Run]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE agent_id = ? ORDER BY created_at ASC",
                (agent_id,),
            ).fetchall()
        return [_row_to_run(r) for r in rows]

    def get_by_status(self, status: str) -> list[Run]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE status = ? ORDER BY created_at ASC",
                (status,),
            ).fetchall()
        return [_row_to_run(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/runtime/test_run_store.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/runtime/run_store.py tests/runtime/test_run_store.py
git commit -m "feat: add Run dataclass and SqliteRunStore"
```

---

## Task 2: `RoleConfig.to_dict()`

**Files:**
- Modify: `harness_claw/role_registry.py`
- Modify: `tests/runtime/test_role_registry.py` (or create `tests/test_role_registry.py` if it exists)

`RoleConfig` has no `to_dict()` today. The `permissions_snapshot` in `create_run()` needs it.

- [ ] **Step 1: Write the failing test**

Open `tests/test_role_registry.py` (already exists). Add at the bottom:

```python
def test_role_config_to_dict():
    from harness_claw.role_registry import RoleConfig
    role = RoleConfig(
        id="writer",
        name="Code Writer",
        provider="claude-code",
        model="claude-sonnet-4-6",
        system_prompt="You write code.",
        max_tokens=4096,
        scopes=["memory:read"],
        caps=["python"],
        max_cost=2.0,
        max_time=600,
        allowed_tools=["Bash", "Edit"],
    )
    d = role.to_dict()
    assert d["id"] == "writer"
    assert d["scopes"] == ["memory:read"]
    assert d["caps"] == ["python"]
    assert d["max_cost"] == 2.0
    assert d["allowed_tools"] == ["Bash", "Edit"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_role_registry.py::test_role_config_to_dict -v
```

Expected: `AttributeError: 'RoleConfig' object has no attribute 'to_dict'`

- [ ] **Step 3: Add `to_dict()` to `RoleConfig` in `harness_claw/role_registry.py`**

After the `allowed_tools` field in the `RoleConfig` dataclass, add the method:

```python
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "system_prompt": self.system_prompt,
            "max_tokens": self.max_tokens,
            "scopes": self.scopes,
            "caps": self.caps,
            "max_cost": self.max_cost,
            "max_time": self.max_time,
            "allowed_tools": self.allowed_tools,
        }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_role_registry.py::test_role_config_to_dict -v
```

Expected: PASS.

- [ ] **Step 5: Run full test suite to check nothing broke**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```

Expected: all previously-passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add harness_claw/role_registry.py tests/test_role_registry.py
git commit -m "feat: add RoleConfig.to_dict()"
```

---

## Task 3: Publish broad `task.*` events to EventBus in Broker

**Files:**
- Modify: `harness_claw/gateway/broker.py`
- Modify: `tests/gateway/test_broker.py`

The EventBus currently only receives task-specific events (`task:{id}:completed`, `task:{id}:failed`). `RunManager` needs generic `task.created`, `task.updated`, `task.completed`, `task.failed` events with the full task payload.

- [ ] **Step 1: Write the failing tests**

Open `tests/gateway/test_broker.py` and add:

```python
@pytest.mark.asyncio
async def test_broker_publishes_task_created_to_event_bus():
    from harness_claw.gateway.event_bus import LocalEventBus, Event
    from harness_claw.gateway.broker import Broker, LocalDispatcher
    from harness_claw.gateway.capability import LocalConnector

    event_bus = LocalEventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    await event_bus.subscribe("task.created", handler)

    broker = Broker(
        connectors=[LocalConnector()],
        dispatcher=LocalDispatcher(),
        event_bus=event_bus,
    )
    await broker.delegate(delegated_by="user", caps=["python"], instructions="do it")
    await asyncio.sleep(0)  # let create_task coroutines run

    assert len(received) == 1
    assert received[0].payload["task"]["status"] in ("queued", "running")


@pytest.mark.asyncio
async def test_broker_publishes_task_completed_to_event_bus():
    from harness_claw.gateway.event_bus import LocalEventBus, Event
    from harness_claw.gateway.broker import Broker, LocalDispatcher
    from harness_claw.gateway.capability import LocalConnector
    from harness_claw.gateway.task_store import TaskStore

    event_bus = LocalEventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    await event_bus.subscribe("task.completed", handler)

    task_store = TaskStore()
    broker = Broker(
        connectors=[LocalConnector()],
        dispatcher=LocalDispatcher(),
        event_bus=event_bus,
        task_store=task_store,
    )
    task_id = await broker.delegate(delegated_by="user", caps=["python"], instructions="do it")
    await broker.complete_task(task_id, result={"ok": True})
    await asyncio.sleep(0)

    assert len(received) == 1
    assert received[0].payload["task"]["status"] == "completed"


@pytest.mark.asyncio
async def test_broker_publishes_task_failed_to_event_bus():
    from harness_claw.gateway.event_bus import LocalEventBus, Event
    from harness_claw.gateway.broker import Broker, LocalDispatcher
    from harness_claw.gateway.capability import LocalConnector
    from harness_claw.gateway.task_store import TaskStore

    event_bus = LocalEventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    await event_bus.subscribe("task.failed", handler)

    task_store = TaskStore()
    broker = Broker(
        connectors=[LocalConnector()],
        dispatcher=LocalDispatcher(),
        event_bus=event_bus,
        task_store=task_store,
    )
    task_id = await broker.delegate(delegated_by="user", caps=["python"], instructions="do it")
    await broker.fail_task(task_id, reason="exploded")
    await asyncio.sleep(0)

    assert len(received) == 1
    assert received[0].payload["task"]["status"] == "failed"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/gateway/test_broker.py::test_broker_publishes_task_created_to_event_bus tests/gateway/test_broker.py::test_broker_publishes_task_completed_to_event_bus tests/gateway/test_broker.py::test_broker_publishes_task_failed_to_event_bus -v
```

Expected: all 3 FAIL (no events received).

- [ ] **Step 3: Add EventBus publishing to Broker**

In `harness_claw/gateway/broker.py`, update three methods:

**In `delegate()`, after the `asyncio.create_task(self._notify("task.created", task))` block:**

```python
        if self._event_bus is not None:
            try:
                asyncio.create_task(
                    self._event_bus.publish(
                        "task.created", {"task": task.to_dict()}, source="broker"
                    )
                )
            except RuntimeError:
                pass
```

**In `complete_task()`, after `asyncio.create_task(self._notify("task.completed", task))`:**

```python
        if self._event_bus is not None:
            try:
                asyncio.create_task(
                    self._event_bus.publish(
                        "task.completed", {"task": task.to_dict()}, source="broker"
                    )
                )
            except RuntimeError:
                pass
```

**In `fail_task()`, after `asyncio.create_task(self._notify("task.failed", task))`:**

```python
        if self._event_bus is not None:
            try:
                asyncio.create_task(
                    self._event_bus.publish(
                        "task.failed", {"task": task.to_dict()}, source="broker"
                    )
                )
            except RuntimeError:
                pass
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/gateway/test_broker.py -v
```

Expected: all broker tests pass (including the 3 new ones).

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/broker.py tests/gateway/test_broker.py
git commit -m "feat: publish broad task.* events to EventBus in Broker"
```

---

## Task 4: `RunManager`

**Files:**
- Create: `harness_claw/runtime/run_manager.py`
- Create: `tests/runtime/test_run_manager.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/runtime/test_run_manager.py
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from harness_claw.gateway.event_bus import LocalEventBus, Event
from harness_claw.runtime.run_manager import RunManager
from harness_claw.runtime.run_store import SqliteRunStore


def make_store(tmp_path: Path) -> SqliteRunStore:
    return SqliteRunStore(tmp_path / "runs.db")


async def make_manager(tmp_path: Path):
    store = make_store(tmp_path)
    event_bus = LocalEventBus()
    broadcast = AsyncMock()
    mgr = RunManager(store=store, event_bus=event_bus, broadcast=broadcast)
    await mgr.start()
    return mgr, store, event_bus, broadcast


@pytest.mark.asyncio
async def test_create_run_returns_run_id(tmp_path):
    mgr, store, _, broadcast = await make_manager(tmp_path)
    run_id = await mgr.create_run(
        agent_id="s1", trigger_type="manual", caller="user",
        goal="do stuff", input_payload={}, budget_usd=1.0,
        timeout_secs=60, permissions_snapshot={"scopes": [], "role": {}},
    )
    assert run_id is not None
    run = store.get(run_id)
    assert run is not None
    assert run.status == "running"
    assert run.agent_id == "s1"
    assert run.trigger_type == "manual"


@pytest.mark.asyncio
async def test_create_run_broadcasts_run_created(tmp_path):
    mgr, _, _, broadcast = await make_manager(tmp_path)
    await mgr.create_run(
        agent_id="s1", trigger_type="manual", caller="user",
        goal="go", input_payload={}, budget_usd=None,
        timeout_secs=None, permissions_snapshot={},
    )
    await asyncio.sleep(0)
    broadcast.assert_called_once()
    call_args = broadcast.call_args[0][0]
    assert call_args["type"] == "run.created"


@pytest.mark.asyncio
async def test_task_created_event_appends_task_id(tmp_path):
    mgr, store, event_bus, _ = await make_manager(tmp_path)
    run_id = await mgr.create_run(
        agent_id="s1", trigger_type="manual", caller="user",
        goal="go", input_payload={}, budget_usd=None,
        timeout_secs=None, permissions_snapshot={},
    )
    await event_bus.publish(
        "task.created",
        {"task": {"task_id": "t1", "delegated_to": "s1", "status": "running",
                  "progress_pct": 0, "provider_session_id": None, "pause_reason": None}},
        source="broker",
    )
    run = store.get(run_id)
    assert "t1" in run.task_ids


@pytest.mark.asyncio
async def test_task_created_event_ignored_for_unknown_agent(tmp_path):
    mgr, store, event_bus, _ = await make_manager(tmp_path)
    run_id = await mgr.create_run(
        agent_id="s1", trigger_type="manual", caller="user",
        goal="go", input_payload={}, budget_usd=None,
        timeout_secs=None, permissions_snapshot={},
    )
    await event_bus.publish(
        "task.created",
        {"task": {"task_id": "t1", "delegated_to": "s-OTHER", "status": "running",
                  "progress_pct": 0, "provider_session_id": None, "pause_reason": None}},
        source="broker",
    )
    run = store.get(run_id)
    assert run.task_ids == []


@pytest.mark.asyncio
async def test_task_updated_paused_sets_run_paused(tmp_path):
    mgr, store, event_bus, broadcast = await make_manager(tmp_path)
    run_id = await mgr.create_run(
        agent_id="s1", trigger_type="manual", caller="user",
        goal="go", input_payload={}, budget_usd=None,
        timeout_secs=None, permissions_snapshot={},
    )
    # First register task
    await event_bus.publish(
        "task.created",
        {"task": {"task_id": "t1", "delegated_to": "s1", "status": "running",
                  "progress_pct": 0, "provider_session_id": None, "pause_reason": None}},
        source="broker",
    )
    # Now pause it
    await event_bus.publish(
        "task.updated",
        {"task": {"task_id": "t1", "status": "paused", "progress_pct": 50,
                  "provider_session_id": "prov-123", "pause_reason": "cost exceeded"}},
        source="broker",
    )
    run = store.get(run_id)
    assert run.status == "paused"
    assert run.checkpoint["pause_reason"] == "cost exceeded"
    assert run.checkpoint["provider_session_id"] == "prov-123"


@pytest.mark.asyncio
async def test_task_failed_sets_run_failed(tmp_path):
    mgr, store, event_bus, broadcast = await make_manager(tmp_path)
    run_id = await mgr.create_run(
        agent_id="s1", trigger_type="manual", caller="user",
        goal="go", input_payload={}, budget_usd=None,
        timeout_secs=None, permissions_snapshot={},
    )
    await event_bus.publish(
        "task.created",
        {"task": {"task_id": "t1", "delegated_to": "s1", "status": "running",
                  "progress_pct": 0, "provider_session_id": None, "pause_reason": None}},
        source="broker",
    )
    await event_bus.publish(
        "task.failed",
        {"task": {"task_id": "t1", "status": "failed", "progress_pct": 20,
                  "provider_session_id": None, "pause_reason": None, "result": "boom"}},
        source="broker",
    )
    run = store.get(run_id)
    assert run.status == "failed"
    assert run.checkpoint["pause_reason"] == "boom"
    broadcast_types = [c[0][0]["type"] for c in broadcast.call_args_list]
    assert "run.failed" in broadcast_types


@pytest.mark.asyncio
async def test_on_session_ended_sets_run_completed(tmp_path):
    mgr, store, _, broadcast = await make_manager(tmp_path)
    run_id = await mgr.create_run(
        agent_id="s1", trigger_type="manual", caller="user",
        goal="go", input_payload={}, budget_usd=None,
        timeout_secs=None, permissions_snapshot={},
    )
    await mgr.on_session_ended("s1")
    await asyncio.sleep(0)
    run = store.get(run_id)
    assert run.status == "completed"
    broadcast_types = [c[0][0]["type"] for c in broadcast.call_args_list]
    assert "run.completed" in broadcast_types


@pytest.mark.asyncio
async def test_on_session_ended_does_not_overwrite_failed(tmp_path):
    mgr, store, event_bus, _ = await make_manager(tmp_path)
    run_id = await mgr.create_run(
        agent_id="s1", trigger_type="manual", caller="user",
        goal="go", input_payload={}, budget_usd=None,
        timeout_secs=None, permissions_snapshot={},
    )
    await event_bus.publish(
        "task.created",
        {"task": {"task_id": "t1", "delegated_to": "s1", "status": "running",
                  "progress_pct": 0, "provider_session_id": None, "pause_reason": None}},
        source="broker",
    )
    await event_bus.publish(
        "task.failed",
        {"task": {"task_id": "t1", "status": "failed", "progress_pct": 0,
                  "provider_session_id": None, "pause_reason": None, "result": "boom"}},
        source="broker",
    )
    await mgr.on_session_ended("s1")
    run = store.get(run_id)
    assert run.status == "failed"  # not overwritten to "completed"


@pytest.mark.asyncio
async def test_list_runs_by_agent(tmp_path):
    mgr, store, _, _ = await make_manager(tmp_path)
    await mgr.create_run(
        agent_id="s1", trigger_type="manual", caller="user",
        goal="g1", input_payload={}, budget_usd=None,
        timeout_secs=None, permissions_snapshot={},
    )
    await mgr.create_run(
        agent_id="s2", trigger_type="manual", caller="user",
        goal="g2", input_payload={}, budget_usd=None,
        timeout_secs=None, permissions_snapshot={},
    )
    runs = mgr.list_runs(agent_id="s1")
    assert len(runs) == 1
    assert runs[0].agent_id == "s1"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/runtime/test_run_manager.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'harness_claw.runtime.run_manager'`

- [ ] **Step 3: Write `run_manager.py`**

```python
# harness_claw/runtime/run_manager.py
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Awaitable
from datetime import datetime, timezone
from typing import Any

from harness_claw.gateway.event_bus import Event, EventBus
from harness_claw.runtime.run_store import Run, SqliteRunStore

Broadcast = Callable[[dict[str, Any]], Awaitable[None]]


class RunManager:
    """Creates Runs at execution entry points and keeps them current via EventBus."""

    def __init__(
        self,
        store: SqliteRunStore,
        event_bus: EventBus,
        broadcast: Broadcast,
    ) -> None:
        self._store = store
        self._event_bus = event_bus
        self._broadcast = broadcast
        self._agent_run_map: dict[str, str] = {}  # agent_id → run_id
        self._task_run_map: dict[str, str] = {}   # task_id → run_id

    async def start(self) -> None:
        """Subscribe to EventBus topics. Call once after construction."""
        await self._event_bus.subscribe("task.created", self._on_task_created)
        await self._event_bus.subscribe("task.updated", self._on_task_updated)
        await self._event_bus.subscribe("task.completed", self._on_task_completed)
        await self._event_bus.subscribe("task.failed", self._on_task_failed)

    async def create_run(
        self,
        agent_id: str,
        trigger_type: str,
        caller: str,
        goal: str,
        input_payload: dict,
        budget_usd: float | None,
        timeout_secs: int | None,
        permissions_snapshot: dict,
    ) -> str:
        """Create and persist a Run, record agent_id mapping, broadcast run.created."""
        now = datetime.now(timezone.utc).isoformat()
        run = Run(
            run_id=str(uuid.uuid4()),
            agent_id=agent_id,
            trigger_type=trigger_type,
            caller=caller,
            goal=goal,
            input_payload=input_payload,
            budget_usd=budget_usd,
            timeout_secs=timeout_secs,
            permissions_snapshot=permissions_snapshot,
            status="running",
            task_ids=[],
            checkpoint=None,
            created_at=now,
            updated_at=now,
        )
        self._store.save(run)
        self._agent_run_map[agent_id] = run.run_id
        try:
            asyncio.create_task(
                self._broadcast({"type": "run.created", "run": run.to_dict()})
            )
        except RuntimeError:
            pass
        return run.run_id

    async def on_session_ended(self, agent_id: str) -> None:
        """Called by JobRunner.kill_session(). Marks run completed unless already failed/paused."""
        run_id = self._agent_run_map.pop(agent_id, None)
        if run_id is None:
            return
        run = self._store.get(run_id)
        if run is None:
            return
        if run.status not in ("failed", "paused"):
            run.status = "completed"
            self._store.save(run)
            try:
                asyncio.create_task(
                    self._broadcast({"type": "run.completed", "run": run.to_dict()})
                )
            except RuntimeError:
                pass

    def get_run(self, run_id: str) -> Run | None:
        return self._store.get(run_id)

    def list_runs(
        self,
        agent_id: str | None = None,
        status: str | None = None,
    ) -> list[Run]:
        if agent_id is not None:
            return self._store.get_by_agent(agent_id)
        if status is not None:
            return self._store.get_by_status(status)
        return self._store.all()

    # --- EventBus handlers ---

    async def _on_task_created(self, event: Event) -> None:
        task = event.payload.get("task", {})
        task_id = task.get("task_id", "")
        agent_id = task.get("delegated_to", "")
        if not task_id or not agent_id:
            return
        run_id = self._agent_run_map.get(agent_id)
        if run_id is None:
            return
        run = self._store.get(run_id)
        if run is None:
            return
        if task_id not in run.task_ids:
            run.task_ids.append(task_id)
            self._task_run_map[task_id] = run_id
            self._store.save(run)

    async def _on_task_updated(self, event: Event) -> None:
        task = event.payload.get("task", {})
        task_id = task.get("task_id", "")
        status = task.get("status", "")
        run_id = self._task_run_map.get(task_id)
        if run_id is None:
            return
        run = self._store.get(run_id)
        if run is None:
            return
        if status == "paused":
            run.status = "paused"
            run.checkpoint = {
                "provider_session_id": task.get("provider_session_id"),
                "last_task_id": task_id,
                "progress_pct": task.get("progress_pct", 0),
                "pause_reason": task.get("pause_reason"),
            }
            self._store.save(run)
            try:
                asyncio.create_task(
                    self._broadcast({"type": "run.updated", "run": run.to_dict()})
                )
            except RuntimeError:
                pass

    async def _on_task_completed(self, event: Event) -> None:
        task = event.payload.get("task", {})
        task_id = task.get("task_id", "")
        run_id = self._task_run_map.get(task_id)
        if run_id is None:
            return
        run = self._store.get(run_id)
        if run is None:
            return
        run.checkpoint = {
            "provider_session_id": task.get("provider_session_id"),
            "last_task_id": task_id,
            "progress_pct": task.get("progress_pct", 100),
            "pause_reason": None,
        }
        self._store.save(run)

    async def _on_task_failed(self, event: Event) -> None:
        task = event.payload.get("task", {})
        task_id = task.get("task_id", "")
        run_id = self._task_run_map.get(task_id)
        if run_id is None:
            return
        run = self._store.get(run_id)
        if run is None:
            return
        run.status = "failed"
        run.checkpoint = {
            "provider_session_id": task.get("provider_session_id"),
            "last_task_id": task_id,
            "progress_pct": task.get("progress_pct", 0),
            "pause_reason": task.get("result"),
        }
        self._store.save(run)
        try:
            asyncio.create_task(
                self._broadcast({"type": "run.failed", "run": run.to_dict()})
            )
        except RuntimeError:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/runtime/test_run_manager.py -v
```

Expected: all 10 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/runtime/run_manager.py tests/runtime/test_run_manager.py
git commit -m "feat: add RunManager with EventBus subscriptions and broadcast"
```

---

## Task 5: Wire `RunManager` into `JobRunner`

**Files:**
- Modify: `harness_claw/runtime/job_runner.py`
- Modify: `tests/runtime/test_job_runner.py`

`JobRunner` gets an optional `run_manager` param. `start_session()` calls `create_run()`. `kill_session()` calls `on_session_ended()`. A `trigger_type` param is added to `start_session()` so startup recovery can pass `"recovery"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/runtime/test_job_runner.py`:

```python
@pytest.mark.asyncio
async def test_start_session_calls_create_run():
    from unittest.mock import AsyncMock, MagicMock, patch
    from harness_claw.runtime.run_manager import RunManager

    run_manager = MagicMock(spec=RunManager)
    run_manager.create_run = AsyncMock(return_value="run-abc")

    runner, _, store = make_runner()
    runner._run_manager = run_manager

    session = make_session(session_id="s1")
    store.get.return_value = session

    with patch("harness_claw.runtime.job_runner.PtySession") as MockPty:
        mock_pty = MagicMock()
        mock_pty.start = AsyncMock()
        MockPty.return_value = mock_pty
        with patch("harness_claw.runtime.job_runner.CostPoller"):
            await runner.start_session(session)

    run_manager.create_run.assert_called_once()
    call_kwargs = run_manager.create_run.call_args[1]
    assert call_kwargs["agent_id"] == "s1"
    assert call_kwargs["trigger_type"] == "manual"
    assert call_kwargs["caller"] == "user"


@pytest.mark.asyncio
async def test_kill_session_calls_on_session_ended():
    from unittest.mock import AsyncMock, MagicMock, patch
    from harness_claw.runtime.run_manager import RunManager

    run_manager = MagicMock(spec=RunManager)
    run_manager.create_run = AsyncMock(return_value="run-abc")
    run_manager.on_session_ended = AsyncMock()

    runner, _, store = make_runner()
    runner._run_manager = run_manager

    session = make_session(session_id="s1")
    store.get.return_value = session

    with patch("harness_claw.runtime.job_runner.PtySession") as MockPty:
        mock_pty = MagicMock()
        mock_pty.start = AsyncMock()
        mock_pty.isalive = MagicMock(return_value=False)
        MockPty.return_value = mock_pty
        with patch("harness_claw.runtime.job_runner.CostPoller"):
            await runner.start_session(session)

    runner.kill_session("s1")
    await asyncio.sleep(0)
    run_manager.on_session_ended.assert_called_once_with("s1")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/runtime/test_job_runner.py::test_start_session_calls_create_run tests/runtime/test_job_runner.py::test_kill_session_calls_on_session_ended -v
```

Expected: both FAIL (`AttributeError` or assertion errors).

- [ ] **Step 3: Modify `JobRunner.__init__()` to accept `run_manager`**

In `harness_claw/runtime/job_runner.py`, update the `__init__` signature and body:

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
        tool_registry: ToolRegistry | None = None,
        run_manager: Any | None = None,
    ) -> None:
        # ... all existing assignments ...
        self._run_manager = run_manager
```

- [ ] **Step 4: Call `create_run()` in `start_session()`**

Add `trigger_type: str = "manual"` parameter to `start_session()`:

```python
    async def start_session(self, session: Session, trigger_type: str = "manual") -> None:
```

At the end of `start_session()`, just before the final cost-poller/enforcer block, add:

```python
        # Create Run record (agent sessions only)
        if not is_terminal and self._run_manager is not None:
            permissions_snapshot: dict = {}
            if self._token_store is not None:
                permissions_snapshot = {
                    "scopes": role.scopes,
                    "role": role.to_dict(),
                }
            try:
                asyncio.create_task(self._run_manager.create_run(
                    agent_id=session_id,
                    trigger_type=trigger_type,
                    caller="user",
                    goal=role.system_prompt,
                    input_payload={},
                    budget_usd=role.max_cost,
                    timeout_secs=role.max_time,
                    permissions_snapshot=permissions_snapshot,
                ))
            except RuntimeError:
                pass
```

- [ ] **Step 5: Call `on_session_ended()` in `kill_session()`**

In `kill_session()`, at the very end (after `self._obs_store.remove_session(session_id)`), add:

```python
        # Notify RunManager that this agent's session has ended
        if self._run_manager is not None:
            try:
                asyncio.create_task(self._run_manager.on_session_ended(session_id))
            except RuntimeError:
                pass
```

- [ ] **Step 6: Update startup in `server.py` to pass `trigger_type="recovery"`**

In `harness_claw/server.py`, find the startup loop:

```python
    for session in store.all():
        if session.status != "killed":
            await runner.start_session(session)
```

Change to:

```python
    for session in store.all():
        if session.status != "killed":
            await runner.start_session(session, trigger_type="recovery")
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
python -m pytest tests/runtime/test_job_runner.py -v
```

Expected: all job_runner tests pass including the 2 new ones.

- [ ] **Step 8: Commit**

```bash
git add harness_claw/runtime/job_runner.py harness_claw/server.py tests/runtime/test_job_runner.py
git commit -m "feat: wire RunManager into JobRunner start_session and kill_session"
```

---

## Task 6: Wire `RunManager` into `Broker.delegate()`

**Files:**
- Modify: `harness_claw/gateway/broker.py`
- Modify: `tests/gateway/test_broker.py`

`Broker.delegate()` calls `run_manager.create_run()` when a task is immediately dispatched to an agent (`delegated_to != ""`). Queued tasks (no available agent) are skipped for now — the Run will be created when the scheduler dispatches them in a future enhancement.

- [ ] **Step 1: Write the failing test**

Add to `tests/gateway/test_broker.py`:

```python
@pytest.mark.asyncio
async def test_broker_delegate_calls_create_run_when_dispatched():
    from unittest.mock import AsyncMock, MagicMock
    from harness_claw.gateway.broker import Broker, LocalDispatcher
    from harness_claw.gateway.capability import LocalConnector, AgentAdvertisement
    from harness_claw.gateway.task_store import TaskStore
    from harness_claw.runtime.run_manager import RunManager

    run_manager = MagicMock(spec=RunManager)
    run_manager.create_run = AsyncMock(return_value="run-xyz")

    connector = LocalConnector()
    await connector.register(AgentAdvertisement(
        session_id="s1", role_id="writer", caps=["python"],
        status="idle", task_count=0, connector="local",
    ))

    dispatcher = LocalDispatcher()
    dispatcher.register_writer("s1", lambda data: None)

    task_store = TaskStore()
    broker = Broker(
        connectors=[connector],
        dispatcher=dispatcher,
        task_store=task_store,
        run_manager=run_manager,
    )

    await broker.delegate(
        delegated_by="user", caps=["python"],
        instructions="write the thing", context={"repo": "myrepo"},
        max_cost=0.5, max_time=120,
    )
    await asyncio.sleep(0)

    run_manager.create_run.assert_called_once()
    kwargs = run_manager.create_run.call_args[1]
    assert kwargs["agent_id"] == "s1"
    assert kwargs["trigger_type"] == "delegate"
    assert kwargs["caller"] == "user"
    assert kwargs["goal"] == "write the thing"
    assert kwargs["input_payload"] == {"repo": "myrepo"}
    assert kwargs["budget_usd"] == 0.5
    assert kwargs["timeout_secs"] == 120


@pytest.mark.asyncio
async def test_broker_delegate_skips_create_run_when_queued():
    from unittest.mock import AsyncMock, MagicMock
    from harness_claw.gateway.broker import Broker, LocalDispatcher
    from harness_claw.gateway.capability import LocalConnector
    from harness_claw.runtime.run_manager import RunManager

    run_manager = MagicMock(spec=RunManager)
    run_manager.create_run = AsyncMock(return_value="run-xyz")

    broker = Broker(
        connectors=[LocalConnector()],  # no agents registered
        dispatcher=LocalDispatcher(),
        run_manager=run_manager,
    )
    await broker.delegate(delegated_by="user", caps=["python"], instructions="do it")
    await asyncio.sleep(0)

    run_manager.create_run.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/gateway/test_broker.py::test_broker_delegate_calls_create_run_when_dispatched tests/gateway/test_broker.py::test_broker_delegate_skips_create_run_when_queued -v
```

Expected: both FAIL (`TypeError: __init__() got unexpected keyword argument 'run_manager'`).

- [ ] **Step 3: Add `run_manager` param to `Broker.__init__()`**

In `harness_claw/gateway/broker.py`, update `Broker.__init__()`:

```python
    def __init__(
        self,
        connectors: list[CapabilityConnector],
        dispatcher: TaskDispatcher,
        event_bus: EventBus | None = None,
        task_store: TaskStoreProtocol | None = None,
        run_manager: Any | None = None,
    ) -> None:
        # ... existing assignments ...
        self._run_manager = run_manager
```

- [ ] **Step 4: Call `create_run()` in `Broker.delegate()`**

In `delegate()`, after the `if candidates:` block dispatches the task (i.e., right after `await self._dispatcher.dispatch(task, agent)`), add:

```python
            if self._run_manager is not None:
                try:
                    asyncio.create_task(self._run_manager.create_run(
                        agent_id=agent.session_id,
                        trigger_type="delegate",
                        caller=delegated_by,
                        goal=instructions,
                        input_payload=context or {},
                        budget_usd=max_cost,
                        timeout_secs=max_time,
                        permissions_snapshot={},
                    ))
                except RuntimeError:
                    pass
```

This block goes inside the `if candidates:` branch, after `self._store.save(task)` and `await self._dispatcher.dispatch(task, agent)`.

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/gateway/test_broker.py -v
```

Expected: all broker tests pass including the 2 new ones.

- [ ] **Step 6: Commit**

```bash
git add harness_claw/gateway/broker.py tests/gateway/test_broker.py
git commit -m "feat: wire RunManager into Broker.delegate()"
```

---

## Task 7: Server wiring and REST API

**Files:**
- Modify: `harness_claw/server.py`
- Modify: `tests/test_server_endpoints.py`

Instantiate `SqliteRunStore` and `RunManager` in `server.py`. Pass `run_manager` to `JobRunner` and `Broker`. Call `run_manager.start()` in startup. Add REST endpoints.

- [ ] **Step 1: Write the failing tests**

Open `tests/test_server_endpoints.py` and add:

```python
def test_list_runs_endpoint(client):
    response = client.get("/api/runs")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_get_run_not_found(client):
    response = client.get("/api/runs/nonexistent-id")
    assert response.status_code == 404


def test_list_runs_filter_by_status(client):
    response = client.get("/api/runs?status=running")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_list_runs_filter_by_agent(client):
    response = client.get("/api/runs?agent_id=some-agent")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_server_endpoints.py::test_list_runs_endpoint tests/test_server_endpoints.py::test_get_run_not_found -v
```

Expected: both FAIL with 404 (routes don't exist yet).

- [ ] **Step 3: Add `_runs_db` path and instantiate `SqliteRunStore` + `RunManager` in `server.py`**

After the existing db path declarations (around line 45), add:

```python
_runs_db = _root / "runs.db"
```

At the top of `server.py`, add these imports alongside the existing runtime imports:

```python
from harness_claw.runtime.run_store import SqliteRunStore
from harness_claw.runtime.run_manager import RunManager
```

After the `obs_provider = ClaudeCodeProvider(...)` line, add a lazy broadcast function and the store/manager. The function references `runner` by name at call time (after `runner` is defined further down), avoiding circular dependency:

```python
async def _run_broadcast(msg: dict) -> None:
    await runner._broadcast(msg)

run_store = SqliteRunStore(_runs_db)
run_manager = RunManager(
    store=run_store,
    event_bus=event_bus,
    broadcast=_run_broadcast,
)
```

- [ ] **Step 4: Pass `run_manager` to `Broker` and `JobRunner`**

Update the `broker = Broker(...)` instantiation (which comes before `runner`) to add `run_manager=run_manager`:

```python
broker = Broker(
    connectors=[connector, gateway_connector],
    dispatcher=dispatcher,
    event_bus=event_bus,
    task_store=task_store,
    run_manager=run_manager,
)
```

Update the `runner = JobRunner(...)` instantiation to add `run_manager=run_manager`:

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
    tool_registry=tool_registry,
    run_manager=run_manager,
)
```

- [ ] **Step 5: Call `run_manager.start()` in the startup handler**

In the `startup()` async function, add near the top (after `task_store.expire(...)` but before the session recovery loop):

```python
    await run_manager.start()
```

- [ ] **Step 6: Add REST endpoints**

After the existing `/api/tasks` endpoint block (around line 268), add:

```python
@app.get("/api/runs")
def list_runs(agent_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    return [r.to_dict() for r in run_manager.list_runs(agent_id=agent_id, status=status)]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = run_manager.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run.to_dict()
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
python -m pytest tests/test_server_endpoints.py -v
```

Expected: all server endpoint tests pass including the 4 new run endpoint tests.

- [ ] **Step 8: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all tests pass.

- [ ] **Step 9: Commit**

```bash
git add harness_claw/server.py tests/test_server_endpoints.py
git commit -m "feat: wire RunManager into server, add /api/runs REST endpoints"
```
