# Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a priority-aware, SQLite-durable task scheduler to the broker so tasks queue when no agent is available and are dispatched automatically when one becomes free.

**Architecture:** A `Scheduler` class lives in `gateway/broker.py` and is owned by `Broker`. It holds an in-memory `heapq` for fast dispatch and writes to SQLite as the source of truth. On server restart, interrupted tasks (queued + running) are recovered from SQLite and re-enqueued; running tasks get a resume preamble. `complete_task`/`fail_task` trigger an event-driven drain, and a background poll loop drains every 30 seconds as a safety net.

**Tech Stack:** Python 3.12, asyncio, heapq, SQLite (via existing `SqliteTaskStore`), React/TypeScript (minor UI change)

---

## File Map

| File | Change |
|------|--------|
| `harness_claw/gateway/task_store.py` | Add `priority`/`resume` fields to `Task`, SQLite migration, `get_interrupted()`, `mark_interrupted_as_queued()` |
| `harness_claw/gateway/broker.py` | Add `Scheduler` class, update `Broker.__init__`, `delegate`, `complete_task`, `fail_task` |
| `harness_claw/gateway/mcp_server.py` | Add `priority` param to `agent_delegate` |
| `harness_claw/server.py` | Update startup: recover interrupted tasks, start poll loop, initial drain; update MCP tool schema |
| `ui/src/types.ts` | Add `priority` and `resume` to `TaskRecord` |
| `ui/src/components/TasksTab.tsx` | Add Priority badge to task cards |
| `tests/gateway/test_task_store.py` | Tests for new fields, migration, `get_interrupted`, `mark_interrupted_as_queued` |
| `tests/gateway/test_broker.py` | Tests for queue behavior, priority ordering, restart recovery, event-driven drain |

---

## Task 1: Extend Task dataclass and SqliteTaskStore

**Files:**
- Modify: `harness_claw/gateway/task_store.py`
- Test: `tests/gateway/test_task_store.py`

- [ ] **Step 1: Write failing tests for new Task fields**

Add to `tests/gateway/test_task_store.py`:

```python
def test_task_priority_default():
    task = make_task()
    assert task.priority == 2
    assert task.resume is False


def test_task_priority_in_to_dict():
    task = make_task(priority=1, resume=True)
    d = task.to_dict()
    assert d["priority"] == 1
    assert d["resume"] is True


def test_sqlite_roundtrip_priority_and_resume(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    task = make_task(task_id="p1", priority=1, resume=True)
    store.save(task)
    loaded = store.get("p1")
    assert loaded.priority == 1
    assert loaded.resume is True


def test_get_interrupted_returns_queued_and_running(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    store.save(make_task(task_id="q1", status="queued"))
    store.save(make_task(task_id="r1", status="running"))
    store.save(make_task(task_id="c1", status="completed"))
    store.save(make_task(task_id="f1", status="failed"))
    results = store.get_interrupted()
    ids = {t.task_id for t in results}
    assert ids == {"q1", "r1"}


def test_mark_interrupted_as_queued(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    store.save(make_task(task_id="q1", status="queued"))
    store.save(make_task(task_id="r1", status="running"))
    store.save(make_task(task_id="c1", status="completed"))
    count = store.mark_interrupted_as_queued()
    assert count == 2
    assert store.get("q1").status == "queued"
    assert store.get("r1").status == "queued"
    assert store.get("c1").status == "completed"  # unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/juichanglu/src/HarnessClaw
.venv/bin/pytest tests/gateway/test_task_store.py::test_task_priority_default tests/gateway/test_task_store.py::test_sqlite_roundtrip_priority_and_resume tests/gateway/test_task_store.py::test_get_interrupted_returns_queued_and_running -v
```

Expected: FAIL — `Task.__init__()` got unexpected keyword argument `priority`

- [ ] **Step 3: Add `priority` and `resume` to Task dataclass**

In `harness_claw/gateway/task_store.py`, update the `Task` dataclass:

