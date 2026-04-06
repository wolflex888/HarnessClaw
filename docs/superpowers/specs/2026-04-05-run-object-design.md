# Run Object Design

**Date:** 2026-04-05
**Status:** Approved

## Overview

Introduce a `Run` — a top-level execution envelope that records every agent execution in HarnessClaw. A Run is the parent of one or more Tasks. It captures what was asked, who asked it, how it was triggered, what permissions were granted, and where execution stopped. It lives independently of `Session` and `Task`, persisted in a new `runs.db` SQLite database.

## Motivation

Today, execution context is split across two objects:
- `Session` — the PTY process record; minimal fields; no trigger, caller, or permissions
- `Task` — the broker unit-of-work; has budget and resume fields but no parent envelope

There is no single record that answers: *who started this execution, why, with what permissions, and where did it stop?* The Run fills that gap.

## Data Model

```python
@dataclass
class Run:
    run_id: str                        # uuid4
    agent_id: str                      # session_id of the spawned PTY or external agent
    trigger_type: str                  # manual|delegate|spawn|workflow|schedule|event|recovery
    caller: str                        # who initiated: "user", task_id, workflow_id, "scheduler", etc.
    goal: str                          # human-readable task spec / instructions
    input_payload: dict                # structured input (context dict, workflow input, etc.)
    budget_usd: float | None           # max_cost from role or task
    timeout_secs: int | None           # max_time from role or task
    permissions_snapshot: dict         # {"scopes": [...], "role": {full role config at launch}}
    status: str                        # pending|running|paused|completed|failed
    task_ids: list[str]                # child task IDs in creation order
    checkpoint: dict | None            # {provider_session_id, last_task_id, progress_pct, pause_reason}
    created_at: str
    updated_at: str
```

### `trigger_type` values

| Value | When used |
|---|---|
| `manual` | User creates a session from the UI or API |
| `delegate` | Broker dispatches a task via `agent.delegate` |
| `spawn` | An agent calls `agent.spawn` |
| `workflow` | A workflow step starts an execution |
| `schedule` | Scheduler triggers an execution (future) |
| `event` | An event trigger fires (future) |
| `recovery` | Server restart re-launches an interrupted session |

### `permissions_snapshot`

A full snapshot of the security context at launch time, so the Run is self-describing even if the role definition changes later:

```json
{
  "scopes": ["memory:read", "task:delegate", "..."],
  "role": { "role_id": "...", "model": "...", "caps": [...], "allowed_tools": [...], "max_cost": ..., "max_time": ... }
}
```

### `checkpoint`

Written on pause or failure. Contains enough context to resume execution:

```json
{
  "provider_session_id": "<claude-jsonl-stem>",
  "last_task_id": "<task-id>",
  "progress_pct": 42,
  "pause_reason": "Cost $1.2400 exceeded limit $1.0000"
}
```

## Persistence

**New file:** `harness_claw/runtime/run_store.py`
**New database:** `runs.db` (alongside `tasks.db`, `memory.db`)

```sql
CREATE TABLE IF NOT EXISTS runs (
    run_id               TEXT PRIMARY KEY,
    agent_id             TEXT NOT NULL,
    trigger_type         TEXT NOT NULL,
    caller               TEXT NOT NULL,
    goal                 TEXT NOT NULL,
    input_payload        TEXT NOT NULL,   -- JSON
    budget_usd           REAL,
    timeout_secs         INTEGER,
    permissions_snapshot TEXT NOT NULL,   -- JSON
    status               TEXT NOT NULL,
    task_ids             TEXT NOT NULL,   -- JSON array
    checkpoint           TEXT,            -- JSON or NULL
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
)
```

`save()` uses `INSERT ... ON CONFLICT DO UPDATE` — same upsert pattern as `SqliteTaskStore`. `updated_at` is always refreshed on write.

**Store interface:**

```python
class SqliteRunStore:
    def save(self, run: Run) -> None: ...
    def get(self, run_id: str) -> Run | None: ...
    def all(self) -> list[Run]: ...
    def get_by_agent(self, agent_id: str) -> list[Run]: ...
    def get_by_status(self, status: str) -> list[Run]: ...
```

