# Agent Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce `AgentPool` so `execute_plan` automatically spawns and cleans up the agents it needs rather than silently queuing tasks with no workers.

**Architecture:** A new `AgentPool` class wraps `JobRunner` and `LocalConnector`; it spawns one PTY agent per role on demand (idempotent), polls until the agent registers, and kills all owned sessions on `release_all()`. `PlanExecutor` gains an optional `pool` param and calls `pool.acquire(caps)` before each delegation. `GatewayMCP.execute_plan` builds the pool and wraps execution in `try/finally`.

**Tech Stack:** Python 3.12, asyncio, existing `JobRunner` / `LocalConnector` / `RoleRegistry` / `Session` patterns.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `harness_claw/runtime/agent_pool.py` | **Create** | `AgentPool`: spawn-on-demand, poll-until-registered, kill-all |
| `harness_claw/runtime/plan_executor.py` | **Modify** | Add optional `pool` param; call `acquire()` before delegate |
| `harness_claw/gateway/mcp_server.py` | **Modify** | Add `runner`/`connector`/`registry` params; build pool in `execute_plan` |
| `harness_claw/server.py` | **Modify** | Pass `runner`, `connector`, `registry` to `GatewayMCP` |
| `tests/runtime/test_agent_pool.py` | **Create** | `AgentPool` unit tests |
| `tests/runtime/test_plan_executor.py` | **Modify** | Add one test verifying `acquire()` is called before `delegate()` |

---

## Task 1: `AgentPool` class