```python
@dataclass
class Task:
    task_id: str
    delegated_by: str
    delegated_to: str
    instructions: str
    caps_requested: list[str]
    context: dict[str, Any] | None = None
    status: str = "queued"
    progress_pct: int = 0
    progress_msg: str = ""
    result: dict[str, Any] | str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    callback: bool = False
    priority: int = 2
    resume: bool = False

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
        }
```

- [ ] **Step 4: Add SQLite migration, update `_CREATE_TABLE`, `_row_to_task`, and new store methods**

Replace the `_CREATE_TABLE` constant and `_row_to_task` function, and add new methods to `SqliteTaskStore`:

```python
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
    updated_at     TEXT NOT NULL,
    priority       INTEGER NOT NULL DEFAULT 2,
    resume         INTEGER NOT NULL DEFAULT 0
)
"""

_MIGRATE_SQL = [
    "ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 2",
    "ALTER TABLE tasks ADD COLUMN resume   INTEGER NOT NULL DEFAULT 0",
]
```

Update `_row_to_task`:

```python
def _row_to_task(row: sqlite3.Row) -> Task:
    result_raw = row["result"]
    if result_raw is None:
        result: dict[str, Any] | str | None = None
    else:
        try:
            result = json.loads(result_raw)
        except (json.JSONDecodeError, TypeError):
            result = result_raw

    keys = row.keys()
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
        priority=row["priority"] if "priority" in keys else 2,
        resume=bool(row["resume"]) if "resume" in keys else False,
    )
```

Update `SqliteTaskStore.__init__` to run migrations:

```python
def __init__(self, path: Path) -> None:
    self._path = str(path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with self._connect() as conn:
        conn.execute(_CREATE_TABLE)
        for sql in _MIGRATE_SQL:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # column already exists
```

Add new methods to `SqliteTaskStore`:

```python
def get_interrupted(self) -> list[Task]:
    """Return all tasks with status in ('queued', 'running')."""
    with self._connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status IN ('queued', 'running') ORDER BY priority ASC, created_at ASC"
        ).fetchall()
    return [_row_to_task(r) for r in rows]

def mark_interrupted_as_queued(self) -> int:
    """Set status='queued' for all interrupted tasks. Returns count updated."""
    now = datetime.now(timezone.utc).isoformat()
    with self._connect() as conn:
        cursor = conn.execute(
            "UPDATE tasks SET status = 'queued', updated_at = ? WHERE status IN ('queued', 'running')",
            (now,),
        )
    return cursor.rowcount
```

