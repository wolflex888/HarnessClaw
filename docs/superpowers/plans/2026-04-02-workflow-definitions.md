# Workflow Definitions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement declarative multi-step agent workflows in `agents.yaml`, driven by EventBus task completion events, with a REST+MCP API and a WorkflowsTab dashboard.

**Architecture:** A `WorkflowEngine` class (peer to `Broker`) subscribes to EventBus task events and autonomously drives step progression. Workflow definitions are parsed from `agents.yaml` by `RoleRegistry`. Run state persists in SQLite so workflows survive restarts.

**Tech Stack:** Python 3.12, FastAPI, SQLite (via sqlite3), asyncio, React 18, TypeScript

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `harness_claw/gateway/workflow_engine.py` | WorkflowStep, WorkflowDefinition, WorkflowRun dataclasses; WorkflowRunStore (SQLite); WorkflowEngine |
| Create | `tests/gateway/test_workflow_engine.py` | Unit tests for engine |
| Modify | `harness_claw/role_registry.py` | Parse `workflows:` section → `workflow_definitions` dict |
| Modify | `harness_claw/gateway/mcp_server.py` | Add `workflow_run()` tool; accept optional `workflow_engine` param |
| Modify | `harness_claw/server.py` | Instantiate WorkflowEngine; wire on startup; add REST endpoints; register `workflow.run` MCP handler |
| Modify | `agents.yaml` | Add example `workflows:` section |
| Modify | `ui/src/types.ts` | Add WorkflowDefinition, WorkflowStep, WorkflowRun types; extend WSIncoming |
| Modify | `ui/src/components/TabPanel.tsx` | Add `'workflows'` tab |
| Create | `ui/src/components/WorkflowsTab.tsx` | Split-panel: definitions left, runs right |
| Modify | `ui/src/App.tsx` | Add workflowRuns state + WS handling; render WorkflowsTab |

---

## Task 1: WorkflowDefinition dataclasses + YAML parsing

**Files:**
- Create: `harness_claw/gateway/workflow_engine.py`
- Create: `tests/gateway/test_workflow_engine.py`
- Modify: `harness_claw/role_registry.py`

- [ ] **Step 1: Write failing test for YAML parsing**

```python
# tests/gateway/test_workflow_engine.py
from __future__ import annotations
import pytest
from pathlib import Path
import tempfile
import yaml
from harness_claw.role_registry import RoleRegistry
from harness_claw.gateway.workflow_engine import WorkflowDefinition, WorkflowStep


def make_yaml(workflows: dict) -> Path:
    data = {
        "roles": [],
        "policy": {"engine": "local"},
        "memory": {"backend": "sqlite", "path": "./memory.db"},
        "broker": {"dispatcher": "local"},
        "event_bus": {"backend": "local"},
        "tasks": {"retention_days": 7},
        "connectors": [{"type": "local"}],
        "workflows": workflows,
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w")
    yaml.dump(data, tmp)
    tmp.close()
    return Path(tmp.name)


def test_parse_workflow_definitions():
    path = make_yaml({
        "review_cycle": {
            "name": "Review Cycle",
            "steps": [
                {"id": "write", "caps": ["code"], "instructions": "{{input}}", "on_success": "review", "on_failure": "stop"},
                {"id": "review", "caps": ["code_review"], "instructions": "Review: {{prev.result}}", "on_success": "stop", "on_failure": "fix"},
                {"id": "fix", "caps": ["code"], "instructions": "Fix: {{prev.result}}", "on_success": "review", "on_failure": "stop"},
            ],
        }
    })
    registry = RoleRegistry(path)
    defs = registry.workflow_definitions
    assert "review_cycle" in defs
    d = defs["review_cycle"]
    assert isinstance(d, WorkflowDefinition)
    assert d.id == "review_cycle"
    assert d.name == "Review Cycle"
    assert len(d.steps) == 3
    assert d.steps[0].id == "write"
    assert d.steps[0].caps == ["code"]
    assert d.steps[0].on_success == "review"
    assert d.steps[0].on_failure == "stop"
    assert d.steps[1].instructions == "Review: {{prev.result}}"
    assert d.step_by_id("fix") is not None
    assert d.step_by_id("missing") is None


def test_parse_no_workflows_section():
    path = make_yaml({})
    registry = RoleRegistry(path)
    assert registry.workflow_definitions == {}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/juichanglu/src/HarnessClaw
python -m pytest tests/gateway/test_workflow_engine.py::test_parse_workflow_definitions tests/gateway/test_workflow_engine.py::test_parse_no_workflows_section -v
```
Expected: FAIL with `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Create `harness_claw/gateway/workflow_engine.py` with dataclasses**

```python
# harness_claw/gateway/workflow_engine.py
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable


@dataclass
class WorkflowStep:
    id: str
    caps: list[str]
    instructions: str
    on_success: str   # step id or "stop"
    on_failure: str   # step id or "stop"


@dataclass
class WorkflowDefinition:
    id: str
    name: str
    steps: list[WorkflowStep]

    def step_by_id(self, step_id: str) -> WorkflowStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    @property
    def first_step(self) -> WorkflowStep:
        return self.steps[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "steps": [
                {
                    "id": s.id,
                    "caps": s.caps,
                    "instructions": s.instructions,
                    "on_success": s.on_success,
                    "on_failure": s.on_failure,
                }
                for s in self.steps
            ],
        }