**Files:**
- Create: `harness_claw/runtime/agent_pool.py`
- Create: `tests/runtime/test_agent_pool.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/runtime/test_agent_pool.py
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from harness_claw.gateway.capability import AgentAdvertisement, LocalConnector
from harness_claw.role_registry import RoleConfig
from harness_claw.runtime.agent_pool import AgentPool
from harness_claw.session import Session


def make_role(role_id: str, caps: list[str]) -> RoleConfig:
    return RoleConfig(
        id=role_id,
        name=role_id,
        provider="claude-code",
        model="claude-sonnet-4-6",
        system_prompt="you are helpful",
        caps=caps,
    )


def make_registry(roles: list[RoleConfig]):
    """Return a minimal registry-like object with .all() and .get()."""
    reg = MagicMock()
    reg.all.return_value = roles
    reg.get.side_effect = lambda rid: next((r for r in roles if r.id == rid), None)
    return reg


def make_runner(connector: LocalConnector, roles: list[RoleConfig]) -> MagicMock:
    """Runner whose start_session immediately registers the agent in connector."""
    caps_by_role = {r.id: r.caps for r in roles}

    async def fake_start(session: Session) -> None:
        await connector.register(AgentAdvertisement(
            session_id=session.session_id,
            role_id=session.role_id,
            caps=caps_by_role.get(session.role_id, []),
            status="idle",
            task_count=0,
            connector="local",
        ))

    runner = MagicMock()
    runner.start_session = AsyncMock(side_effect=fake_start)
    runner.kill_session = MagicMock()
    return runner


@pytest.mark.asyncio
async def test_acquire_spawns_agent_and_returns_session_id():
    roles = [make_role("code-writer", ["python", "typescript"])]
    connector = LocalConnector()
    runner = make_runner(connector, roles)
    registry = make_registry(roles)
    pool = AgentPool(runner=runner, connector=connector, registry=registry, working_dir="/tmp")

    session_id = await pool.acquire(["python", "typescript"])

    assert isinstance(session_id, str) and len(session_id) > 0
    runner.start_session.assert_called_once()
    spawned: Session = runner.start_session.call_args[0][0]
    assert spawned.role_id == "code-writer"
    assert spawned.working_dir == "/tmp"
    assert spawned.model == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_acquire_is_idempotent():
    roles = [make_role("code-writer", ["python", "typescript"])]
    connector = LocalConnector()
    runner = make_runner(connector, roles)
    registry = make_registry(roles)
    pool = AgentPool(runner=runner, connector=connector, registry=registry, working_dir="/tmp")

    sid1 = await pool.acquire(["python", "typescript"])
    sid2 = await pool.acquire(["python", "typescript"])

    assert sid1 == sid2
    assert runner.start_session.call_count == 1


@pytest.mark.asyncio
async def test_acquire_unknown_caps_raises_value_error():
    connector = LocalConnector()
    registry = make_registry([])
    runner = MagicMock()
    pool = AgentPool(runner=runner, connector=connector, registry=registry, working_dir="/tmp")

    with pytest.raises(ValueError, match="no role"):
        await pool.acquire(["unknown-cap"])


@pytest.mark.asyncio
async def test_acquire_none_runner_raises_runtime_error():
    roles = [make_role("code-writer", ["python"])]
    connector = LocalConnector()
    registry = make_registry(roles)
    pool = AgentPool(runner=None, connector=connector, registry=registry, working_dir="/tmp")

    with pytest.raises(RuntimeError, match="requires a runner"):
        await pool.acquire(["python"])


@pytest.mark.asyncio
async def test_acquire_timeout_raises():
    roles = [make_role("code-writer", ["python"])]
    connector = LocalConnector()  # nothing ever registers

    async def slow_start(session: Session) -> None:
        pass  # doesn't register in connector

    runner = MagicMock()
    runner.start_session = AsyncMock(side_effect=slow_start)
    registry = make_registry(roles)
    pool = AgentPool(runner=runner, connector=connector, registry=registry,
                     working_dir="/tmp", acquire_timeout=1)

    with pytest.raises(TimeoutError):
        await pool.acquire(["python"])


@pytest.mark.asyncio
async def test_release_all_kills_owned_sessions():
    roles = [
        make_role("code-writer", ["python", "typescript"]),
        make_role("spec-reviewer", ["spec-review"]),
    ]
    connector = LocalConnector()
    runner = make_runner(connector, roles)
    registry = make_registry(roles)
    pool = AgentPool(runner=runner, connector=connector, registry=registry, working_dir="/tmp")

    await pool.acquire(["python", "typescript"])
    await pool.acquire(["spec-review"])
    await pool.release_all()

    assert runner.kill_session.call_count == 2
    assert pool._owned == {}


@pytest.mark.asyncio
async def test_release_all_continues_on_kill_error():
    roles = [
        make_role("code-writer", ["python"]),
        make_role("spec-reviewer", ["spec-review"]),
    ]
    connector = LocalConnector()
    runner = make_runner(connector, roles)
    runner.kill_session = MagicMock(side_effect=RuntimeError("boom"))
    registry = make_registry(roles)
    pool = AgentPool(runner=runner, connector=connector, registry=registry, working_dir="/tmp")

    await pool.acquire(["python"])
    await pool.acquire(["spec-review"])

    # Should not raise even though kill_session raises
    await pool.release_all()
    assert pool._owned == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/runtime/test_agent_pool.py -v
```

Expected: `ERROR` — `ModuleNotFoundError: No module named 'harness_claw.runtime.agent_pool'`

- [ ] **Step 3: Implement `AgentPool`**