Also update the `save` method to persist `priority` and `resume`:

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
                 created_at, updated_at, priority, resume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                status       = excluded.status,
                progress_pct = excluded.progress_pct,
                progress_msg = excluded.progress_msg,
                result       = excluded.result,
                callback     = excluded.callback,
                updated_at   = excluded.updated_at,
                priority     = excluded.priority,
                resume       = excluded.resume
            """,
            (
                task.task_id, task.delegated_by, task.delegated_to, task.instructions,
                json.dumps(task.caps_requested),
                json.dumps(task.context) if task.context is not None else None,
                task.status, task.progress_pct, task.progress_msg,
                result_json, int(task.callback),
                task.created_at, task.updated_at,
                task.priority, int(task.resume),
            ),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/gateway/test_task_store.py -v
```

Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add harness_claw/gateway/task_store.py tests/gateway/test_task_store.py
git commit -m "feat: add priority/resume fields to Task and SqliteTaskStore recovery methods"
```

---

## Task 2: Implement Scheduler class

**Files:**
- Modify: `harness_claw/gateway/broker.py`
- Test: `tests/gateway/test_broker.py`

- [ ] **Step 1: Write failing tests for Scheduler**

Add to `tests/gateway/test_broker.py`:

```python
import heapq
from harness_claw.gateway.broker import Broker, Scheduler, LocalDispatcher
from harness_claw.gateway.task_store import Task, TaskStore


def make_task(task_id: str, caps: list[str], priority: int = 2, status: str = "queued") -> Task:
    return Task(
        task_id=task_id,
        delegated_by="orch",
        delegated_to="",
        instructions=f"do task {task_id}",
        caps_requested=caps,
        priority=priority,
        status=status,
    )


async def test_scheduler_push_adds_to_queue():
    store = TaskStore()
    scheduler = Scheduler(connectors=[], dispatcher=AsyncMock(), store=store)
    task = make_task("t1", ["python"])
    scheduler.push(task)
    assert len(scheduler._queue) == 1
    assert store.get("t1").status == "queued"


async def test_scheduler_drain_dispatches_matching_task():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    store = TaskStore()
    scheduler = Scheduler(connectors=[conn], dispatcher=dispatcher, store=store)

    task = make_task("t1", ["python"])
    scheduler.push(task)
    await scheduler.drain()

    assert len(scheduler._queue) == 0
    assert store.get("t1").status == "running"
    dispatcher.dispatch.assert_called_once()


async def test_scheduler_drain_respects_priority():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    store = TaskStore()
    scheduler = Scheduler(connectors=[conn], dispatcher=dispatcher, store=store)

    low = make_task("low", ["python"], priority=3)
    high = make_task("high", ["python"], priority=1)
    normal = make_task("normal", ["python"], priority=2)

    scheduler.push(low)
    scheduler.push(high)
    scheduler.push(normal)

    # Only one agent — drain dispatches highest priority first
    await scheduler.drain()

    dispatched_task = dispatcher.dispatch.call_args[0][0]
    assert dispatched_task.task_id == "high"
    assert len(scheduler._queue) == 2  # low and normal still queued


async def test_scheduler_recover_re_enqueues_queued_task():
    store = TaskStore()
    scheduler = Scheduler(connectors=[], dispatcher=AsyncMock(), store=store)
    task = make_task("t1", ["python"], status="queued")
    scheduler.recover([task])
    assert len(scheduler._queue) == 1
    assert scheduler._tasks["t1"].resume is False


async def test_scheduler_recover_sets_resume_on_running_task():
    store = TaskStore()
    scheduler = Scheduler(connectors=[], dispatcher=AsyncMock(), store=store)
    task = make_task("t1", ["python"], status="running")
    scheduler.recover([task])
    assert scheduler._tasks["t1"].resume is True


async def test_scheduler_drain_prepends_resume_preamble():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    store = TaskStore()
    scheduler = Scheduler(connectors=[conn], dispatcher=dispatcher, store=store)

    task = make_task("t1", ["python"], status="running")
    task.resume = True
    scheduler.recover([task])
    await scheduler.drain()

    dispatched_task = dispatcher.dispatch.call_args[0][0]
    assert "[RESUME]" in dispatched_task.instructions
    assert "do task t1" in dispatched_task.instructions


async def test_scheduler_drain_skips_task_when_no_agent():
    conn = LocalConnector()  # no agents registered
    dispatcher = AsyncMock()
    store = TaskStore()
    scheduler = Scheduler(connectors=[conn], dispatcher=dispatcher, store=store)

    task = make_task("t1", ["python"])
    scheduler.push(task)
    await scheduler.drain()

    assert len(scheduler._queue) == 1  # still queued
    assert store.get("t1").status == "queued"
    dispatcher.dispatch.assert_not_called()


async def test_scheduler_drain_retries_on_dispatch_failure():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    dispatcher.dispatch.side_effect = RuntimeError("writer not registered")
    store = TaskStore()
    scheduler = Scheduler(connectors=[conn], dispatcher=dispatcher, store=store)

    task = make_task("t1", ["python"])
    scheduler.push(task)
    await scheduler.drain()

    # Task should still be in queue (dispatch failed)
    assert len(scheduler._queue) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/gateway/test_broker.py::test_scheduler_push_adds_to_queue tests/gateway/test_broker.py::test_scheduler_drain_dispatches_matching_task -v
```

Expected: FAIL — `cannot import name 'Scheduler' from 'harness_claw.gateway.broker'`

- [ ] **Step 3: Implement the Scheduler class in broker.py**

Add these imports at the top of `harness_claw/gateway/broker.py`:

```python
import heapq
from typing import Any, Callable, Protocol
```

Add the `Scheduler` class before the `Broker` class:

```python
class Scheduler:
    """Priority queue that holds tasks waiting for an available agent.

    In-memory heapq ordered by (priority, created_at, task_id).
    SQLite is the source of truth — push() persists before enqueuing.
    """

    def __init__(
        self,
        connectors: list[CapabilityConnector],
        dispatcher: TaskDispatcher,
        store: TaskStoreProtocol,
        notify_fn: Callable | None = None,
        poll_interval: int = 30,
    ) -> None:
        self._connectors = connectors
        self._dispatcher = dispatcher
        self._store = store
        self._notify_fn = notify_fn
        self._poll_interval = poll_interval
        self._queue: list[tuple[int, str, str]] = []   # (priority, created_at, task_id)
        self._tasks: dict[str, Task] = {}               # task_id → Task
        self._poll_task: asyncio.Task | None = None

    def push(self, task: Task) -> None:
        """Persist task as queued and enqueue it."""
        task.status = "queued"
        self._store.save(task)
        heapq.heappush(self._queue, (task.priority, task.created_at, task.task_id))
        self._tasks[task.task_id] = task

    def recover(self, tasks: list[Task]) -> None:
        """Re-enqueue interrupted tasks on startup.
        Tasks that were running get resume=True."""
        for task in tasks:
            if task.status == "running":
                task.resume = True
            heapq.heappush(self._queue, (task.priority, task.created_at, task.task_id))
            self._tasks[task.task_id] = task

    async def drain(self) -> None:
        """Dispatch queued tasks to available agents, in priority order.
        Continues past tasks whose cap set has no available agent."""
        pending = sorted(self._queue)
        dispatched: set[str] = set()

        for _, _, task_id in pending:
            task = self._tasks.get(task_id)
            if task is None:
                dispatched.add(task_id)
                continue

            candidates: list[AgentAdvertisement] = []
            for connector in self._connectors:
                candidates.extend(await connector.query(task.caps_requested))

            if not candidates:
                continue

            agent = candidates[0]

            instructions = task.instructions
            if task.resume:
                instructions = (
                    "[RESUME] You were previously working on this task. "
                    "Your conversation history is intact — continue where you left off.\n\n"
                    + instructions
                )

            dispatch_task = Task(
                task_id=task.task_id,
                delegated_by=task.delegated_by,
                delegated_to=agent.session_id,
                instructions=instructions,
                caps_requested=task.caps_requested,
                context=task.context,
                status="running",
                priority=task.priority,
                resume=task.resume,
                callback=task.callback,
                created_at=task.created_at,
            )

            try:
                await self._dispatcher.dispatch(dispatch_task, agent)
                task.status = "running"
                task.delegated_to = agent.session_id
                self._store.save(task)
                dispatched.add(task_id)
                del self._tasks[task_id]
                if self._notify_fn is not None:
                    try:
                        asyncio.create_task(self._notify_fn("task.updated", task))
                    except RuntimeError:
                        pass
            except Exception:
                pass  # dispatch failed — leave in queue for next drain

        if dispatched:
            self._queue = [
                (p, c, tid) for p, c, tid in self._queue if tid not in dispatched
            ]
            heapq.heapify(self._queue)

    async def start_poll_loop(self) -> None:
        """Start background asyncio task that calls drain() every poll_interval seconds."""
        async def _loop() -> None:
            while True:
                await asyncio.sleep(self._poll_interval)
                await self.drain()

        self._poll_task = asyncio.create_task(_loop())

    async def stop(self) -> None:
        """Cancel the background poll loop."""
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/gateway/test_broker.py::test_scheduler_push_adds_to_queue tests/gateway/test_broker.py::test_scheduler_drain_dispatches_matching_task tests/gateway/test_broker.py::test_scheduler_drain_respects_priority tests/gateway/test_broker.py::test_scheduler_recover_re_enqueues_queued_task tests/gateway/test_broker.py::test_scheduler_recover_sets_resume_on_running_task tests/gateway/test_broker.py::test_scheduler_drain_prepends_resume_preamble tests/gateway/test_broker.py::test_scheduler_drain_skips_task_when_no_agent tests/gateway/test_broker.py::test_scheduler_drain_retries_on_dispatch_failure -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/broker.py tests/gateway/test_broker.py
git commit -m "feat: add Scheduler class with priority queue, drain, and restart recovery"
```

---

## Task 3: Wire Scheduler into Broker

**Files:**
- Modify: `harness_claw/gateway/broker.py`
- Test: `tests/gateway/test_broker.py`

- [ ] **Step 1: Write failing tests for new Broker behavior**

Add to `tests/gateway/test_broker.py`:

```python
async def test_delegate_queues_when_no_agent_available():
    conn = LocalConnector()  # no agents
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    task_id = await broker.delegate(
        delegated_by="orch-1",
        caps=["python"],
        instructions="do it",
    )
    task = broker.get_task(task_id)
    assert task is not None
    assert task.status == "queued"
    dispatcher.dispatch.assert_not_called()


async def test_delegate_no_longer_raises_on_no_agent():
    conn = LocalConnector()
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    # Should not raise
    task_id = await broker.delegate("orch-1", caps=["nonexistent-cap"], instructions="do it")
    assert task_id is not None


async def test_delegate_accepts_priority_param():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    task_id = await broker.delegate("orch-1", ["python"], "do it", priority=1)
    task = broker.get_task(task_id)
    assert task.priority == 1


async def test_complete_task_triggers_drain():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    # Queue a task while agent is busy
    # First delegate fills the agent
    task_id_1 = await broker.delegate("orch-1", ["python"], "first task")
    assert broker.get_task(task_id_1).status == "running"

    # Second delegate queues (agent already running task_id_1 — task_count=1 but connector still returns it)
    # To properly test drain trigger, we unregister agent, queue a task, re-register, then complete
    conn2 = LocalConnector()  # fresh connector with no agents
    broker2 = Broker(connectors=[conn2], dispatcher=AsyncMock())
    queued_id = await broker2.delegate("orch-1", ["python"], "queued task")
    assert broker2.get_task(queued_id).status == "queued"

    # Now register an agent and complete a dummy task to trigger drain
    await conn2.register(make_agent("s2", ["python"]))
    dummy_task = Task(
        task_id="dummy", delegated_by="orch", delegated_to="s2",
        instructions="dummy", caps_requested=["python"], status="running",
    )
    broker2._store.save(dummy_task)
    await broker2.complete_task("dummy", result="done")

    # drain should have dispatched the queued task
    assert broker2.get_task(queued_id).status == "running"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/gateway/test_broker.py::test_delegate_queues_when_no_agent_available tests/gateway/test_broker.py::test_delegate_no_longer_raises_on_no_agent -v
```

Expected: FAIL — `ValueError: no agent found matching caps`

- [ ] **Step 3: Update Broker.__init__ to create a Scheduler**

In `harness_claw/gateway/broker.py`, update `Broker.__init__`:

```python
class Broker:
    def __init__(
        self,
        connectors: list[CapabilityConnector],
        dispatcher: TaskDispatcher,
        event_bus: EventBus | None = None,
        task_store: TaskStoreProtocol | None = None,
    ) -> None:
        self._connectors = connectors
        self._dispatcher = dispatcher
        self._event_bus = event_bus
        self._store = task_store or TaskStore()
        self._listeners: list[Any] = []
        self._callback_handlers: dict[str, Any] = {}
        self._callback_subs: dict[str, list[Any]] = {}
        self.scheduler = Scheduler(
            connectors=connectors,
            dispatcher=dispatcher,
            store=self._store,
            notify_fn=self._notify,
        )
```

- [ ] **Step 4: Update Broker.delegate to queue instead of raise**

Replace the `delegate` method:

```python
async def delegate(
    self,
    delegated_by: str,
    caps: list[str],
    instructions: str,
    context: dict[str, Any] | None = None,
    callback: bool = False,
    priority: int = 2,
) -> str:
    candidates: list[AgentAdvertisement] = []
    for connector in self._connectors:
        candidates.extend(await connector.query(caps))

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
    )

    if candidates:
        agent = candidates[0]
        task.delegated_to = agent.session_id
        task.status = "running"
        self._store.save(task)
        await self._dispatcher.dispatch(task, agent)
    else:
        self.scheduler.push(task)

    if callback and self._event_bus is not None:
        handler = self._callback_handlers.get(delegated_by)
        if handler is not None:
            subs = []
            sub_ok = await self._event_bus.subscribe(f"task:{task.task_id}:completed", handler)
            subs.append(sub_ok)
            sub_fail = await self._event_bus.subscribe(f"task:{task.task_id}:failed", handler)
            subs.append(sub_fail)
            self._callback_subs[task.task_id] = subs

    try:
        asyncio.create_task(self._notify("task.created", task))
    except RuntimeError:
        pass
    return task.task_id
```

- [ ] **Step 5: Add drain trigger to complete_task and fail_task**

In `complete_task`, add after `self._store.save(task)`:

```python
        try:
            asyncio.create_task(self.scheduler.drain())
        except RuntimeError:
            pass
```

In `fail_task`, add after `self._store.save(task)`:

```python
        try:
            asyncio.create_task(self.scheduler.drain())
        except RuntimeError:
            pass
```

- [ ] **Step 6: Update the existing test that expected ValueError**

In `tests/gateway/test_broker.py`, replace `test_delegate_raises_when_no_agent_matches`:

```python
async def test_delegate_queues_when_no_agent_matches():
    conn = LocalConnector()
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    task_id = await broker.delegate("orch-1", caps=["nonexistent-cap"], instructions="do it")
    task = broker.get_task(task_id)
    assert task.status == "queued"
    dispatcher.dispatch.assert_not_called()
```

- [ ] **Step 7: Run all broker tests**

```bash
.venv/bin/pytest tests/gateway/test_broker.py -v
```

Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add harness_claw/gateway/broker.py tests/gateway/test_broker.py
git commit -m "feat: wire Scheduler into Broker — delegate queues instead of raising, drain on task completion"
```

---

## Task 4: Update startup sequence, MCP tool, and mcp_server

**Files:**
- Modify: `harness_claw/server.py`
- Modify: `harness_claw/gateway/mcp_server.py`

- [ ] **Step 1: Update startup in server.py**

In `harness_claw/server.py`, replace the startup handler. Find:

```python
@app.on_event("startup")
async def startup() -> None:
    task_store.expire(cfg.task_retention_days)
    task_store.mark_stale_as_failed()
```

Replace with:

```python
@app.on_event("startup")
async def startup() -> None:
    task_store.expire(cfg.task_retention_days)

    # Recover interrupted tasks (queued + running) into the scheduler
    interrupted = task_store.get_interrupted()
    broker.scheduler.recover(interrupted)
    task_store.mark_interrupted_as_queued()
```

Then find the end of the startup function (after the session restart loop and before the closing of the function) and add:

```python
    # Start scheduler poll loop and do an initial drain
    await broker.scheduler.start_poll_loop()
    await broker.scheduler.drain()
```

The full startup function should look like:

```python
@app.on_event("startup")
async def startup() -> None:
    task_store.expire(cfg.task_retention_days)

    # Recover interrupted tasks (queued + running) into the scheduler
    interrupted = task_store.get_interrupted()
    broker.scheduler.recover(interrupted)
    task_store.mark_interrupted_as_queued()

    # Wire broker task events into WebSocket broadcast
    async def on_task_event(event: str, task_dict: dict[str, Any]) -> None:
        await runner._broadcast({"type": event, "task": task_dict})

    broker.add_listener(on_task_event)

    async def _wf_broadcast(msg: dict) -> None:
        await runner._broadcast(msg)

    workflow_engine._broadcast_fn = _wf_broadcast

    import json as _json

    def _make_pty_callback_handler(session_id: str):
        async def _on_task_callback(event: Any) -> None:
            result_str = _json.dumps(event.payload.get("task", {}).get("result", ""))
            task_id = event.payload.get("task", {}).get("task_id", "unknown")
            status = event.payload.get("task", {}).get("status", "unknown")
            msg = (
                f"\n[TASK CALLBACK] task_id={task_id} status={status}\n"
                f"Result: {result_str}\n"
            ).encode()
            write_fn = dispatcher._writers.get(session_id)
            if write_fn is not None:
                write_fn(msg)
        return _on_task_callback

    runner._pty_callback_handler_factory = _make_pty_callback_handler
    runner._broker = broker

    for session in store.all():
        if session.status != "killed":
            await runner.start_session(session)

    # Start scheduler poll loop and do an initial drain
    await broker.scheduler.start_poll_loop()
    await broker.scheduler.drain()
```

- [ ] **Step 2: Add priority to agent_delegate in mcp_server.py**

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
            "delegated_to": task.delegated_to if task else None,
        },
    ))
    return {"task_id": task_id}
```

- [ ] **Step 3: Update agent.delegate tool schema in server.py**

In `harness_claw/server.py`, find the `agent.delegate` tool in `_MCP_TOOLS` and add `priority`:

```python
mcp_types.Tool(name="agent.delegate", description="Delegate a task to the best-matched agent; returns task_id",
    inputSchema={"type": "object", "properties": {
        "caps": {"type": "array", "items": {"type": "string"}},
        "instructions": {"type": "string"},
        "context": {"type": "object"},
        "callback": {"type": "boolean"},
        "priority": {"type": "integer", "description": "1=high, 2=normal (default), 3=low"},
    }, "required": ["caps", "instructions"]}),
```

- [ ] **Step 4: Verify the server imports cleanly**

```bash
.venv/bin/python -c "from harness_claw.server import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Run full test suite**

```bash
.venv/bin/pytest tests/ -v --ignore=tests/runtime/test_pty_session.py
```

Expected: ALL PASS (pty_session tests are skipped as they require a real terminal)

- [ ] **Step 6: Commit**

```bash
git add harness_claw/server.py harness_claw/gateway/mcp_server.py
git commit -m "feat: update startup to recover interrupted tasks and start scheduler poll loop"
```

---

## Task 5: UI — add priority to types and TasksTab

**Files:**
- Modify: `ui/src/types.ts`
- Modify: `ui/src/components/TasksTab.tsx`

- [ ] **Step 1: Add priority and resume to TaskRecord in types.ts**

In `ui/src/types.ts`, update the `TaskRecord` interface:

```typescript
export interface TaskRecord {
  task_id: string
  delegated_by: string
  delegated_to: string
  instructions: string
  caps_requested: string[]
  status: 'queued' | 'running' | 'completed' | 'failed'
  progress_pct: number
  progress_msg: string
  result: string | Record<string, unknown> | null
  context: Record<string, unknown> | null
  callback: boolean
  created_at: string
  updated_at: string
  priority: number
  resume: boolean
}
```

- [ ] **Step 2: Add priority badge to TasksTab.tsx**

In `ui/src/components/TasksTab.tsx`, add a helper function after the `statusColor` function:

```typescript
function priorityLabel(priority: number): string {
  if (priority === 1) return '↑ High'
  if (priority === 3) return '↓ Low'
  return '→ Normal'
}

function priorityColor(priority: number): string {
  if (priority === 1) return 'text-red-400'
  if (priority === 3) return 'text-gray-500'
  return 'text-gray-400'
}
```

Then in the task card JSX, add the priority badge next to the status badge. Find the line with `statusBadge(task.status)` and add alongside it:

```tsx
<span className={`text-xs w-20 text-right ${statusColor(task.status)}`}>
  {statusBadge(task.status)}
</span>
<span className={`text-xs ${priorityColor(task.priority ?? 2)}`}>
  {priorityLabel(task.priority ?? 2)}
</span>
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
git commit -m "feat: add priority field to TaskRecord and priority badge in TasksTab"
```

---

## Final Verification

- [ ] **Run full test suite**

```bash
.venv/bin/pytest tests/ -v --ignore=tests/runtime/test_pty_session.py 2>&1 | tail -30
```

Expected: ALL PASS

- [ ] **Push**

```bash
git push
```