```

- [ ] **Step 4: Add `workflow_definitions` property to `RoleRegistry`**

In `harness_claw/role_registry.py`, after the import block add:
```python
from harness_claw.gateway.workflow_engine import WorkflowDefinition, WorkflowStep
```

In `RoleRegistry.__init__`, after parsing roles, add:
```python
        self.workflow_definitions: dict[str, WorkflowDefinition] = {}
        for wf_id, wf_data in data.get("workflows", {}).items():
            steps = [
                WorkflowStep(
                    id=step["id"],
                    caps=list(step.get("caps", [])),
                    instructions=step["instructions"],
                    on_success=step["on_success"],
                    on_failure=step["on_failure"],
                )
                for step in wf_data.get("steps", [])
            ]
            self.workflow_definitions[wf_id] = WorkflowDefinition(
                id=wf_id,
                name=wf_data.get("name", wf_id),
                steps=steps,
            )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/gateway/test_workflow_engine.py::test_parse_workflow_definitions tests/gateway/test_workflow_engine.py::test_parse_no_workflows_section -v
```
Expected: PASS

- [ ] **Step 6: Run full test suite to check no regressions**

```bash
python -m pytest --tb=short -q
```
Expected: all existing tests still pass

- [ ] **Step 7: Commit**

```bash
git add harness_claw/gateway/workflow_engine.py harness_claw/role_registry.py tests/gateway/test_workflow_engine.py
git commit -m "feat: add WorkflowDefinition dataclasses and YAML parsing in RoleRegistry"
```

---

## Task 2: WorkflowRun data model + SQLite store

**Files:**
- Modify: `harness_claw/gateway/workflow_engine.py`
- Modify: `tests/gateway/test_workflow_engine.py`

- [ ] **Step 1: Write failing tests for WorkflowRun store**

Append to `tests/gateway/test_workflow_engine.py`:
```python
import tempfile
from harness_claw.gateway.workflow_engine import WorkflowRun, WorkflowRunStore


def make_run(run_id: str = "r1", workflow_id: str = "wf1") -> WorkflowRun:
    now = datetime.now(timezone.utc).isoformat()
    return WorkflowRun(
        run_id=run_id,
        workflow_id=workflow_id,
        status="running",
        current_step_id="write",
        step_results={},
        input="build a thing",
        initiated_by="user",
        created_at=now,
        updated_at=now,
    )


def test_workflow_run_store_save_and_get(tmp_path):
    store = WorkflowRunStore(tmp_path / "wf.db")
    run = make_run()
    store.save(run)
    loaded = store.get("r1")
    assert loaded is not None
    assert loaded.run_id == "r1"
    assert loaded.workflow_id == "wf1"
    assert loaded.status == "running"
    assert loaded.current_step_id == "write"
    assert loaded.step_results == {}
    assert loaded.input == "build a thing"


def test_workflow_run_store_update(tmp_path):
    store = WorkflowRunStore(tmp_path / "wf.db")
    run = make_run()
    store.save(run)
    run.status = "completed"
    run.step_results = {"write": "some code"}
    store.save(run)
    loaded = store.get("r1")
    assert loaded.status == "completed"
    assert loaded.step_results == {"write": "some code"}


def test_workflow_run_store_all(tmp_path):
    store = WorkflowRunStore(tmp_path / "wf.db")
    store.save(make_run("r1"))
    store.save(make_run("r2"))
    runs = store.all()
    assert len(runs) == 2
    assert {r.run_id for r in runs} == {"r1", "r2"}


def test_workflow_run_store_get_missing(tmp_path):
    store = WorkflowRunStore(tmp_path / "wf.db")
    assert store.get("nonexistent") is None


def test_workflow_run_to_dict():
    run = make_run()
    d = run.to_dict()
    assert d["run_id"] == "r1"
    assert d["status"] == "running"
    assert d["step_results"] == {}
```

Also add at the top of the test file (after existing imports):
```python
from datetime import datetime, timezone
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/gateway/test_workflow_engine.py::test_workflow_run_store_save_and_get tests/gateway/test_workflow_engine.py::test_workflow_run_to_dict -v
```
Expected: FAIL with `ImportError` on `WorkflowRun, WorkflowRunStore`

- [ ] **Step 3: Add WorkflowRun dataclass and WorkflowRunStore to `workflow_engine.py`**

Append to `harness_claw/gateway/workflow_engine.py`:
```python

@dataclass
class WorkflowRun:
    run_id: str
    workflow_id: str
    status: str          # running | completed | failed
    current_step_id: str
    step_results: dict[str, Any]
    input: str
    initiated_by: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "current_step_id": self.current_step_id,
            "step_results": self.step_results,
            "input": self.input,
            "initiated_by": self.initiated_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


_CREATE_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id          TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL,
    status          TEXT NOT NULL,
    current_step_id TEXT NOT NULL,
    step_results    TEXT NOT NULL DEFAULT '{}',
    input           TEXT NOT NULL,
    initiated_by    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)
