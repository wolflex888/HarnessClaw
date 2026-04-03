# Scheduler Design

**Date:** 2026-04-03
**Status:** Approved

## Overview

Add a priority-aware task scheduler to the broker so that tasks are queued when no matching agent is available, dispatched automatically when one becomes free, and survive server restarts without data loss.

Today `Broker.delegate` raises `ValueError` if no agent matches the requested caps. This makes agent pipelines fragile — a momentarily busy agent causes the whole delegation to fail. The scheduler replaces this fail-fast behavior with a durable queue backed by SQLite and drained by both event-driven triggers and a background poll loop.

## Goals

- Tasks are never lost due to agent unavailability
- Higher-priority tasks are dispatched before lower-priority ones
- Queued and running tasks survive server restarts and are re-dispatched automatically
- Running tasks (mid-pipeline) get a resume preamble so the agent can continue from its conversation history
- Public API of `Broker.delegate` is unchanged except it no longer raises on no-agent

## Non-Goals

- Full agent state checkpointing / snapshot-restore
- Preemption (a running task is never interrupted to make room for a higher-priority one)
- Per-agent concurrency limits (agents can receive at most one task at a time — unchanged)

---

## Data Model Changes

### `Task` dataclass (`gateway/task_store.py`)

Two new fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `priority` | `int` | `2` | Dispatch priority. Lower = higher priority. 1=high, 2=normal, 3=low. |
| `resume` | `bool` | `False` | Set to `True` when a running task is re-queued after a server restart. Causes a resume preamble to be prepended to instructions at dispatch time. |

### SQLite schema (`tasks` table)

Two new columns added via `ALTER TABLE` migration on first startup:

```sql
ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 2;
ALTER TABLE tasks ADD COLUMN resume   INTEGER NOT NULL DEFAULT 0;
```

### `SqliteTaskStore` new methods

```python
def get_interrupted(self) -> list[Task]:
    """Return all tasks with status in ('queued', 'running')."""

def mark_interrupted_as_queued(self) -> int:
    """Set status='queued' for all interrupted tasks. Returns count updated."""
```

---

## Scheduler Component

A `Scheduler` class added to `gateway/broker.py`. The `Broker` owns one instance.

### Interface

```python
class Scheduler:
    def __init__(
        self,
        connectors: list[CapabilityConnector],
        dispatcher: TaskDispatcher,
        store: TaskStoreProtocol,
        poll_interval: int = 30,
    ) -> None: ...

    def push(self, task: Task) -> None:
        """Add a task to the in-memory priority queue and persist to SQLite as queued."""

    def recover(self, tasks: list[Task]) -> None:
        """Called on startup. Re-enqueues all interrupted tasks.
        Tasks with status='running' have resume=True set before being pushed."""

    async def drain(self) -> None:
        """Attempt to dispatch queued tasks to available agents.
        Iterates the heapq by priority; dispatches each task whose caps
        are satisfied by an available agent. Stops when queue is empty
        or no agents are available for any remaining task."""

    async def start_poll_loop(self) -> None:
        """Start a background asyncio task that calls drain() every poll_interval seconds."""

    async def stop(self) -> None:
        """Cancel the background poll loop."""
```

### Priority queue ordering

The in-memory queue is a `heapq` of `(priority, created_at, task_id)` tuples so that:
- Lower `priority` integer = dispatched first
- Equal priority = FIFO by `created_at`
- `task_id` is the tiebreaker to avoid comparing `Task` objects

### Resume preamble

When `Scheduler.drain()` dispatches a task with `resume=True`, the dispatcher receives modified instructions:

```
[RESUME] You were previously working on this task. Your conversation history is intact — continue where you left off.

<original instructions>
```

---

## Broker Changes

### `Broker.delegate`

```python
async def delegate(
    self,
    delegated_by: str,
    caps: list[str],
    instructions: str,
    context: dict | None = None,
    callback: bool = False,
    priority: int = 2,           # new parameter
) -> str:
```

Behavior change:
- If a matching agent is found → dispatch immediately (unchanged, status=`running`)
- If no matching agent → `scheduler.push(task)` (task status=`queued`), return `task_id`
- Never raises `ValueError` for no-agent-available

### `Broker.complete_task` and `Broker.fail_task`

Both call `asyncio.create_task(self.scheduler.drain())` after updating task state. This is the event-driven drain trigger — when an agent finishes, the scheduler immediately tries to dispatch the next queued task to it.

---

## Startup Sequence (`server.py`)

```python
@app.on_event("startup")
async def startup():
    # 1. Recover interrupted tasks (replaces mark_stale_as_failed)
    interrupted = task_store.get_interrupted()
    broker.scheduler.recover(interrupted)        # re-queues all; sets resume=True for running ones
    task_store.mark_interrupted_as_queued()      # sync SQLite status to match

    # 2. Start PTY sessions (unchanged)
    for session in store.all():
        if session.status != "killed":
            await runner.start_session(session)

    # 3. Start scheduler poll loop
    await broker.scheduler.start_poll_loop()

    # 4. Initial drain (agents just came up, dispatch any recovered queued tasks)
    await broker.scheduler.drain()

    # ... rest of startup unchanged
```

`mark_stale_as_failed` is no longer called on startup. It remains available for explicit use (e.g. admin endpoint) but is not part of the normal boot sequence.

---

## Drain Algorithm

```
drain():
  snapshot = sorted copy of heapq (by priority, created_at)
  for each task in snapshot:
    candidates = query all connectors for agents matching task.caps_requested
    if candidates is empty:
      continue  # no agent available for this task right now
    agent = candidates[0]  # least-loaded (existing selection logic)
    pop task from heapq
    if task.resume:
      prepend resume preamble to instructions
    try:
      dispatch(task, agent)
      task.status = "running"
      store.save(task)
      notify("task.updated", task)
    except Exception:
      push task back onto heapq  # dispatch failed, will retry on next drain
```

The drain does not stop on first miss — it continues checking remaining queued tasks in case a different agent can serve a different cap set. This allows heterogeneous agent pools to make progress even when some cap sets are saturated.

---

## Testing

### 1. Queue and drain
- Delegate a task when no agent is registered → task status is `queued`
- Register an agent, call `drain()` → task is dispatched, status becomes `running`

### 2. Priority ordering
- Push 3 tasks: priority=3 (low), priority=1 (high), priority=2 (normal) — no agent available
- Register one agent, call `drain()`
- Verify: high-priority task (priority=1) is dispatched first

### 3. Restart recovery — queued tasks
- Save a task to SQLite with `status='queued'`
- Create a fresh `Broker` + `Scheduler`, call `recover([task])`
- Call `drain()` with a registered agent → task is dispatched with original instructions (no resume preamble)

### 4. Restart recovery — running tasks
- Save a task to SQLite with `status='running'`
- Call `recover([task])` → task gets `resume=True`
- Call `drain()` → task dispatched with resume preamble prepended

### 5. Event-driven drain
- Queue a task, no agent available
- Complete an unrelated task (freeing an agent) → `complete_task` triggers drain → queued task is dispatched automatically

---

## UI

Minor: add a `Priority` column to the Tasks tab in the dashboard showing `high / normal / low` labels. No other UI changes required — the `queued` status badge already exists.
