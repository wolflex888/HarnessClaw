from pathlib import Path
import pytest
from harness_claw.role_registry import RoleConfig, RoleRegistry


def test_load_roles(tmp_path: Path) -> None:
    yaml_file = tmp_path / "agents.yaml"
    yaml_file.write_text("""
roles:
  - id: general-purpose
    name: General Purpose
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You are a helpful assistant."
    max_tokens: 8192
  - id: code-writer
    name: Code Writer
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You write clean code."
    max_tokens: 8192
""")
    registry = RoleRegistry(yaml_file)
    roles = registry.all()
    assert len(roles) == 2
    assert roles[0].id == "general-purpose"
    assert roles[1].id == "code-writer"


def test_get_role(tmp_path: Path) -> None:
    yaml_file = tmp_path / "agents.yaml"
    yaml_file.write_text("""
roles:
  - id: general-purpose
    name: General Purpose
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You are a helpful assistant."
    max_tokens: 8192
""")
    registry = RoleRegistry(yaml_file)
    role = registry.get("general-purpose")
    assert role is not None
    assert role.name == "General Purpose"
    assert role.model == "claude-sonnet-4-6"
    assert role.system_prompt == "You are a helpful assistant."
    assert role.max_tokens == 8192


def test_get_missing_role(tmp_path: Path) -> None:
    yaml_file = tmp_path / "agents.yaml"
    yaml_file.write_text("roles: []")
    registry = RoleRegistry(yaml_file)
    assert registry.get("nonexistent") is None
