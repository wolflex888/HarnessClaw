from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness_claw.role_registry import RoleRegistry


def _write_yaml(tmp_path: Path, extra: str = "") -> Path:
    p = tmp_path / "agents.yaml"
    p.write_text(f"""
policy:
  engine: local
memory:
  backend: sqlite
  path: ./memory.db
broker:
  dispatcher: local
event_bus:
  backend: local
{extra}
connectors: []
roles: []
""")
    return p


def _write_yaml_roles(tmp_path: Path, roles: list[dict]) -> Path:
    p = tmp_path / "agents.yaml"
    p.write_text(yaml.dump({"roles": roles}))
    return p


def test_task_retention_days_from_yaml(tmp_path):
    p = _write_yaml(tmp_path, "tasks:\n  retention_days: 14")
    registry = RoleRegistry(p)
    assert registry.gateway_config.task_retention_days == 14


def test_task_retention_days_defaults_to_7(tmp_path):
    p = _write_yaml(tmp_path)
    registry = RoleRegistry(p)
    assert registry.gateway_config.task_retention_days == 7


def test_role_defaults_no_limits(tmp_path):
    p = _write_yaml_roles(tmp_path, [{"id": "r1", "name": "R1", "model": "claude-sonnet-4-5-20251001",
                                       "system_prompt": "hi"}])
    reg = RoleRegistry(p)
    role = reg.get("r1")
    assert role.max_cost is None
    assert role.max_time is None
    assert role.allowed_tools is None


def test_role_parses_limits(tmp_path):
    p = _write_yaml_roles(tmp_path, [{"id": "r1", "name": "R1", "model": "claude-sonnet-4-5-20251001",
                                       "system_prompt": "hi", "max_cost": 0.5,
                                       "max_time": 3600, "allowed_tools": ["Read", "Bash"]}])
    reg = RoleRegistry(p)
    role = reg.get("r1")
    assert role.max_cost == 0.5
    assert role.max_time == 3600
    assert role.allowed_tools == ["Read", "Bash"]