## RunManager

**New file:** `harness_claw/runtime/run_manager.py`

Owns Run creation, status transitions, and checkpoint writes. Subscribes to `EventBus` for reactive updates.

### `create_run()`

Called at each execution entry point. Creates the Run with `status="pending"` then immediately transitions to `"running"`.

```python
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
) -> str:  # returns run_id
```

### Event subscriptions

| EventBus event | Action |
|---|---|
| `task.created` | Append `task_id` to `run.task_ids`, save |
| `task.updated` (status=running) | Ensure `run.status = "running"`, save |
| `task.updated` (status=paused) | Set `run.status = "paused"`, write checkpoint, save |
| `task.completed` | Update checkpoint `last_task_id` + `progress_pct`, save |
| `session.ended` | Set `run.status = "completed"`, save |
| `task.failed` | Set `run.status = "failed"`, write checkpoint, save |

Run completion is driven by the session lifecycle (`session.ended`), not by counting Tasks — because Tasks are created dynamically and RunManager has no upfront count. Run status is eventually consistent — updated within one event loop tick of the triggering event.

### WebSocket broadcast

`RunManager` calls `runner._broadcast()` on every state change:

```python
{"type": "run.created",   "run": run.to_dict()}
{"type": "run.updated",   "run": run.to_dict()}
{"type": "run.completed", "run": run.to_dict()}
{"type": "run.failed",    "run": run.to_dict()}
```

## Call Sites

### `JobRunner.start_session()` — `manual`, `spawn`, `recovery`

```python
await run_manager.create_run(
    agent_id=session_id,
    trigger_type="manual",        # or "spawn" / "recovery"
    caller="user",                # or parent session_id for spawn
    goal=role.system_prompt,
    input_payload={},
    budget_usd=role.max_cost,
    timeout_secs=role.max_time,
    permissions_snapshot={
        "scopes": token_scopes,
        "role": role.to_dict(),
    },
)
```

### `Broker.delegate()` — `delegate`

```python
await run_manager.create_run(
    agent_id=matched_session_id,
    trigger_type="delegate",
    caller=delegated_by,
    goal=instructions,
    input_payload=context or {},
    budget_usd=max_cost,
    timeout_secs=max_time,
    permissions_snapshot={
        "scopes": token_store.get_scopes(matched_session_id),
        "role": role.to_dict(),
    },
)
```

### `WorkflowEngine` — `workflow`

Same pattern with `caller=workflow_id` and `trigger_type="workflow"`.

### Future: `schedule`, `event`

`RunManager.create_run()` signature is identical — wired at the scheduler/event-trigger entry points when they are built.

### Backward compatibility

`run_manager` is passed as an optional dependency (`None` by default) to `JobRunner` and `Broker`. Existing tests require no changes.

## REST API

Added to `server.py`:

```
GET  /api/runs                    → list all runs
GET  /api/runs/{run_id}           → get single run
GET  /api/runs?agent_id=<id>      → filter by agent
GET  /api/runs?status=<status>    → filter by status
```

Runs are never created directly by a client — only by `RunManager`.

## File Layout

```
harness_claw/runtime/
  run_store.py        # Run dataclass + SqliteRunStore
  run_manager.py      # RunManager (create_run, event subscriptions, broadcast)
  job_runner.py       # modified: call run_manager.create_run() in start_session()
  ...
harness_claw/
  server.py           # modified: instantiate RunManager, runs.db, REST endpoints
harness_claw/gateway/
  broker.py           # modified: call run_manager.create_run() in delegate()
```

## Testing

- Unit tests for `Run` dataclass serialization roundtrip
- Unit tests for `SqliteRunStore` (save, get, filter by agent/status)
- Unit tests for `RunManager.create_run()` — verifies Run is written with correct fields
- Integration tests for EventBus → RunManager transitions (pending→running→paused, pending→running→completed, pending→running→failed)
- Integration test: `start_session()` with `run_manager` wired — verifies a Run is created
- Integration test: `broker.delegate()` with `run_manager` wired — verifies a Run is created with `trigger_type="delegate"`
