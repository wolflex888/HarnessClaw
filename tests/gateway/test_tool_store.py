import pytest
from datetime import datetime, timezone
from harness_claw.gateway.tool_store import ToolDef, SqliteToolStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_tool(id: str = "run_tests") -> ToolDef:
    now = _now()
    return ToolDef(
        id=id,
        name="Run Tests",
        description="Execute the test suite",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        output_schema={"type": "object", "properties": {"exit_code": {"type": "integer"}}},
        adapter="cli",
        adapter_config={"command": "pytest {{path}}", "timeout": 30},
        cap_allowlist=["python", "testing"],
        allowed_roles=["code-writer"],
        required_scopes=[],
        metadata={},
        created_at=now,
        updated_at=now,
    )


def test_save_and_get(tmp_path):
    store = SqliteToolStore(tmp_path / "tools.db")
    tool = make_tool("run_tests")
    store.save(tool)
    result = store.get("run_tests")
    assert result is not None
    assert result.id == "run_tests"
    assert result.name == "Run Tests"
    assert result.input_schema == tool.input_schema
    assert result.cap_allowlist == ["python", "testing"]
    assert result.adapter_config == {"command": "pytest {{path}}", "timeout": 30}


def test_upsert_updates_fields(tmp_path):
    store = SqliteToolStore(tmp_path / "tools.db")
    store.save(make_tool("run_tests"))
    updated = make_tool("run_tests")
    updated.description = "Updated description"
    store.save(updated)
    result = store.get("run_tests")
    assert result.description == "Updated description"


def test_upsert_preserves_created_at(tmp_path):
    store = SqliteToolStore(tmp_path / "tools.db")
    original = make_tool("run_tests")
    store.save(original)
    updated = make_tool("run_tests")
    updated.description = "new"
    store.save(updated)
    result = store.get("run_tests")
    assert result.created_at == original.created_at


def test_list_returns_all(tmp_path):
    store = SqliteToolStore(tmp_path / "tools.db")
    store.save(make_tool("tool_a"))
    store.save(make_tool("tool_b"))
    tools = store.list()
    assert len(tools) == 2
    assert {t.id for t in tools} == {"tool_a", "tool_b"}


def test_delete_removes_tool(tmp_path):
    store = SqliteToolStore(tmp_path / "tools.db")
    store.save(make_tool("run_tests"))
    assert store.delete("run_tests") is True
    assert store.get("run_tests") is None


def test_delete_nonexistent_returns_false(tmp_path):
    store = SqliteToolStore(tmp_path / "tools.db")
    assert store.delete("nonexistent") is False


def test_get_nonexistent_returns_none(tmp_path):
    store = SqliteToolStore(tmp_path / "tools.db")
    assert store.get("nonexistent") is None


def test_to_dict_round_trips(tmp_path):
    store = SqliteToolStore(tmp_path / "tools.db")
    tool = make_tool("run_tests")
    store.save(tool)
    result = store.get("run_tests")
    d = result.to_dict()
    assert d["id"] == "run_tests"
    assert d["input_schema"] == tool.input_schema
    assert d["cap_allowlist"] == ["python", "testing"]