```python
# harness_claw/runtime/agent_pool.py
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from harness_claw.gateway.capability import LocalConnector
from harness_claw.role_registry import RoleRegistry
from harness_claw.session import Session

_logger = logging.getLogger(__name__)


class AgentPool:
    """Spawns and tracks PTY agents for the duration of a plan execution.

    One agent per role, reused across tasks. All owned sessions are killed
    on release_all().
    """

    def __init__(
        self,
        runner: Any,
        connector: LocalConnector,
        registry: RoleRegistry,
        working_dir: str,
        acquire_timeout: int = 30,
    ) -> None:
        self._runner = runner
        self._connector = connector
        self._registry = registry
        self._working_dir = working_dir
        self._acquire_timeout = acquire_timeout
        self._owned: dict[str, str] = {}  # role_id → session_id

    async def acquire(self, caps: list[str]) -> str:
        """Ensure an agent with matching caps is running. Returns session_id.

        Idempotent — second call for same caps returns the cached session_id.
        """
        cap_set = set(caps)

        # Return existing session if we already own a matching role
        for role_id, session_id in self._owned.items():
            role = self._registry.get(role_id)
            if role and cap_set.issubset(set(role.caps)):
                return session_id

        if self._runner is None:
            raise RuntimeError("agent pool requires a runner — not configured")

        # Find a role in the registry that satisfies the requested caps
        role = next(
            (r for r in self._registry.all() if cap_set.issubset(set(r.caps))),
            None,
        )
        if role is None:
            raise ValueError(f"no role in registry matches caps {caps!r}")

        # Spawn the PTY agent
        session = Session(
            role_id=role.id,
            working_dir=self._working_dir,
            model=role.model,
        )
        session_id = session.session_id
        await self._runner.start_session(session)

        # Poll connector until the agent self-registers (or timeout)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._acquire_timeout
        while True:
            agents = await self._connector.query(caps)
            if any(a.session_id == session_id for a in agents):
                break
            if loop.time() >= deadline:
                raise TimeoutError(
                    f"agent {session_id!r} (role={role.id!r}) did not register"
                    f" within {self._acquire_timeout}s"
                )
            await asyncio.sleep(0.5)

        self._owned[role.id] = session_id
        return session_id

    async def release_all(self) -> None:
        """Kill all sessions owned by this pool."""
        for role_id, session_id in list(self._owned.items()):
            try:
                self._runner.kill_session(session_id)
            except Exception as exc:
                _logger.warning(
                    "AgentPool: failed to kill session %s (role=%s): %s",
                    session_id, role_id, exc,
                )
        self._owned.clear()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/runtime/test_agent_pool.py -v
```

Expected: 7 tests, all PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/runtime/agent_pool.py tests/runtime/test_agent_pool.py
git commit -m "feat: add AgentPool — spawn-on-demand PTY agents for plan execution"
```

---

## Task 2: Wire `AgentPool` into `PlanExecutor`

**Files:**
- Modify: `harness_claw/runtime/plan_executor.py`
- Modify: `tests/runtime/test_plan_executor.py`

- [ ] **Step 1: Write the failing test**

Add this test to the bottom of `tests/runtime/test_plan_executor.py`:

```python
@pytest.mark.asyncio
async def test_run_task_calls_pool_acquire_before_delegate():
    """pool.acquire() must be called with the right caps before each broker.delegate()."""
    from unittest.mock import AsyncMock as AM
    event_bus = LocalEventBus()
    broker = MagicMock()
    tasks_store: dict[str, Task] = {}
    broker.get_task.side_effect = lambda tid: tasks_store.get(tid)

    def result_factory(call_num, caps):
        if "python" in caps or "typescript" in caps:
            return {"summary": "done", "files_changed": []}
        return {"verdict": "APPROVE", "summary": "ok", "findings": []}

    broker.delegate = AsyncMock(side_effect=build_fake_delegate(event_bus, tasks_store, result_factory))

    acquired_caps: list[list[str]] = []
    pool = MagicMock()
    pool.acquire = AM(side_effect=lambda caps: acquired_caps.append(caps))

    progress = []
    async def on_progress(msg: str) -> None:
        progress.append(msg)

    executor = PlanExecutor(broker=broker, event_bus=event_bus, on_progress=on_progress, pool=pool)
    plan_task = PlanTask(index=1, title="My Task", full_text="## Task 1: My Task\n\nDo it.")
    result = await executor._run_task(plan_task, total=1)

    assert result == "DONE: My Task"
    # acquire called for writer caps, spec-review, code-review
    assert ["python", "typescript"] in acquired_caps
    assert ["spec-review"] in acquired_caps
    assert ["code-review"] in acquired_caps
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/runtime/test_plan_executor.py::test_run_task_calls_pool_acquire_before_delegate -v
```

Expected: FAIL — `TypeError: PlanExecutor.__init__() got an unexpected keyword argument 'pool'`

- [ ] **Step 3: Add `pool` param to `PlanExecutor` and call `acquire()` before each delegation**

In `harness_claw/runtime/plan_executor.py`, make these changes:

Change `__init__` from:
```python
def __init__(
    self,
    broker: Any,
    event_bus: EventBus,
    on_progress: Callable[[str], Awaitable[None]],
) -> None:
    self._broker = broker
    self._event_bus = event_bus
    self._on_progress = on_progress
