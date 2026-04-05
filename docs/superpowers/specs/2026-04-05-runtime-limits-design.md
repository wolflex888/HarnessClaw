# Runtime Limits & Pause Design

**Date:** 2026-04-05
**Status:** Approved

## Overview

Add cost, time, and tool-permission limits to the agent runtime. When a limit is exceeded the agent's PTY is killed, the task is set to `paused` (resumable), and the dashboard notifies the operator. No work is lost — the scheduler's existing resume-preamble mechanism re-delivers the task when a human clicks Resume.

## Goals

- Hard cost (USD) and time (seconds) caps per role, overridable per task at delegation
- Tool allow-list per role, enforced via Claude Code's `permissions` settings
- Limit violations → `paused` task status (not `failed`) with human-readable reason
- Resume restores full conversation history via `provider_session_id` (Claude Code: `--resume <id>`)
- Other providers fall back to the text preamble until they implement their own resume

## Non-Goals

- Preemption / partial rollback of in-flight tool calls
- Per-task cost isolation when a session handles multiple sequential tasks (cost is session-level for MVP)
- Quota enforcement across multiple sessions of the same role

---

## Data Model Changes

### `RoleConfig` (`harness_claw/role_registry.py`)

Three new optional fields:

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `max_cost` | `float \| None` | `None` | USD ceiling for the session |
| `max_time` | `int \| None` | `None` | Seconds ceiling from session start |
| `allowed_tools` | `list[str] \| None` | `None` | Explicit allow-list; `None` = all tools |

`agents.yaml` example:
```yaml
- id: code-reviewer
  caps: [code-reviewer]
  max_cost: 0.50
  max_time: 3600
  allowed_tools: [Read, Grep, Glob, Bash]
```

### `Task` (`harness_claw/gateway/task_store.py`)

Five new fields:

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `max_cost` | `float \| None` | `None` | Per-task override (wins over role default) |
| `max_time` | `int \| None` | `None` | Per-task override |
| `allowed_tools` | `list[str] \| None` | `None` | Per-task override |
| `provider_session_id` | `str \| None` | `None` | Claude session ID captured at runtime for resume |
| `pause_reason` | `str \| None` | `None` | Human-readable reason, e.g. `"Cost $0.52 exceeded limit $0.50"` |

New valid status: **`paused`** — task is stopped but resumable. Scheduler re-enqueues it with `resume=True` when a human clicks Resume.

---

## LimitEnforcer

New file: `harness_claw/runtime/limit_enforcer.py`

```python
class LimitEnforcer:
    def __init__(self, session_id: str, max_cost: float | None,
                 max_time: int | None, on_exceeded: Callable)
    def check_cost(self, current_cost: float) -> None
    def check_time(self) -> None
```

- Created per session in `JobRunner.start_session`, initialized with role limits merged with any per-task overrides
- `check_cost` and `check_time` are called on every `CostPoller` tick (every ~3 seconds)
- Fires `on_exceeded(reason, detail)` exactly once, then becomes a no-op
- `on_exceeded` is an async callback wired to `JobRunner.pause_session`

---

## Pause Mechanism (`JobRunner.pause_session`)

When `on_exceeded` fires:

1. **Kill PTY** — `self.kill_session(session_id)` (existing method)
2. **Mark task paused** — find the task running on this session, set `status="paused"`, `pause_reason=detail`, `resume=True`
3. **Store provider_session_id** — already captured by `CostPoller` from the JSONL filename
4. **Re-enqueue** — `self._broker.scheduler.push(task)` (task stays in queue for human to trigger resume)
5. **Broadcast** — `task.updated` WebSocket event

---

## provider_session_id Capture

`CostPoller` already reads JSONL files from `~/.claude/projects/<encoded_cwd>/`. The JSONL filename (without `.jsonl`) is the Claude Code session ID. On first successful poll, `CostPoller` records the filename and passes it via the cost callback as a new optional field.

`JobRunner` stores it in the running `Task` via `broker.update_task_field(task_id, provider_session_id=...)`.

When the scheduler re-dispatches a resumed task with `provider_session_id` set, it passes it to the provider. `ClaudeCodeProvider` adds `--resume <provider_session_id>` to the CLI invocation.

`BaseProvider.stream_chat` already has a `claude_session_id` parameter — it stays as-is (no rename). Other providers ignore it until they add their own resume support.

---

## Tool Policy Enforcement

`ClaudeCodeProvider.configure_session` writes to `{working_dir}/.claude/settings.json`. When `allowed_tools` is non-null it adds:

```json
{
  "permissions": {
    "allow": ["Read", "Grep", "Glob", "Bash"],
    "deny": ["*"]
  }
}
```

`unconfigure_session` removes the `permissions` key when the session ends.

**Limitation:** directory-level policy — two sessions sharing the same working directory would conflict. Each agent session should have its own working directory.

---

## agent.delegate Changes

`GatewayMCP.agent_delegate` and `Broker.delegate` accept three new optional parameters:

```python
max_cost: float | None = None    # USD; overrides role default
max_time: int | None = None      # seconds; overrides role default
allowed_tools: list[str] | None = None
```

MCP tool schema updated with these fields. Role defaults are looked up from the registry at session start and stored on the Session; per-task values override only for the duration of that task.

---

## Resume Flow

1. Human clicks **Resume** in the dashboard → `POST /api/tasks/{task_id}/resume`
2. Endpoint: sets `task.status = "queued"`, clears `pause_reason`, calls `scheduler.push(task)`, triggers `drain()`
3. Scheduler dispatches task to a matching agent with resume preamble + `--resume <provider_session_id>` (if Claude Code)
4. Agent resumes with full conversation history

---

## UI Changes

**TasksTab:**
- `"paused"` status badge — yellow, distinct from gray `"queued"` and red `"failed"`
- Pause reason shown below task instructions
- **Resume** button on paused tasks → calls `POST /api/tasks/{task_id}/resume`

**types.ts:**
- Add `"paused"` to `TaskRecord` status union
- Add `pause_reason: string | null` and `provider_session_id: string | null` to `TaskRecord`

---

## Testing

1. `LimitEnforcer` fires exactly once when cost threshold crossed
2. `LimitEnforcer` fires exactly once when time threshold crossed
3. `LimitEnforcer` is a no-op after firing
4. `pause_session` sets task to `paused` with correct reason and `resume=True`
5. `ClaudeCodeProvider.configure_session` writes correct permissions when `allowed_tools` is set
6. `ClaudeCodeProvider.unconfigure_session` removes permissions
7. `POST /api/tasks/{task_id}/resume` re-queues a paused task and triggers drain
8. Task fields roundtrip through SQLite (new columns)
