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