```

To:
```python
def __init__(
    self,
    broker: Any,
    event_bus: EventBus,
    on_progress: Callable[[str], Awaitable[None]],
    pool: Any | None = None,
) -> None:
    self._broker = broker
    self._event_bus = event_bus
    self._on_progress = on_progress
    self._pool = pool
```

In `_run_task`, add `await self._pool.acquire(caps)` before each `broker.delegate()`. There are three delegate call sites:

**1. Writer delegation** (around line 48) — change:
```python
        await self._on_progress(f"{prefix} Implementing: {task.title}...")
        writer_tid = await self._broker.delegate(
            delegated_by="plan-executor",
            caps=["python", "typescript"],
            instructions=task.full_text,
        )
```
To:
```python
        await self._on_progress(f"{prefix} Implementing: {task.title}...")
        if self._pool is not None:
            await self._pool.acquire(["python", "typescript"])
        writer_tid = await self._broker.delegate(
            delegated_by="plan-executor",
            caps=["python", "typescript"],
            instructions=task.full_text,
        )
```

**2. Parallel reviewer delegations** (around line 73) — change:
```python
            spec_tid, quality_tid = await asyncio.gather(
                self._broker.delegate(
                    delegated_by="plan-executor",
                    caps=["spec-review"],
                    instructions=spec_instr,
                ),
                self._broker.delegate(
                    delegated_by="plan-executor",
                    caps=["code-review"],
                    instructions=quality_instr,
                ),
            )
```
To:
```python
            if self._pool is not None:
                await asyncio.gather(
                    self._pool.acquire(["spec-review"]),
                    self._pool.acquire(["code-review"]),
                )
            spec_tid, quality_tid = await asyncio.gather(
                self._broker.delegate(
                    delegated_by="plan-executor",
                    caps=["spec-review"],
                    instructions=spec_instr,
                ),
                self._broker.delegate(
                    delegated_by="plan-executor",
                    caps=["code-review"],
                    instructions=quality_instr,
                ),
            )
```

**3. Rewrite writer delegation** (around line 123) — change:
```python
            rewrite_tid = await self._broker.delegate(
                delegated_by="plan-executor",
                caps=["python", "typescript"],
                instructions=rewrite_instr,
            )
```
To:
```python
            if self._pool is not None:
                await self._pool.acquire(["python", "typescript"])
            rewrite_tid = await self._broker.delegate(
                delegated_by="plan-executor",
                caps=["python", "typescript"],
                instructions=rewrite_instr,
            )
