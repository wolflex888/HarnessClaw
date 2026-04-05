import pytest
from datetime import datetime, timezone
from harness_claw.gateway.tool_store import SqliteToolStore, ToolDef
from harness_claw.gateway.tool_registry import ToolRegistry
from harness_claw.role_registry import RoleConfig


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_tool(
    id: str,
    cap_allowlist: list[str],
    allowed_roles: list[str] = [],
    required_scopes: list[str] = [],
) -> ToolDef:
    now = _now()
    return ToolDef(
        id=id, name=id, description="desc",
        input_schema={}, output_schema={},
        adapter="cli", adapter_config={"command": "echo"},
        cap_allowlist=cap_allowlist,
        allowed_roles=allowed_roles,
        required_scopes=required_scopes,
        metadata={}, created_at=now, updated_at=now,
    )


def make_role(id: str, caps: list[str], scopes: list[str] = []) -> RoleConfig:
    return RoleConfig(
        id=id, name=id, provider="claude-code",
        model="claude-sonnet-4-6", system_prompt="",
        caps=caps, scopes=scopes,
    )


def make_registry(tmp_path) -> ToolRegistry:
    store = SqliteToolStore(tmp_path / "tools.db")
    return ToolRegistry(store)


def test_visible_when_all_gates_pass(tmp_path):
    reg = make_registry(tmp_path)
    reg.save(make_tool("t1", cap_allowlist=["python"], allowed_roles=["coder"], required_scopes=["tool:use"]))
    role = make_role("coder", caps=["python"], scopes=["tool:use"])
    assert len(reg.get_visible_tools(role)) == 1


def test_hidden_when_cap_not_in_allowlist(tmp_path):
    reg = make_registry(tmp_path)
    reg.save(make_tool("t1", cap_allowlist=["python"]))
    role = make_role("coder", caps=["typescript"])
    assert reg.get_visible_tools(role) == []


def test_hidden_when_role_not_in_allowed_roles(tmp_path):
    reg = make_registry(tmp_path)
    reg.save(make_tool("t1", cap_allowlist=["python"], allowed_roles=["coder"]))
    role = make_role("reviewer", caps=["python"])
    assert reg.get_visible_tools(role) == []


def test_visible_when_allowed_roles_is_empty(tmp_path):
    reg = make_registry(tmp_path)
    # empty allowed_roles = all roles pass
    reg.save(make_tool("t1", cap_allowlist=["python"], allowed_roles=[]))
    role = make_role("any-role", caps=["python"])
    assert len(reg.get_visible_tools(role)) == 1


def test_hidden_when_scope_missing(tmp_path):
    reg = make_registry(tmp_path)
    reg.save(make_tool("t1", cap_allowlist=["python"], required_scopes=["tool:use"]))
    role = make_role("coder", caps=["python"], scopes=[])
    assert reg.get_visible_tools(role) == []


def test_empty_cap_allowlist_hides_from_everyone(tmp_path):
    reg = make_registry(tmp_path)
    reg.save(make_tool("t1", cap_allowlist=[]))
    role = make_role("coder", caps=["python", "testing"])
    assert reg.get_visible_tools(role) == []


def test_get_returns_none_for_missing(tmp_path):
    reg = make_registry(tmp_path)
    assert reg.get("nonexistent") is None


def test_delete_delegates_to_store(tmp_path):
    reg = make_registry(tmp_path)
    reg.save(make_tool("t1", cap_allowlist=["python"]))
    assert reg.delete("t1") is True
    assert reg.get("t1") is None
