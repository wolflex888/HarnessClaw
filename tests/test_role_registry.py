from __future__ import annotations

from pathlib import Path

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


def test_task_retention_days_from_yaml(tmp_path):
    p = _write_yaml(tmp_path, "tasks:\n  retention_days: 14")
    registry = RoleRegistry(p)
    assert registry.gateway_config.task_retention_days == 14


def test_task_retention_days_defaults_to_7(tmp_path):
    p = _write_yaml(tmp_path)
    registry = RoleRegistry(p)
    assert registry.gateway_config.task_retention_days == 7