```

- [ ] **Step 4: Run all plan executor tests**

```bash
python -m pytest tests/runtime/test_plan_executor.py -v
```

Expected: 5 tests, all PASS (4 existing + 1 new).

- [ ] **Step 5: Commit**

```bash
git add harness_claw/runtime/plan_executor.py tests/runtime/test_plan_executor.py
git commit -m "feat: wire AgentPool into PlanExecutor — acquire before each delegation"
```

---

## Task 3: Update `GatewayMCP.execute_plan` to build and own the pool

**Files:**
- Modify: `harness_claw/gateway/mcp_server.py`

- [ ] **Step 1: Add `runner`, `connector`, `registry` params to `GatewayMCP.__init__`**

In `harness_claw/gateway/mcp_server.py`, change `__init__` from:
```python
    def __init__(
        self,
        token_store: TokenStore,
        policy: PolicyEngine,
        connectors: list[CapabilityConnector],
        broker: Broker,
        memory: MemoryStore,
        audit: AuditLogger,
        spawn_callback: Any | None = None,
        workflow_engine: Any | None = None,
        event_bus: EventBus | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._tokens = token_store
        self._policy = policy
        self._connectors = connectors
        self._broker = broker
        self._memory = memory
        self._audit = audit
        self._spawn_callback = spawn_callback
        self._workflow_engine = workflow_engine
        self._event_bus = event_bus
        self._tool_registry = tool_registry
```
To:
```python
    def __init__(
        self,
        token_store: TokenStore,
        policy: PolicyEngine,
        connectors: list[CapabilityConnector],
        broker: Broker,
        memory: MemoryStore,
        audit: AuditLogger,
        spawn_callback: Any | None = None,
        workflow_engine: Any | None = None,
        event_bus: EventBus | None = None,
        tool_registry: ToolRegistry | None = None,
        runner: Any | None = None,
        connector: Any | None = None,
        registry: Any | None = None,
    ) -> None:
        self._tokens = token_store
        self._policy = policy
        self._connectors = connectors
        self._broker = broker
        self._memory = memory
        self._audit = audit
        self._spawn_callback = spawn_callback
        self._workflow_engine = workflow_engine
        self._event_bus = event_bus
        self._tool_registry = tool_registry
        self._runner = runner
        self._connector = connector
        self._registry = registry
```

- [ ] **Step 2: Rewrite `execute_plan` to build pool and wrap in `try/finally`**

Replace the current `execute_plan` method body (lines 192–219 in `mcp_server.py`) with:

```python
    async def execute_plan(self, token: str, plan_path: str) -> str:
        subject = self._auth(token, "agent:delegate")

        if not Path(plan_path).exists():
            raise FileNotFoundError(f"Plan file not found: {plan_path!r}")

        if self._event_bus is None:
            raise RuntimeError("execute_plan requires event_bus — not configured")

        progress_log: list[str] = []

        async def on_progress(msg: str) -> None:
            progress_log.append(msg)

        pool = None
        if (
            self._runner is not None
            and self._connector is not None
            and self._registry is not None
        ):
            from harness_claw.runtime.agent_pool import AgentPool
            pool = AgentPool(
                runner=self._runner,
                connector=self._connector,
                registry=self._registry,
                working_dir=str(Path(plan_path).parent),
            )

        executor = PlanExecutor(
            broker=self._broker,
            event_bus=self._event_bus,
            on_progress=on_progress,
            pool=pool,
        )

        try:
            summary = await executor.execute(plan_path)
        finally:
            if pool is not None:
                await pool.release_all()

        self._audit.log(AuditEvent(
            subject=subject, operation="execute_plan", resource=plan_path,
            outcome="allowed", details={"plan_path": plan_path},
        ))
        progress_output = "\n".join(progress_log)
        return f"{progress_output}\n\n---\n\n{summary}"
```

- [ ] **Step 3: Run existing server endpoint tests to verify nothing broke**

```bash
python -m pytest tests/test_server_endpoints.py::test_execute_plan_missing_file_returns_error -v
```

Expected: PASS.

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest -v
```

Expected: all previously-passing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/mcp_server.py
git commit -m "feat: GatewayMCP.execute_plan builds AgentPool when runner/connector/registry wired"
```

---

## Task 4: Wire `runner`, `connector`, `registry` into `GatewayMCP` in `server.py`

**Files:**
- Modify: `harness_claw/server.py`

- [ ] **Step 1: Add three kwargs to the `GatewayMCP(...)` constructor call**

In `harness_claw/server.py`, find the `gateway_mcp = GatewayMCP(...)` block (around line 79) and add three lines:

```python
gateway_mcp = GatewayMCP(
    token_store=token_store,
    policy=policy,
    connectors=[connector, gateway_connector],
    broker=broker,
    memory=memory,
    audit=audit,
    workflow_engine=workflow_engine,
    event_bus=event_bus,
    tool_registry=tool_registry,
    runner=runner,
    connector=connector,
    registry=registry,
)
```

- [ ] **Step 2: Run full test suite**

```bash
python -m pytest -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add harness_claw/server.py
git commit -m "feat: wire runner/connector/registry into GatewayMCP so execute_plan can spawn agents"
```