"""


class WorkflowRunStore:
    def __init__(self, path: Path) -> None:
        self._path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_CREATE_RUNS_TABLE)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, run: WorkflowRun) -> None:
        run.updated_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_runs
                    (run_id, workflow_id, status, current_step_id, step_results,
                     input, initiated_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status          = excluded.status,
                    current_step_id = excluded.current_step_id,
                    step_results    = excluded.step_results,
                    updated_at      = excluded.updated_at
                """,
                (
                    run.run_id, run.workflow_id, run.status, run.current_step_id,
                    json.dumps(run.step_results), run.input, run.initiated_by,
                    run.created_at, run.updated_at,
                ),
            )

    def get(self, run_id: str) -> WorkflowRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return WorkflowRun(
            run_id=row["run_id"],
            workflow_id=row["workflow_id"],
            status=row["status"],
            current_step_id=row["current_step_id"],
            step_results=json.loads(row["step_results"]),
            input=row["input"],
            initiated_by=row["initiated_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def all(self) -> list[WorkflowRun]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_runs ORDER BY created_at ASC"
            ).fetchall()
        return [
            WorkflowRun(
                run_id=r["run_id"],
                workflow_id=r["workflow_id"],
                status=r["status"],
                current_step_id=r["current_step_id"],
                step_results=json.loads(r["step_results"]),
                input=r["input"],
                initiated_by=r["initiated_by"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]
```

- [ ] **Step 4: Run store tests to verify they pass**

```bash
python -m pytest tests/gateway/test_workflow_engine.py -k "store or to_dict" -v
```
Expected: PASS (5 tests)

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest --tb=short -q
```
Expected: all passing

- [ ] **Step 6: Commit**

```bash
git add harness_claw/gateway/workflow_engine.py tests/gateway/test_workflow_engine.py
git commit -m "feat: add WorkflowRun dataclass and SQLite WorkflowRunStore"
```

---

## Task 3: WorkflowEngine core

**Files:**
- Modify: `harness_claw/gateway/workflow_engine.py`
- Modify: `tests/gateway/test_workflow_engine.py`

- [ ] **Step 1: Write failing tests for WorkflowEngine**

Append to `tests/gateway/test_workflow_engine.py`:
```python
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from harness_claw.gateway.workflow_engine import WorkflowEngine, WorkflowDefinition, WorkflowStep
from harness_claw.gateway.event_bus import LocalEventBus, Event


def make_definition(steps_config: list[dict]) -> WorkflowDefinition:
    steps = [
        WorkflowStep(
            id=s["id"],
            caps=s.get("caps", ["code"]),
            instructions=s.get("instructions", "{{input}}"),
            on_success=s.get("on_success", "stop"),
            on_failure=s.get("on_failure", "stop"),
        )
        for s in steps_config
    ]
    return WorkflowDefinition(id="test_wf", name="Test Workflow", steps=steps)


def make_broker(task_id: str = "task-1") -> MagicMock:
    broker = MagicMock()
    broker.delegate = AsyncMock(return_value=task_id)
    return broker


@pytest.mark.asyncio
async def test_render_instructions():
    engine = WorkflowEngine(definitions={}, broker=MagicMock(), event_bus=LocalEventBus())
    result = engine._render("{{input}}", input="hello", prev_result=None, step_results={})
    assert result == "hello"

    result = engine._render("prev: {{prev.result}}", input="x", prev_result="world", step_results={})
    assert result == "prev: world"

    result = engine._render("{{steps.write.result}}", input="x", prev_result=None, step_results={"write": "some code"})
    assert result == "some code"

    result = engine._render(
        "Fix: {{prev.result}}\nOriginal: {{steps.write.result}}",
        input="x", prev_result="bug found", step_results={"write": "bad code"},
    )
    assert result == "Fix: bug found\nOriginal: bad code"


@pytest.mark.asyncio
async def test_start_workflow_delegates_first_step():
    defn = make_definition([
        {"id": "write", "caps": ["code"], "instructions": "{{input}}", "on_success": "stop", "on_failure": "stop"},
    ])
    broker = make_broker("t1")
    event_bus = LocalEventBus()
    engine = WorkflowEngine(definitions={"test_wf": defn}, broker=broker, event_bus=event_bus)

    run_id = await engine.start("test_wf", input="build a thing", initiated_by="user")
    assert run_id is not None
    broker.delegate.assert_called_once()
    call_kwargs = broker.delegate.call_args.kwargs
    assert call_kwargs["caps"] == ["code"]
    assert call_kwargs["instructions"] == "build a thing"
    assert call_kwargs["delegated_by"] == run_id

    run = engine.get_run(run_id)
    assert run.status == "running"
    assert run.current_step_id == "write"


@pytest.mark.asyncio
async def test_step_completion_stops_on_stop():
    defn = make_definition([
        {"id": "write", "caps": ["code"], "instructions": "{{input}}", "on_success": "stop", "on_failure": "stop"},
    ])
    broker = make_broker("t1")
    event_bus = LocalEventBus()
    broadcasts = []
    engine = WorkflowEngine(
        definitions={"test_wf": defn},
        broker=broker,
        event_bus=event_bus,
        broadcast_fn=lambda msg: broadcasts.append(msg),
    )
    run_id = await engine.start("test_wf", input="build", initiated_by="user")

    await event_bus.publish(
        f"task:t1:completed",
        payload={"task": {"task_id": "t1", "result": "done!", "status": "completed"}},
        source="broker",
    )

    run = engine.get_run(run_id)
    assert run.status == "completed"
    assert run.step_results["write"] == "done!"
    types = [b["type"] for b in broadcasts]
    assert "workflow.started" in types
    assert "workflow.step" in types
    assert "workflow.completed" in types


@pytest.mark.asyncio
async def test_step_completion_advances_to_next_step():
    defn = make_definition([
        {"id": "write", "caps": ["code"], "instructions": "{{input}}", "on_success": "review", "on_failure": "stop"},
        {"id": "review", "caps": ["code_review"], "instructions": "Review: {{prev.result}}", "on_success": "stop", "on_failure": "stop"},
    ])
    broker = MagicMock()
    broker.delegate = AsyncMock(side_effect=["t1", "t2"])
    event_bus = LocalEventBus()
    engine = WorkflowEngine(definitions={"test_wf": defn}, broker=broker, event_bus=event_bus)

    run_id = await engine.start("test_wf", input="build", initiated_by="user")
    assert broker.delegate.call_count == 1

    await event_bus.publish(
        "task:t1:completed",
        payload={"task": {"task_id": "t1", "result": "the code", "status": "completed"}},
        source="broker",
    )

    assert broker.delegate.call_count == 2
    second_call = broker.delegate.call_args.kwargs
    assert second_call["caps"] == ["code_review"]
    assert second_call["instructions"] == "Review: the code"

    run = engine.get_run(run_id)
    assert run.status == "running"
    assert run.current_step_id == "review"
    assert run.step_results["write"] == "the code"


@pytest.mark.asyncio
async def test_step_failure_follows_on_failure_branch():
    defn = make_definition([
        {"id": "write", "caps": ["code"], "instructions": "{{input}}", "on_success": "stop", "on_failure": "fix"},
        {"id": "fix", "caps": ["code"], "instructions": "Fix: {{prev.result}}", "on_success": "stop", "on_failure": "stop"},
    ])
    broker = MagicMock()
    broker.delegate = AsyncMock(side_effect=["t1", "t2"])
    event_bus = LocalEventBus()
    engine = WorkflowEngine(definitions={"test_wf": defn}, broker=broker, event_bus=event_bus)

    run_id = await engine.start("test_wf", input="build", initiated_by="user")
    await event_bus.publish(
        "task:t1:failed",
        payload={"task": {"task_id": "t1", "result": "error details", "status": "failed"}},
        source="broker",
    )

    assert broker.delegate.call_count == 2
    second_call = broker.delegate.call_args.kwargs
    assert second_call["caps"] == ["code"]
    assert second_call["instructions"] == "Fix: error details"

    run = engine.get_run(run_id)
    assert run.status == "running"
    assert run.current_step_id == "fix"


@pytest.mark.asyncio
async def test_step_failure_on_failure_stop_fails_run():
    defn = make_definition([
        {"id": "write", "caps": ["code"], "instructions": "{{input}}", "on_success": "stop", "on_failure": "stop"},
    ])
    broker = make_broker("t1")
    event_bus = LocalEventBus()
    broadcasts = []
    engine = WorkflowEngine(
        definitions={"test_wf": defn},
        broker=broker,
        event_bus=event_bus,
        broadcast_fn=lambda msg: broadcasts.append(msg),
    )
    run_id = await engine.start("test_wf", input="build", initiated_by="user")

    await event_bus.publish(
        "task:t1:failed",
        payload={"task": {"task_id": "t1", "result": "it broke", "status": "failed"}},
        source="broker",
    )

    run = engine.get_run(run_id)
    assert run.status == "failed"
    types = [b["type"] for b in broadcasts]
    assert "workflow.failed" in types


@pytest.mark.asyncio
async def test_start_unknown_workflow_raises():
    engine = WorkflowEngine(definitions={}, broker=MagicMock(), event_bus=LocalEventBus())
    with pytest.raises(ValueError, match="not found"):
        await engine.start("nonexistent", input="x", initiated_by="user")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/gateway/test_workflow_engine.py -k "engine or render or start or step or failure or unknown" -v
```
Expected: FAIL with `ImportError` on `WorkflowEngine`

- [ ] **Step 3: Add WorkflowEngine to `workflow_engine.py`**

Append to `harness_claw/gateway/workflow_engine.py`:
```python

class WorkflowEngine:
    def __init__(
        self,
        definitions: dict[str, WorkflowDefinition],
        broker: Any,
        event_bus: Any,
        broadcast_fn: Callable[[dict[str, Any]], Any] | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._definitions = definitions
        self._broker = broker
        self._event_bus = event_bus
        self._broadcast_fn = broadcast_fn
        self._store = WorkflowRunStore(db_path) if db_path else _InMemoryRunStore()
        self._task_subs: dict[str, list[Any]] = {}  # task_id -> [sub_completed, sub_failed]

    # -- Public API --

    async def start(self, workflow_id: str, input: str, initiated_by: str) -> str:
        defn = self._definitions.get(workflow_id)
        if defn is None:
            raise ValueError(f"workflow {workflow_id!r} not found")

        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        run = WorkflowRun(
            run_id=run_id,
            workflow_id=workflow_id,
            status="running",
            current_step_id=defn.first_step.id,
            step_results={},
            input=input,
            initiated_by=initiated_by,
            created_at=now,
            updated_at=now,
        )
        self._store.save(run)

        instructions = self._render(defn.first_step.instructions, input=input, prev_result=None, step_results={})
        task_id = await self._broker.delegate(
            delegated_by=run_id,
            caps=defn.first_step.caps,
            instructions=instructions,
        )
        await self._subscribe_step(run_id=run_id, step_id=defn.first_step.id, task_id=task_id)
        await self._broadcast({"type": "workflow.started", "run_id": run_id, "workflow_id": workflow_id, "step_id": defn.first_step.id})
        return run_id

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self._store.get(run_id)

    def list_runs(self) -> list[WorkflowRun]:
        return self._store.all()

    def list_definitions(self) -> list[WorkflowDefinition]:
        return list(self._definitions.values())

    # -- Internal --

    def _render(self, template: str, input: str, prev_result: Any, step_results: dict[str, Any]) -> str:
        result = template
        result = result.replace("{{input}}", input)
        prev_str = prev_result if isinstance(prev_result, str) else (json.dumps(prev_result) if prev_result is not None else "")
        result = result.replace("{{prev.result}}", prev_str)
        for match in re.finditer(r"\{\{steps\.([\w-]+)\.result\}\}", result):
            step_id = match.group(1)
            val = step_results.get(step_id)
            val_str = val if isinstance(val, str) else (json.dumps(val) if val is not None else "")
            result = result.replace(match.group(0), val_str)
        return result

    async def _subscribe_step(self, run_id: str, step_id: str, task_id: str) -> None:
        async def on_completed(event: Any) -> None:
            await self._on_step_event(run_id=run_id, step_id=step_id, task_id=task_id, outcome="completed", event=event)

        async def on_failed(event: Any) -> None:
            await self._on_step_event(run_id=run_id, step_id=step_id, task_id=task_id, outcome="failed", event=event)

        sub_ok = await self._event_bus.subscribe(f"task:{task_id}:completed", on_completed)
        sub_fail = await self._event_bus.subscribe(f"task:{task_id}:failed", on_failed)
        self._task_subs[task_id] = [sub_ok, sub_fail]

    async def _on_step_event(self, run_id: str, step_id: str, task_id: str, outcome: str, event: Any) -> None:
        for sub in self._task_subs.pop(task_id, []):
            await self._event_bus.unsubscribe(sub)

        run = self._store.get(run_id)
        if run is None or run.status != "running":
            return

        defn = self._definitions.get(run.workflow_id)
        if defn is None:
            return

        step = defn.step_by_id(step_id)
        if step is None:
            return

        result = event.payload.get("task", {}).get("result")
        run.step_results[step_id] = result

        await self._broadcast({"type": "workflow.step", "run_id": run_id, "step_id": step_id, "status": outcome, "result": result})

        next_step_id = step.on_success if outcome == "completed" else step.on_failure

        if next_step_id == "stop":
            run.status = "completed" if outcome == "completed" else "failed"
            self._store.save(run)
            if outcome == "completed":
                await self._broadcast({"type": "workflow.completed", "run_id": run_id})
            else:
                await self._broadcast({"type": "workflow.failed", "run_id": run_id, "reason": f"step {step_id!r} failed"})
            return

        next_step = defn.step_by_id(next_step_id)
        if next_step is None:
            run.status = "failed"
            self._store.save(run)
            await self._broadcast({"type": "workflow.failed", "run_id": run_id, "reason": f"step {next_step_id!r} not found"})
            return

        run.current_step_id = next_step_id
        self._store.save(run)

        instructions = self._render(next_step.instructions, input=run.input, prev_result=result, step_results=run.step_results)
        new_task_id = await self._broker.delegate(
            delegated_by=run_id,
            caps=next_step.caps,
            instructions=instructions,
        )
        await self._subscribe_step(run_id=run_id, step_id=next_step_id, task_id=new_task_id)

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        if self._broadcast_fn is not None:
            try:
                result = self._broadcast_fn(payload)
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                pass


class _InMemoryRunStore:
    """In-memory fallback used in tests when no db_path is provided."""

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRun] = {}

    def save(self, run: WorkflowRun) -> None:
        run.updated_at = datetime.now(timezone.utc).isoformat()
        self._runs[run.run_id] = run

    def get(self, run_id: str) -> WorkflowRun | None:
        return self._runs.get(run_id)

    def all(self) -> list[WorkflowRun]:
        return list(self._runs.values())
```

- [ ] **Step 4: Run WorkflowEngine tests to verify they pass**

```bash
python -m pytest tests/gateway/test_workflow_engine.py -v
```
Expected: all tests PASS

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest --tb=short -q
```
Expected: all passing

- [ ] **Step 6: Commit**

```bash
git add harness_claw/gateway/workflow_engine.py tests/gateway/test_workflow_engine.py
git commit -m "feat: add WorkflowEngine with step progression and failure branching"
```

---

## Task 4: MCP tool + REST API

**Files:**
- Modify: `harness_claw/gateway/mcp_server.py`
- Modify: `harness_claw/server.py`

- [ ] **Step 1: Write failing test for workflow.run MCP tool**

Create `tests/gateway/test_workflow_mcp.py`:
```python
# tests/gateway/test_workflow_mcp.py
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from harness_claw.gateway.mcp_server import GatewayMCP
from harness_claw.gateway.auth import TokenStore
from harness_claw.gateway.policy import LocalPolicyEngine


def make_mcp(workflow_engine=None) -> tuple[GatewayMCP, TokenStore]:
    token_store = TokenStore()
    broker = MagicMock()
    broker.delegate = AsyncMock(return_value="task-1")
    broker.get_task = MagicMock(return_value=None)
    memory = MagicMock()
    audit = MagicMock()
    audit.log = MagicMock()
    connector = MagicMock()
    connector.query = AsyncMock(return_value=[])

    mcp = GatewayMCP(
        token_store=token_store,
        policy=LocalPolicyEngine(),
        connectors=[connector],
        broker=broker,
        memory=memory,
        audit=audit,
        workflow_engine=workflow_engine,
    )
    return mcp, token_store


@pytest.mark.asyncio
async def test_workflow_run_tool():
    wf_engine = MagicMock()
    wf_engine.start = AsyncMock(return_value="run-123")

    mcp, token_store = make_mcp(workflow_engine=wf_engine)
    token = token_store.issue(subject="orch-1", scopes=["agent:delegate"])

    result = await mcp.workflow_run(token=token, workflow_id="code_review_cycle", input="review my PR")
    assert result == {"run_id": "run-123"}
    wf_engine.start.assert_called_once_with(
        workflow_id="code_review_cycle",
        input="review my PR",
        initiated_by="orch-1",
    )


@pytest.mark.asyncio
async def test_workflow_run_tool_no_engine():
    mcp, token_store = make_mcp(workflow_engine=None)
    token = token_store.issue(subject="orch-1", scopes=["agent:delegate"])

    with pytest.raises(RuntimeError, match="workflow engine"):
        await mcp.workflow_run(token=token, workflow_id="wf1", input="x")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/gateway/test_workflow_mcp.py -v
```
Expected: FAIL with `TypeError` on unexpected kwarg `workflow_engine`

- [ ] **Step 3: Add `workflow_engine` param and `workflow_run()` to `GatewayMCP`**

In `harness_claw/gateway/mcp_server.py`, update `__init__` signature:
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
    ) -> None:
        self._tokens = token_store
        self._policy = policy
        self._connectors = connectors
        self._broker = broker
        self._memory = memory
        self._audit = audit
        self._spawn_callback = spawn_callback
        self._workflow_engine = workflow_engine
```

Add new method after `agent_spawn` and before the memory tools section:
```python
    # --- Workflow tools ---

    async def workflow_run(self, token: str, workflow_id: str, input: str) -> dict[str, Any]:
        subject = self._auth(token, "agent:delegate")
        if self._workflow_engine is None:
            raise RuntimeError("workflow engine not configured")
        run_id = await self._workflow_engine.start(
            workflow_id=workflow_id,
            input=input,
            initiated_by=subject,
        )
        self._audit.log(AuditEvent(
            subject=subject, operation="workflow.run", resource=workflow_id,
            outcome="allowed", details={"run_id": run_id},
        ))
        return {"run_id": run_id}
```

- [ ] **Step 4: Run MCP test to verify it passes**

```bash
python -m pytest tests/gateway/test_workflow_mcp.py -v
```
Expected: PASS

- [ ] **Step 5: Wire WorkflowEngine in `server.py` and add REST endpoints**

In `harness_claw/server.py`:

1. After the existing imports, add:
```python
from harness_claw.gateway.workflow_engine import WorkflowEngine
```

2. Add `_workflows_db` path after `_tasks_db`:
```python
_workflows_db = _root / "workflows.db"
```

3. After the `task_store` line in the shared state section, add:
```python
workflow_engine = WorkflowEngine(
    definitions=registry.workflow_definitions,
    broker=broker,
    event_bus=event_bus,
    db_path=_workflows_db,
)
```

4. Update the `gateway_mcp` instantiation to pass `workflow_engine`:
```python
gateway_mcp = GatewayMCP(
    token_store=token_store,
    policy=policy,
    connectors=[connector, gateway_connector],
    broker=broker,
    memory=memory,
    audit=audit,
    workflow_engine=workflow_engine,
)
```

5. In the `startup()` function, add wiring for the broadcast fn after `broker.add_listener(on_task_event)`:
```python
    async def _wf_broadcast(msg: dict) -> None:
        await runner._broadcast(msg)

    workflow_engine._broadcast_fn = _wf_broadcast
```

6. In the `/mcp/tools/call` handler dict, add:
```python
        "workflow.run": lambda a: gateway_mcp.workflow_run(token=token, **a),
```

7. Add REST endpoints after the `/api/tasks/{task_id}/retry` endpoint:
```python
class WorkflowRunRequest(BaseModel):
    input: str
    initiated_by: str = "dashboard"


@app.get("/api/workflows")
def list_workflows() -> list[dict[str, Any]]:
    return [d.to_dict() for d in workflow_engine.list_definitions()]


@app.post("/api/workflows/{workflow_id}/run", status_code=201)
async def run_workflow(workflow_id: str, req: WorkflowRunRequest) -> dict[str, Any]:
    try:
        run_id = await workflow_engine.start(
            workflow_id=workflow_id,
            input=req.input,
            initiated_by=req.initiated_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"run_id": run_id}


@app.get("/api/workflows/runs")
def list_workflow_runs() -> list[dict[str, Any]]:
    return [r.to_dict() for r in workflow_engine.list_runs()]


@app.get("/api/workflows/runs/{run_id}")
def get_workflow_run(run_id: str) -> dict[str, Any]:
    run = workflow_engine.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run.to_dict()
```

8. In the `/api/mcp/tools` GET handler list, add:
```python
        {"name": "workflow.run", "description": "Start a named workflow by ID with an input string; returns run_id"},
```

- [ ] **Step 6: Verify the server imports without error**

```bash
python -c "from harness_claw.server import app; print('OK')"
```
Expected: `OK`

- [ ] **Step 7: Run full test suite**

```bash
python -m pytest --tb=short -q
```
Expected: all passing

- [ ] **Step 8: Commit**

```bash
git add harness_claw/gateway/mcp_server.py harness_claw/server.py tests/gateway/test_workflow_mcp.py
git commit -m "feat: add workflow.run MCP tool and workflow REST endpoints"
```

---

## Task 5: Add example workflow to agents.yaml

**Files:**
- Modify: `agents.yaml`

- [ ] **Step 1: Add `workflows:` section to `agents.yaml`**

Append to the end of `agents.yaml`:
```yaml

workflows:
  code_review_cycle:
    name: "Code Review Cycle"
    steps:
      - id: write
        caps: [python, typescript]
        instructions: "{{input}}"
        on_success: review
        on_failure: stop

      - id: review
        caps: [code-review]
        instructions: |
          Review the following code change and return your verdict:

          {{prev.result}}
        on_success: stop
        on_failure: fix

      - id: fix
        caps: [python, typescript]
        instructions: |
          Fix the issues found in review:

          {{prev.result}}

          Original task:
          {{steps.write.result}}
        on_success: review
        on_failure: stop
```

- [ ] **Step 2: Verify parsing works**

```bash
python -c "
from pathlib import Path
from harness_claw.role_registry import RoleRegistry
r = RoleRegistry(Path('agents.yaml'))
defs = r.workflow_definitions
print('Workflows:', list(defs.keys()))
d = defs['code_review_cycle']
print('Steps:', [s.id for s in d.steps])
print('OK')
"
```
Expected:
```
Workflows: ['code_review_cycle']
Steps: ['write', 'review', 'fix']
OK
```

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest --tb=short -q
```
Expected: all passing

- [ ] **Step 4: Commit**

```bash
git add agents.yaml
git commit -m "feat: add code_review_cycle workflow definition to agents.yaml"
```

---

## Task 6: TypeScript types + App.tsx WS handling

**Files:**
- Modify: `ui/src/types.ts`
- Modify: `ui/src/App.tsx`

- [ ] **Step 1: Add workflow types to `ui/src/types.ts`**

Append to `ui/src/types.ts`:
```typescript
// Workflow definitions (from /api/workflows)
export interface WorkflowStep {
  id: string
  caps: string[]
  instructions: string
  on_success: string
  on_failure: string
}

export interface WorkflowDefinition {
  id: string
  name: string
  steps: WorkflowStep[]
}

// Workflow run (from /api/workflows/runs)
export interface WorkflowRun {
  run_id: string
  workflow_id: string
  status: 'running' | 'completed' | 'failed'
  current_step_id: string
  step_results: Record<string, unknown>
  input: string
  initiated_by: string
  created_at: string
  updated_at: string
}
```

Extend the `WSIncoming` union type — add these cases to the existing union:
```typescript
  | { type: 'workflow.started'; run_id: string; workflow_id: string; step_id: string }
  | { type: 'workflow.step'; run_id: string; step_id: string; status: 'completed' | 'failed'; result: unknown }
  | { type: 'workflow.completed'; run_id: string }
  | { type: 'workflow.failed'; run_id: string; reason: string }
```

- [ ] **Step 2: Update `ui/src/App.tsx` to handle workflow state + WS events**

Add import at top of `App.tsx`:
```typescript
import type { WorkflowRun } from './types'
import { WorkflowsTab } from './components/WorkflowsTab'
```

Add state after the `mcpTools` state line:
```typescript
  const [workflowRuns, setWorkflowRuns] = useState<Record<string, WorkflowRun>>({})
```

Add initial fetch in the `useEffect` with the other fetches:
```typescript
    fetch('/api/workflows/runs')
      .then(r => r.json())
      .then((runList: WorkflowRun[]) => {
        const runMap: Record<string, WorkflowRun> = {}
        for (const r of runList) runMap[r.run_id] = r
        setWorkflowRuns(runMap)
      })
      .catch(console.error)
```

In `handleWsMessage`, add workflow event handling after the task event block (after line `setTasks(prev => ({ ...prev, [msg.task.task_id]: msg.task }))` and its closing brace):
```typescript
    } else if (msg.type === 'workflow.started') {
      setWorkflowRuns(prev => ({
        ...prev,
        [msg.run_id]: {
          ...(prev[msg.run_id] ?? {}),
          run_id: msg.run_id,
          workflow_id: msg.workflow_id,
          status: 'running' as const,
          current_step_id: msg.step_id,
          step_results: {},
          input: '',
          initiated_by: '',
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        },
      }))
    } else if (msg.type === 'workflow.step') {
      setWorkflowRuns(prev => {
        const existing = prev[msg.run_id]
        if (!existing) return prev
        return {
          ...prev,
          [msg.run_id]: {
            ...existing,
            step_results: { ...existing.step_results, [msg.step_id]: msg.result },
          },
        }
      })
    } else if (msg.type === 'workflow.completed') {
      setWorkflowRuns(prev => {
        const existing = prev[msg.run_id]
        if (!existing) return prev
        return { ...prev, [msg.run_id]: { ...existing, status: 'completed' } }
      })
    } else if (msg.type === 'workflow.failed') {
      setWorkflowRuns(prev => {
        const existing = prev[msg.run_id]
        if (!existing) return prev
        return { ...prev, [msg.run_id]: { ...existing, status: 'failed' } }
      })
    }
```

Add a `handleRunWorkflow` callback after `handleRetry`:
```typescript
  const handleRunWorkflow = useCallback(async (workflowId: string, input: string) => {
    const res = await fetch(`/api/workflows/${workflowId}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ input, initiated_by: 'dashboard' }),
    })
    if (!res.ok) console.error('workflow run failed', res.status, await res.text())
  }, [])
```

In the JSX, add the WorkflowsTab rendering after the `{tab === 'audit' && <AuditTab />}` line:
```tsx
                  {tab === 'workflows' && (
                    <WorkflowsTab
                      runs={Object.values(workflowRuns)}
                      onRunWorkflow={handleRunWorkflow}
                    />
                  )}
```

- [ ] **Step 3: Verify TypeScript compiles**

```bash
cd /Users/juichanglu/src/HarnessClaw/ui && npx tsc --noEmit 2>&1 | head -30
```
Expected: errors only about missing `WorkflowsTab` component (which is created in Task 7) — or clean if the component stub already exists

- [ ] **Step 4: Commit**

```bash
cd /Users/juichanglu/src/HarnessClaw
git add ui/src/types.ts ui/src/App.tsx
git commit -m "feat: add workflow TypeScript types and WS event handling in App.tsx"
```

---

## Task 7: WorkflowsTab component + TabPanel update

**Files:**
- Create: `ui/src/components/WorkflowsTab.tsx`
- Modify: `ui/src/components/TabPanel.tsx`
- Modify: `ui/src/App.tsx` (TabPanel renders WorkflowsTab only when `tab === 'workflows'` — already done in Task 6)

- [ ] **Step 1: Add `'workflows'` to TabPanel**

In `ui/src/components/TabPanel.tsx`, update the `TabId` type:
```typescript
export type TabId = 'work' | 'tasks' | 'agent' | 'tools' | 'memory' | 'audit' | 'workflows'
```

Add the new tab to the `TABS` array after the `audit` entry:
```typescript
  { id: 'workflows', label: 'Workflows' },
```

- [ ] **Step 2: Create `ui/src/components/WorkflowsTab.tsx`**

```tsx
// ui/src/components/WorkflowsTab.tsx
import { useState, useEffect, useCallback } from 'react'
import type { WorkflowDefinition, WorkflowRun } from '../types'

interface Props {
  runs: WorkflowRun[]
  onRunWorkflow: (workflowId: string, input: string) => Promise<void>
}

function statusColor(status: WorkflowRun['status']): string {
  if (status === 'completed') return 'text-green-400'
  if (status === 'failed') return 'text-red-400'
  return 'text-yellow-400'
}

function statusLabel(status: WorkflowRun['status']): string {
  if (status === 'completed') return 'completed'
  if (status === 'failed') return 'failed'
  return 'running'
}

export function WorkflowsTab({ runs, onRunWorkflow }: Props) {
  const [definitions, setDefinitions] = useState<WorkflowDefinition[]>([])
  const [selectedDef, setSelectedDef] = useState<WorkflowDefinition | null>(null)
  const [expandedRun, setExpandedRun] = useState<string | null>(null)
  const [showRunModal, setShowRunModal] = useState(false)
  const [runInput, setRunInput] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    fetch('/api/workflows')
      .then(r => r.json())
      .then(setDefinitions)
      .catch(console.error)
  }, [])

  const handleRunSubmit = useCallback(async () => {
    if (!selectedDef || !runInput.trim()) return
    setSubmitting(true)
    await onRunWorkflow(selectedDef.id, runInput.trim())
    setRunInput('')
    setShowRunModal(false)
    setSubmitting(false)
  }, [selectedDef, runInput, onRunWorkflow])

  const sortedRuns = [...runs].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  )

  return (
    <div className="flex flex-1 min-h-0 overflow-hidden">
      {/* Left panel: workflow definitions */}
      <div className="w-64 border-r border-gray-800 flex flex-col overflow-y-auto">
        <div className="p-2 text-xs text-gray-500 uppercase tracking-wide">Workflows</div>
        {definitions.map(def => (
          <div key={def.id}>
            <button
              onClick={() => setSelectedDef(selectedDef?.id === def.id ? null : def)}
              className={`w-full px-3 py-2 text-left border-b border-gray-800 hover:bg-gray-850 ${
                selectedDef?.id === def.id ? 'bg-gray-800' : ''
              }`}
            >
              <div className="text-sm text-gray-200">{def.name}</div>
              <div className="text-xs text-gray-500">{def.steps.length} steps</div>
            </button>
            {selectedDef?.id === def.id && (
              <div className="bg-gray-900 border-b border-gray-800 px-3 py-2">
                {def.steps.map((step, i) => (
                  <div key={step.id} className="text-xs text-gray-400 py-0.5 flex items-center gap-1">
                    <span className="text-gray-600">{i + 1}.</span>
                    <span className="font-mono text-gray-300">{step.id}</span>
                    <span className="text-gray-600">→</span>
                    <span>{step.caps.join(', ')}</span>
                  </div>
                ))}
                <button
                  onClick={() => setShowRunModal(true)}
                  className="mt-2 w-full text-xs bg-blue-600 hover:bg-blue-500 text-white px-2 py-1 rounded"
                >
                  Run
                </button>
              </div>
            )}
          </div>
        ))}
        {definitions.length === 0 && (
          <div className="px-3 py-2 text-xs text-gray-600">No workflows defined</div>
        )}
      </div>

      {/* Right panel: workflow runs */}
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="p-2 text-xs text-gray-500 uppercase tracking-wide border-b border-gray-800">
          Recent Runs
        </div>
        <div className="flex-1 overflow-y-auto">
          {sortedRuns.map(run => (
            <div key={run.run_id} className="border-b border-gray-800">
              <button
                onClick={() => setExpandedRun(expandedRun === run.run_id ? null : run.run_id)}
                className="w-full px-3 py-2 text-left hover:bg-gray-850 flex items-center justify-between"
              >
                <div>
                  <div className="text-sm text-gray-200">{run.workflow_id}</div>
                  <div className="text-xs text-gray-500">
                    by {run.initiated_by} · {new Date(run.created_at).toLocaleString()}
                  </div>
                </div>
                <span className={`text-xs font-medium ${statusColor(run.status)}`}>
                  {statusLabel(run.status)}
                </span>
              </button>
              {expandedRun === run.run_id && (
                <div className="px-4 pb-3 bg-gray-900">
                  <div className="text-xs text-gray-500 mb-1">Input: <span className="text-gray-300">{run.input}</span></div>
                  <div className="text-xs text-gray-500 mb-1">Current step: <span className="font-mono text-gray-300">{run.current_step_id}</span></div>
                  {Object.entries(run.step_results).map(([stepId, result]) => (
                    <div key={stepId} className="mt-1">
                      <div className="text-xs text-gray-500 font-mono">{stepId}:</div>
                      <pre className="text-xs text-gray-300 whitespace-pre-wrap font-mono pl-2 max-h-24 overflow-y-auto">
                        {typeof result === 'string' ? result : JSON.stringify(result, null, 2)}
                      </pre>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
          {sortedRuns.length === 0 && (
            <div className="px-3 py-4 text-xs text-gray-600">No runs yet. Select a workflow and click Run.</div>
          )}
        </div>
      </div>

      {/* Run modal */}
      {showRunModal && selectedDef && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
          <div className="bg-gray-900 border border-gray-700 rounded-lg p-4 w-96">
            <div className="text-sm text-white mb-3">Run: {selectedDef.name}</div>
            <textarea
              className="w-full bg-gray-800 text-gray-200 text-sm p-2 rounded border border-gray-700 resize-none h-24 focus:outline-none focus:border-blue-500"
              placeholder="Describe what you want the workflow to do..."
              value={runInput}
              onChange={e => setRunInput(e.target.value)}
              autoFocus
            />
            <div className="flex gap-2 mt-3 justify-end">
              <button
                onClick={() => { setShowRunModal(false); setRunInput('') }}
                className="text-xs text-gray-400 hover:text-gray-200 px-3 py-1.5 border border-gray-700 rounded"
              >
                Cancel
              </button>
              <button
                onClick={handleRunSubmit}
                disabled={!runInput.trim() || submitting}
                className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white px-3 py-1.5 rounded"
              >
                {submitting ? 'Starting...' : 'Start'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: Build the UI to verify no TypeScript errors**

```bash
cd /Users/juichanglu/src/HarnessClaw/ui && npm run build 2>&1 | tail -20
```
Expected: build succeeds with no errors

- [ ] **Step 4: Run full Python test suite**

```bash
cd /Users/juichanglu/src/HarnessClaw && python -m pytest --tb=short -q
```
Expected: all passing

- [ ] **Step 5: Commit**

```bash
cd /Users/juichanglu/src/HarnessClaw
git add ui/src/components/WorkflowsTab.tsx ui/src/components/TabPanel.tsx ui/src/App.tsx
git commit -m "feat: add WorkflowsTab component and wire into dashboard"
```
