from pathlib import Path
import pytest
from harness_claw.role_registry import RoleRegistry, RoleConfig, GatewayConfig

YAML = """
policy:
  engine: local

memory:
  backend: sqlite
  path: ./memory.db

broker:
  dispatcher: local

connectors:
  - type: local
  - type: gateway
    heartbeat_ttl: 30
    bootstrap_token: "testtoken"

roles:
  - id: orchestrator
    name: Orchestrator
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You orchestrate."
    max_tokens: 8192
    scopes: [agent:list, agent:delegate, agent:spawn, memory:read, memory:write]
    caps: [orchestration, planning]
  - id: coder
    name: Coder
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You write code."
    scopes: [agent:list, memory:read, memory:write]
    caps: [python, typescript]
"""

@pytest.fixture
def registry(tmp_path):
    p = tmp_path / "agents.yaml"
    p.write_text(YAML)
    return RoleRegistry(p)

def test_role_scopes_parsed(registry):
    role = registry.get("orchestrator")
    assert role.scopes == ["agent:list", "agent:delegate", "agent:spawn", "memory:read", "memory:write", "agent:report"]

def test_role_caps_parsed(registry):
    role = registry.get("coder")
    assert role.caps == ["python", "typescript"]

def test_all_agents_get_report_scope(registry):
    role = registry.get("coder")
    assert "agent:report" in role.scopes

def test_gateway_config_parsed(registry):
    cfg = registry.gateway_config
    assert cfg.policy_engine == "local"
    assert cfg.memory_backend == "sqlite"
    assert cfg.dispatcher == "local"
    assert cfg.gateway_bootstrap_token == "testtoken"
    assert cfg.gateway_heartbeat_ttl == 30

def test_existing_roles_still_load(registry):
    roles = registry.all()
    assert len(roles) == 2
    assert registry.get("orchestrator") is not None
    assert registry.get("coder") is not None
