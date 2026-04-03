# Task Persistence — Design Spec

**Date:** 2026-04-02
**Status:** Approved

## Problem

Tasks live in `Broker._store` (an in-memory `TaskStore`) and are lost on server restart. There is no task history for debugging failed workflows.

## Goals

- Tasks survive server restarts
- In-flight tasks (`queued`/`running`) at restart time are marked `failed` with reason `"server_restart"`
- Failed tasks can be manually retried from the UI
- Tasks older than a configurable retention window are expired
- Default retention: 7 days

## Approach

Write-through cache using plain `sqlite3`. The broker's in-memory `TaskStore` is replaced by `SqliteTaskStore`, which has the same `save`/`get`/`all` interface plus three lifecycle methods. No broker logic changes beyond accepting the store as a constructor param.

---

## Section 1: Data Layer

**New file:** `harness_claw/gateway/task_store.py`

### Schema

```sql
CREATE TABLE IF NOT EXISTS tasks (
    task_id        TEXT PRIMARY KEY,
    delegated_by   TEXT NOT NULL,
    delegated_to   TEXT NOT NULL,
    instructions   TEXT NOT NULL,
    caps_requested TEXT NOT NULL,   -- JSON array
    context        TEXT,            -- JSON object or NULL
    status         TEXT NOT NULL,   -- queued | running | completed | failed
    progress_pct   INTEGER NOT NULL DEFAULT 0,
    progress_msg   TEXT NOT NULL DEFAULT '',
    result         TEXT,            -- JSON or plain string or NULL
    callback       INTEGER NOT NULL DEFAULT 0,  -- 0 | 1
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
)
```

### Interface

```python
class SqliteTaskStore:
    def __init__(self, path: Path) -> None: ...

    def save(self, task: Task) -> None:
        # UPDATE updated_at, upsert row

    def get(self, task_id: str) -> Task | None: ...

    def all(self) -> list[Task]: ...

    def mark_stale_as_failed(self) -> int:
        # UPDATE tasks SET status='failed', result='"server_restart"'
        # WHERE status IN ('queued', 'running')
        # Returns count of rows updated

    def expire(self, days: int) -> int:
        # DELETE tasks WHERE updated_at < now() - days
        # Returns count of rows deleted
```

`TaskStore` (in-memory) stays in `broker.py` unchanged — used as the fallback in tests.

---

## Section 2: Broker Integration

Single change to `Broker.__init__`:

```python
def __init__(self, connectors, dispatcher, event_bus=None, task_store=None):
    self._store = task_store or TaskStore()
```

All existing mutations already call `self._store.save(task)`, so persistence is automatic. Tests pass `task_store=None` and get the in-memory store as before — no test changes needed.

---

## Section 3: Startup Wiring

### `agents.yaml`

```yaml
tasks:
  retention_days: 7
```

### `GatewayConfig` (role_registry.py)

```python
task_retention_days: int = 7
```

Parsed from the `tasks` section in `agents.yaml`.

### `server.py`

```python
_tasks_db = _root / "tasks.db"
task_store = SqliteTaskStore(_tasks_db)
broker = Broker(..., task_store=task_store)
```

In the `startup` handler (order matters):
```python
task_store.expire(cfg.task_retention_days)   # remove old tasks first
task_store.mark_stale_as_failed()            # mark survivors as failed
```

---

## Section 4: Retry

### REST endpoint

```
POST /api/tasks/{task_id}/retry
```

- 404 if task not found
- 400 if task status is not `failed`
- Calls `broker.delegate(delegated_by=task.delegated_by, caps=task.caps_requested, instructions=task.instructions, context=task.context)`
- Returns `{"task_id": "<new_task_id>"}`

### UI

`TasksTab.tsx`: add a "Retry" button for tasks with `status === 'failed'`. On click, POST to the endpoint and merge the returned task into local state.

---

## Files Changed

| File | Change |
|------|--------|
| `harness_claw/gateway/task_store.py` | New — `SqliteTaskStore` |
| `tests/gateway/test_task_store.py` | New — unit tests |
| `harness_claw/gateway/broker.py` | Accept optional `task_store` param |
| `harness_claw/role_registry.py` | Add `task_retention_days` to `GatewayConfig` |
| `agents.yaml` | Add `tasks.retention_days: 7` |
| `harness_claw/server.py` | Instantiate store, wire startup, add retry endpoint |
| `ui/src/components/TasksTab.tsx` | Add retry button for failed tasks |

---

## Testing

- `test_task_store.py`: save/get/all, mark_stale_as_failed, expire
- `test_broker.py`: broker uses SqliteTaskStore, task survives a second broker instance reading same DB
- Existing broker tests continue to pass (use in-memory fallback)
