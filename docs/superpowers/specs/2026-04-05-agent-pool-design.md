# Agent Pool Design

**Date:** 2026-04-05
**Status:** Approved

## Problem

`execute_plan` delegates tasks to agents via the broker but never spawns them. When no agents with the required capabilities are running, tasks queue silently and `_wait_for_task` blocks forever. Additionally, `spawn_callback` is never wired from `JobRunner` into `GatewayMCP` in `server.py`, so `agent.spawn` also fails at runtime.

## Goal

Give `execute_plan` the ability to spawn the agents it needs, manage their lifetime for the duration of the plan, and clean them up when the plan completes or fails.

## Decisions

- Pool is **scoped to a single plan execution** — spawned when the plan starts, killed when it ends
- **One agent per role** — reused across all tasks in the plan (not spawned per task)
- **Working directory** = directory containing the plan file
- Agents are **killed immediately** on plan completion or failure

## Architecture

### New File: `harness_claw/runtime/agent_pool.py`

```python
class AgentPool:
    def __init__(
        self,
        runner: JobRunner,
        connector: LocalConnector,
        registry: RoleRegistry,
        working_dir: str,
        acquire_timeout: int = 30,
    ) -> None: ...

    async def acquire(self, caps: list[str]) -> str:
        """Ensure an agent with matching caps is running. Returns session_id.
        Idempotent — second call for same caps returns the existing session_id.
        Raises TimeoutError if agent doesn't register within acquire_timeout seconds.
        Raises ValueError if no role in registry matches the caps.
        Raises RuntimeError if runner has no spawn capability."""

    async def release_all(self) -> None:
        """Kill all sessions owned by this pool. Logs and continues on error."""
```

**Internal state:** `_owned: dict[str, str]` — maps `role_id → session_id` for sessions this pool spawned.

**`acquire()` logic:**
1. Check `_owned` — if a role matching `caps` already exists, return its `session_id`
2. Find matching `RoleConfig` in registry (`cap_set.issubset(role.caps)`)
3. Call `runner.start_session(Session(role_id, working_dir))` to spawn the PTY agent
4. Poll `connector.query(caps)` with 0.5s sleep until the agent appears or timeout expires
5. Store `role_id → session_id` in `_owned` and return `session_id`

### Modified: `harness_claw/runtime/plan_executor.py`

`PlanExecutor.__init__` gains an optional `pool: AgentPool | None = None`.

Before each `broker.delegate(caps=...)` call, if `pool` is set:
```python
if self._pool is not None:
    await self._pool.acquire(caps)
```

The broker then dispatches normally — no other changes to `PlanExecutor`.

### Modified: `harness_claw/gateway/mcp_server.py`

`GatewayMCP.__init__` gains:
- `connector: LocalConnector | None = None`
- `registry: RoleRegistry | None = None`

`execute_plan()` creates an `AgentPool` and wraps execution:
```python
pool = AgentPool(
    runner=self._runner,   # JobRunner, separate from spawn_callback
    connector=self._connector,
    registry=self._registry,
    working_dir=str(Path(plan_path).parent),
)
executor = PlanExecutor(broker=self._broker, event_bus=self._event_bus,
                        on_progress=on_progress, pool=pool)
try:
    summary = await executor.execute(plan_path)
finally:
    await pool.release_all()
```

`GatewayMCP` gains a new `runner: JobRunner | None = None` parameter (separate from the existing `spawn_callback`, which stays for `agent.spawn`). `AgentPool` receives `runner` directly and calls `runner.start_session()` / `runner.kill_session()`.

### Modified: `harness_claw/server.py`

Three additions to the `GatewayMCP(...)` call:
1. `runner=runner` (new param — `JobRunner` instance, for pool use)
2. `connector=connector`
3. `registry=registry`

`spawn_callback` is left unwired (stays `None`) since `agent.spawn` is a separate MCP tool not needed for plan execution.

## Data Flow

```
execute_plan(plan_path)
  │
  ├── pool = AgentPool(runner, connector, registry, working_dir=plan_dir)
  │
  ├── try:
  │     PlanExecutor.execute(plan_path)
  │       │
  │       └── for each task:
  │             pool.acquire(["python","typescript"])
  │               └── if not spawned: runner.start_session(code-writer, plan_dir)
  │                   poll connector.query() until registered (max 30s)
  │             broker.delegate(caps) → dispatches to code-writer PTY
  │             _wait_for_task()
  │
  │             pool.acquire(["spec-review"])   → spawns spec-reviewer (first task only)
  │             pool.acquire(["code-review"])   → spawns code-reviewer (first task only)
  │             broker.delegate × 2 in parallel
  │             _wait_for_task() × 2
  │
  └── finally:
        pool.release_all() → runner.kill_session() × 3
```

## Error Handling

| Scenario | Behaviour |
|---|---|
| Agent doesn't register within 30s | `acquire()` raises `TimeoutError`; `release_all()` still runs via `finally` |
| `runner` is `None` (not wired) | `acquire()` raises `RuntimeError` immediately — fail fast |
| No role matches the requested caps | `acquire()` raises `ValueError` |
| `kill_session()` raises during cleanup | `release_all()` logs the error and continues killing other sessions |

## Files Changed

| File | Action |
|---|---|
| `harness_claw/runtime/agent_pool.py` | **Create** |
| `harness_claw/runtime/plan_executor.py` | **Modify** — add optional `pool` param |
| `harness_claw/gateway/mcp_server.py` | **Modify** — add `runner`/`connector`/`registry`; build pool in `execute_plan` |
| `harness_claw/server.py` | **Modify** — wire `runner`, `connector`, `registry` into `GatewayMCP` |
| `tests/runtime/test_agent_pool.py` | **Create** |
| `tests/runtime/test_plan_executor.py` | **Modify** — add pool integration test |

## Testing

- **`test_agent_pool.py`**: mock `JobRunner`, real `LocalConnector`. Tests: acquire spawns once, second acquire returns same session, release_all kills all, timeout raises, unknown caps raises `ValueError`
- **`test_plan_executor.py`**: existing tests unchanged (pool defaults to `None`). Add one test with mock pool verifying `acquire()` is called with correct caps before each `broker.delegate()`
- **`test_server_endpoints.py`**: existing missing-file test unchanged
