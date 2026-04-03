from __future__ import annotations
import pytest
from pathlib import Path
import tempfile
import yaml
from datetime import datetime, timezone
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


def test_workflow_definition_to_dict():
    steps = [
        WorkflowStep(id="write", caps=["code"], instructions="{{input}}", on_success="stop", on_failure="stop"),
    ]
    d = WorkflowDefinition(id="wf1", name="Test WF", steps=steps)
    result = d.to_dict()
    assert result["id"] == "wf1"
    assert result["name"] == "Test WF"
    assert len(result["steps"]) == 1
    assert result["steps"][0]["id"] == "write"
    assert result["steps"][0]["caps"] == ["code"]
    assert result["steps"][0]["on_success"] == "stop"


def test_workflow_definition_first_step():
    steps = [
        WorkflowStep(id="write", caps=["code"], instructions="{{input}}", on_success="stop", on_failure="stop"),
        WorkflowStep(id="review", caps=["code_review"], instructions="review", on_success="stop", on_failure="stop"),
    ]
    d = WorkflowDefinition(id="wf1", name="Test WF", steps=steps)
    assert d.first_step.id == "write"


def test_workflow_definition_empty_steps_raises():
    with pytest.raises(ValueError, match="at least one step"):
        WorkflowDefinition(id="bad", name="Bad WF", steps=[])


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
    assert d["workflow_id"] == "wf1"
    assert d["current_step_id"] == "write"


import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from harness_claw.gateway.workflow_engine import WorkflowEngine
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
        "task:t1:completed",
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
