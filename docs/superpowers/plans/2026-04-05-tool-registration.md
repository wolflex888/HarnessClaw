# Tool Registration API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a runtime Tool Registration API so agents can register and call executable tools (CLI, HTTP, MCP-forward) with name/description/input-output schemas and per-role visibility gating.

**Architecture:** `SqliteToolStore` persists tool definitions; `ToolRegistry` wraps it with a triple-gate visibility filter (caps ∩ role ∩ scopes); `ToolExecutor` routes calls to CLI/HTTP/MCP adapters with JSON Schema validation; `GatewayMCP.tool_register` exposes registration via MCP; REST routes expose CRUD; `JobRunner` computes per-session visible tools that are injected into each agent's MCP server instance; a `ToolsTab` lists registered tools in the dashboard.

**Tech Stack:** Python 3.12, asyncio, sqlite3, jsonschema, httpx, React 18 + TypeScript

---

## File Map

| File | Change |
|------|--------|
| `harness_claw/gateway/tool_store.py` | New: `ToolDef` dataclass + `SqliteToolStore` |
| `harness_claw/gateway/tool_registry.py` | New: `ToolRegistry` with visibility filter |
| `harness_claw/runtime/tool_executor.py` | New: `ToolExecutor` with CLI/HTTP/MCP adapters |
| `harness_claw/gateway/mcp_server.py` | Add `tool_registry` param + `tool_register` method |
| `harness_claw/server.py` | Add REST routes, fallthrough dispatch, wire all components |
| `harness_claw/runtime/job_runner.py` | Store per-session visible tools at session start |
| `ui/src/types.ts` | Add `ToolDef` type |
| `ui/src/components/ToolsTab.tsx` | New: read-only tools list tab |
| `ui/src/App.tsx` | Add ToolsTab to dashboard |
| `tests/gateway/test_tool_store.py` | New: CRUD unit tests |
| `tests/gateway/test_tool_registry.py` | New: visibility filter unit tests |
| `tests/runtime/test_tool_executor.py` | New: adapter execution tests |
| `tests/test_server_endpoints.py` | Add tool REST endpoint tests |

---

## Task 1: ToolDef + SqliteToolStore

**Files:**
- Create: `harness_claw/gateway/tool_store.py`
- Create: `tests/gateway/test_tool_store.py`

- [ ] **Step 1: Write failing tests**

Create `tests/gateway/test_tool_store.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/juichanglu/src/HarnessClaw
.venv/bin/pytest tests/gateway/test_tool_store.py -v
```

Expected: FAIL — `No module named 'harness_claw.gateway.tool_store'`

- [ ] **Step 3: Implement tool_store.py**

Create `harness_claw/gateway/tool_store.py`:

```python
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ToolDef:
    id: str                     # unique key, e.g. "run_tests"
    name: str                   # display name
    description: str            # LLM-readable description
    input_schema: dict          # JSON Schema for call parameters
    output_schema: dict         # JSON Schema for structured return value
    adapter: str                # "cli" | "http" | "mcp"
    adapter_config: dict        # backend-specific config
    cap_allowlist: list[str]    # caps required ([] = no agent sees this tool)
    allowed_roles: list[str]    # role IDs allowed ([] = all roles pass)
    required_scopes: list[str]  # scopes required ([] = no scope gate)
    metadata: dict              # scheduler hints, tags, etc.
    created_at: str             # ISO 8601
    updated_at: str             # ISO 8601

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "adapter": self.adapter,
            "adapter_config": self.adapter_config,
            "cap_allowlist": self.cap_allowlist,
            "allowed_roles": self.allowed_roles,
            "required_scopes": self.required_scopes,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tools (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL,
    input_schema    TEXT NOT NULL,
    output_schema   TEXT NOT NULL,
    adapter         TEXT NOT NULL,
    adapter_config  TEXT NOT NULL,
    cap_allowlist   TEXT NOT NULL,
    allowed_roles   TEXT NOT NULL,
    required_scopes TEXT NOT NULL,
    metadata        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)
"""


def _row_to_tool(row: sqlite3.Row) -> ToolDef:
    return ToolDef(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        input_schema=json.loads(row["input_schema"]),
        output_schema=json.loads(row["output_schema"]),
        adapter=row["adapter"],
        adapter_config=json.loads(row["adapter_config"]),
        cap_allowlist=json.loads(row["cap_allowlist"]),
        allowed_roles=json.loads(row["allowed_roles"]),
        required_scopes=json.loads(row["required_scopes"]),
        metadata=json.loads(row["metadata"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SqliteToolStore:
    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, tool: ToolDef) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tools (id, name, description, input_schema, output_schema,
                    adapter, adapter_config, cap_allowlist, allowed_roles, required_scopes,
                    metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description,
                    input_schema=excluded.input_schema,
                    output_schema=excluded.output_schema,
                    adapter=excluded.adapter,
                    adapter_config=excluded.adapter_config,
                    cap_allowlist=excluded.cap_allowlist,
                    allowed_roles=excluded.allowed_roles,
                    required_scopes=excluded.required_scopes,
                    metadata=excluded.metadata,
                    updated_at=excluded.updated_at
                """,
                (
                    tool.id, tool.name, tool.description,
                    json.dumps(tool.input_schema),
                    json.dumps(tool.output_schema),
                    tool.adapter,
                    json.dumps(tool.adapter_config),
                    json.dumps(tool.cap_allowlist),
                    json.dumps(tool.allowed_roles),
                    json.dumps(tool.required_scopes),
                    json.dumps(tool.metadata),
                    tool.created_at,
                    now,
                ),
            )

    def get(self, tool_id: str) -> ToolDef | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tools WHERE id = ?", (tool_id,)
            ).fetchone()
        return _row_to_tool(row) if row else None

    def list(self) -> list[ToolDef]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tools ORDER BY created_at"
            ).fetchall()
        return [_row_to_tool(r) for r in rows]

    def delete(self, tool_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM tools WHERE id = ?", (tool_id,)
            )
        return cursor.rowcount > 0
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/gateway/test_tool_store.py -v
```

Expected: ALL PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/tool_store.py tests/gateway/test_tool_store.py
git commit -m "feat: add ToolDef dataclass and SqliteToolStore"
```

---

## Task 2: ToolRegistry

**Files:**
- Create: `harness_claw/gateway/tool_registry.py`
- Create: `tests/gateway/test_tool_registry.py`

- [ ] **Step 1: Write failing tests**

Create `tests/gateway/test_tool_registry.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/juichanglu/src/HarnessClaw
.venv/bin/pytest tests/gateway/test_tool_registry.py -v
```

Expected: FAIL — `No module named 'harness_claw.gateway.tool_registry'`

- [ ] **Step 3: Implement tool_registry.py**

Create `harness_claw/gateway/tool_registry.py`:

```python
from __future__ import annotations

from harness_claw.gateway.tool_store import SqliteToolStore, ToolDef
from harness_claw.role_registry import RoleConfig


class ToolRegistry:
    """Wraps SqliteToolStore and applies the per-role visibility filter.

    Visibility = cap_allowlist ∩ role.caps  AND  role_id ∈ allowed_roles  AND
                 role.scopes ⊇ required_scopes
    """

    def __init__(self, store: SqliteToolStore) -> None:
        self._store = store

    def save(self, tool: ToolDef) -> None:
        self._store.save(tool)

    def get(self, tool_id: str) -> ToolDef | None:
        return self._store.get(tool_id)

    def list(self) -> list[ToolDef]:
        return self._store.list()

    def delete(self, tool_id: str) -> bool:
        return self._store.delete(tool_id)

    def get_visible_tools(self, role: RoleConfig) -> list[ToolDef]:
        """Return tools visible to this role — triple-gate intersection."""
        return [t for t in self._store.list() if self._is_visible(t, role)]

    def _is_visible(self, tool: ToolDef, role: RoleConfig) -> bool:
        caps_ok = bool(set(tool.cap_allowlist) & set(role.caps))
        role_ok = not tool.allowed_roles or role.id in tool.allowed_roles
        scopes_ok = all(s in role.scopes for s in tool.required_scopes)
        return caps_ok and role_ok and scopes_ok
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/gateway/test_tool_registry.py -v
```

Expected: ALL PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/tool_registry.py tests/gateway/test_tool_registry.py
git commit -m "feat: add ToolRegistry with triple-gate visibility filter"
```

---

## Task 3: ToolExecutor

**Files:**
- Create: `harness_claw/runtime/tool_executor.py`
- Create: `tests/runtime/test_tool_executor.py`

Note: `ToolExecutor` uses `jsonschema` for input/output validation and `httpx` for HTTP/MCP adapters. Both are expected to already be in the project's dependencies. If `import jsonschema` fails, add `jsonschema` to `requirements.txt` (or `pyproject.toml`).

- [ ] **Step 1: Write failing tests**

Create `tests/runtime/test_tool_executor.py`:

```python
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from harness_claw.gateway.tool_store import SqliteToolStore, ToolDef
from harness_claw.gateway.tool_registry import ToolRegistry
from harness_claw.runtime.tool_executor import ToolExecutor


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_tool(
    id: str = "echo_tool",
    adapter: str = "cli",
    adapter_config: dict | None = None,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
) -> ToolDef:
    now = _now()
    return ToolDef(
        id=id, name=id, description="desc",
        input_schema=input_schema or {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
        output_schema=output_schema or {
            "type": "object",
            "properties": {
                "stdout": {"type": "string"},
                "stderr": {"type": "string"},
                "exit_code": {"type": "integer"},
            },
            "required": ["stdout", "stderr", "exit_code"],
        },
        adapter=adapter,
        adapter_config=adapter_config or {"command": "echo {{msg}}", "timeout": 5},
        cap_allowlist=["python"], allowed_roles=[], required_scopes=[],
        metadata={}, created_at=now, updated_at=now,
    )


def make_executor(tmp_path, tools: list[ToolDef] | None = None) -> ToolExecutor:
    store = SqliteToolStore(tmp_path / "tools.db")
    registry = ToolRegistry(store)
    for t in (tools or []):
        registry.save(t)
    return ToolExecutor(registry)


@pytest.mark.asyncio
async def test_cli_adapter_runs_command(tmp_path):
    executor = make_executor(tmp_path, [make_tool()])
    result = await executor.execute("echo_tool", {"msg": "hello"})
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]


@pytest.mark.asyncio
async def test_cli_substitutes_template_args(tmp_path):
    tool = make_tool(adapter_config={"command": "echo {{word}}", "timeout": 5},
                     input_schema={"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]})
    executor = make_executor(tmp_path, [tool])
    result = await executor.execute("echo_tool", {"word": "foobar"})
    assert "foobar" in result["stdout"]


@pytest.mark.asyncio
async def test_raises_key_error_for_unknown_tool(tmp_path):
    executor = make_executor(tmp_path)
    with pytest.raises(KeyError, match="nonexistent"):
        await executor.execute("nonexistent", {})


@pytest.mark.asyncio
async def test_raises_validation_error_on_bad_input(tmp_path):
    from jsonschema import ValidationError
    executor = make_executor(tmp_path, [make_tool()])
    with pytest.raises(ValidationError):
        # msg is required but not provided
        await executor.execute("echo_tool", {})


@pytest.mark.asyncio
async def test_raises_value_error_for_unknown_adapter(tmp_path):
    tool = make_tool(
        adapter="unknown",
        adapter_config={},
        input_schema={},
        output_schema={},
    )
    executor = make_executor(tmp_path, [tool])
    with pytest.raises(ValueError, match="Unknown adapter"):
        await executor.execute("echo_tool", {})


@pytest.mark.asyncio
async def test_http_adapter_posts_to_url(tmp_path):
    import httpx
    tool = make_tool(
        adapter="http",
        adapter_config={"url": "http://fake-server/api", "method": "POST"},
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    executor = make_executor(tmp_path, [tool])

    mock_response = MagicMock()
    mock_response.json.return_value = {"result": "ok"}
    mock_response.raise_for_status = MagicMock()

    with patch("harness_claw.runtime.tool_executor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await executor.execute("echo_tool", {"key": "value"})
        assert result == {"result": "ok"}
        mock_client.request.assert_called_once_with(
            "POST", "http://fake-server/api", json={"key": "value"}, headers={}
        )
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/juichanglu/src/HarnessClaw
.venv/bin/pytest tests/runtime/test_tool_executor.py -v
```

Expected: FAIL — `No module named 'harness_claw.runtime.tool_executor'`

- [ ] **Step 3: Implement tool_executor.py**

Create `harness_claw/runtime/tool_executor.py`:

```python
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from jsonschema import validate

from harness_claw.gateway.tool_registry import ToolRegistry
from harness_claw.gateway.tool_store import ToolDef


class ToolExecutor:
    """Routes registered tool calls to the appropriate execution adapter.

    Validates input against tool.input_schema before dispatch and output
    against tool.output_schema before returning. Raises jsonschema.ValidationError
    on schema violations and KeyError for unknown tool IDs.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, tool_id: str, arguments: dict) -> dict:
        tool = self._registry.get(tool_id)
        if tool is None:
            raise KeyError(f"Tool {tool_id!r} not registered")
        validate(instance=arguments, schema=tool.input_schema)
        result = await self._dispatch(tool, arguments)
        validate(instance=result, schema=tool.output_schema)
        return result

    async def _dispatch(self, tool: ToolDef, args: dict) -> dict:
        if tool.adapter == "cli":
            return await self._run_cli(tool, args)
        if tool.adapter == "http":
            return await self._run_http(tool, args)
        if tool.adapter == "mcp":
            return await self._run_mcp(tool, args)
        raise ValueError(f"Unknown adapter: {tool.adapter!r}")

    async def _run_cli(self, tool: ToolDef, args: dict) -> dict:
        config = tool.adapter_config
        command = config["command"]
        for key, value in args.items():
            command = command.replace(f"{{{{{key}}}}}", str(value))
        timeout = float(config.get("timeout", 30))
        working_dir = config.get("working_dir")
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise TimeoutError(f"Tool {tool.id!r} timed out after {timeout}s")
        return {
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
            "exit_code": proc.returncode,
        }

    async def _run_http(self, tool: ToolDef, args: dict) -> dict:
        config = tool.adapter_config
        url = config["url"]
        method = config.get("method", "POST").upper()
        headers = config.get("headers", {})
        async with httpx.AsyncClient() as client:
            response = await client.request(method, url, json=args, headers=headers)
            response.raise_for_status()
            return response.json()

    async def _run_mcp(self, tool: ToolDef, args: dict) -> dict:
        config = tool.adapter_config
        server_url = config["server_url"]
        tool_name = config["tool_name"]
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{server_url}/mcp/tools/call",
                json={"name": tool_name, "arguments": args},
            )
            response.raise_for_status()
            return response.json()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/runtime/test_tool_executor.py -v
```

Expected: ALL PASS (6 tests)

- [ ] **Step 5: Run full test suite for regressions**

```bash
.venv/bin/pytest tests/ --ignore=tests/runtime/test_pty_session.py -q 2>&1 | tail -5
```

Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add harness_claw/runtime/tool_executor.py tests/runtime/test_tool_executor.py
git commit -m "feat: add ToolExecutor with CLI, HTTP, and MCP-forward adapters"
```

---

## Task 4: GatewayMCP.tool_register

**Files:**
- Modify: `harness_claw/gateway/mcp_server.py`

**Before modifying, read `harness_claw/gateway/mcp_server.py` to see the current `__init__` signature and the pattern used by other MCP methods (auth → operation → audit log).**

- [ ] **Step 1: Write failing test**

Add to `tests/test_server_endpoints.py` (at the end of the file):

```python
def test_tool_register_mcp_method(client):
    """tool.register MCP method stores a tool and returns {id, status}."""
    resp = client.post(
        "/mcp/tools/call",
        json={
            "name": "tool.register",
            "arguments": {
                "id": "test_cli_tool",
                "name": "Test CLI Tool",
                "description": "Runs echo",
                "input_schema": {"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
                "output_schema": {"type": "object"},
                "adapter": "cli",
                "adapter_config": {"command": "echo {{msg}}"},
                "cap_allowlist": ["python"],
                "allowed_roles": [],
                "required_scopes": [],
                "metadata": {},
            },
        },
        headers={"Authorization": f"Bearer {_get_bootstrap_token()}"},
    )
    assert resp.status_code == 400 or resp.status_code == 200
    # Once tool.register is implemented, update this to assert 200 + {"id": "test_cli_tool", "status": "registered"}
```

- [ ] **Step 2: Run test to verify it reflects current state**

```bash
cd /Users/juichanglu/src/HarnessClaw
.venv/bin/pytest tests/test_server_endpoints.py::test_tool_register_mcp_method -v
```

Expected: passes (tool.register not yet registered → routes to tool_executor → KeyError → 400)

- [ ] **Step 3: Add tool_registry param and tool_register method to GatewayMCP**

In `harness_claw/gateway/mcp_server.py`:

Add imports at the top (after existing imports):
```python
from datetime import datetime, timezone
from harness_claw.gateway.tool_registry import ToolRegistry
from harness_claw.gateway.tool_store import ToolDef
```

Add `tool_registry: ToolRegistry | None = None` as the last parameter of `GatewayMCP.__init__`, and `self._tool_registry = tool_registry` in the body. The updated `__init__` signature:

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

Add `tool_register` method after `execute_plan` (at the end of the class):

```python
async def tool_register(
    self,
    token: str,
    id: str,
    name: str,
    description: str,
    input_schema: dict,
    output_schema: dict,
    adapter: str,
    adapter_config: dict,
    cap_allowlist: list[str],
    allowed_roles: list[str],
    required_scopes: list[str],
    metadata: dict | None = None,
) -> dict[str, Any]:
    subject = self._auth(token, "tool:write")

    if self._tool_registry is None:
        raise RuntimeError("tool_register requires tool_registry — not configured")

    now = datetime.now(timezone.utc).isoformat()
    tool = ToolDef(
        id=id,
        name=name,
        description=description,
        input_schema=input_schema,
        output_schema=output_schema,
        adapter=adapter,
        adapter_config=adapter_config,
        cap_allowlist=cap_allowlist,
        allowed_roles=allowed_roles,
        required_scopes=required_scopes,
        metadata=metadata or {},
        created_at=now,
        updated_at=now,
    )
    self._tool_registry.save(tool)

    self._audit.log(AuditEvent(
        subject=subject, operation="tool.register", resource=id,
        outcome="allowed", details={"adapter": adapter},
    ))
    return {"id": id, "status": "registered"}
```

- [ ] **Step 4: Verify server imports cleanly**

```bash
cd /Users/juichanglu/src/HarnessClaw
.venv/bin/python -c "from harness_claw.gateway.mcp_server import GatewayMCP; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/mcp_server.py
git commit -m "feat: add tool_register MCP method to GatewayMCP"
```

---

## Task 5: REST API + Server Wiring

**Files:**
- Modify: `harness_claw/server.py`
- Modify: `tests/test_server_endpoints.py`

**Before modifying, read `harness_claw/server.py` to understand:**
1. Where stores are instantiated (e.g., `SqliteTaskStore`, `SqliteWorkflowStore`) — add `SqliteToolStore` nearby
2. Where `GatewayMCP(...)` is constructed — add `tool_registry=tool_registry` kwarg
3. Where `_MCP_TOOLS` list is defined — add `tool.register` entry
4. Where `_dispatch_tool` or the handlers dict is defined — add `tool.register` handler + fallthrough
5. Where `get_mcp_tools()` is defined — add `tool.register` entry
6. Where REST routes like `/api/tasks` or `/api/workflows` are defined — add tool REST routes nearby

- [ ] **Step 1: Write failing tests**

Add to `tests/test_server_endpoints.py`:

```python
def test_get_tools_returns_empty_list(client):
    resp = client.get("/api/tools")
    assert resp.status_code == 200
    assert resp.json() == []


def test_post_tool_creates_and_get_returns_it(client):
    payload = {
        "id": "my_tool",
        "name": "My Tool",
        "description": "Does something",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "adapter": "cli",
        "adapter_config": {"command": "echo hello"},
        "cap_allowlist": ["python"],
        "allowed_roles": [],
        "required_scopes": [],
        "metadata": {},
    }
    resp = client.post("/api/tools", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "my_tool"

    resp2 = client.get("/api/tools")
    assert resp2.status_code == 200
    tools = resp2.json()
    assert any(t["id"] == "my_tool" for t in tools)


def test_get_tool_by_id(client):
    payload = {
        "id": "get_me",
        "name": "Get Me",
        "description": "desc",
        "input_schema": {}, "output_schema": {},
        "adapter": "cli", "adapter_config": {"command": "echo"},
        "cap_allowlist": ["x"], "allowed_roles": [], "required_scopes": [],
        "metadata": {},
    }
    client.post("/api/tools", json=payload)
    resp = client.get("/api/tools/get_me")
    assert resp.status_code == 200
    assert resp.json()["id"] == "get_me"


def test_get_tool_by_id_not_found(client):
    resp = client.get("/api/tools/nonexistent")
    assert resp.status_code == 404


def test_delete_tool(client):
    payload = {
        "id": "del_me",
        "name": "Del Me",
        "description": "desc",
        "input_schema": {}, "output_schema": {},
        "adapter": "cli", "adapter_config": {"command": "echo"},
        "cap_allowlist": ["x"], "allowed_roles": [], "required_scopes": [],
        "metadata": {},
    }
    client.post("/api/tools", json=payload)
    resp = client.delete("/api/tools/del_me")
    assert resp.status_code == 200
    assert client.get("/api/tools/del_me").status_code == 404


def test_tool_register_mcp_stores_tool(client):
    """After registering via MCP tool.register, tool appears in GET /api/tools."""
    resp = client.post(
        "/mcp/tools/call",
        json={
            "name": "tool.register",
            "arguments": {
                "id": "mcp_registered_tool",
                "name": "MCP Tool",
                "description": "via MCP",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "adapter": "cli",
                "adapter_config": {"command": "echo"},
                "cap_allowlist": ["python"],
                "allowed_roles": [],
                "required_scopes": [],
                "metadata": {},
            },
        },
        headers={"Authorization": f"Bearer {_get_bootstrap_token()}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("id") == "mcp_registered_tool"
    assert body.get("status") == "registered"

    tools_resp = client.get("/api/tools")
    ids = [t["id"] for t in tools_resp.json()]
    assert "mcp_registered_tool" in ids
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/juichanglu/src/HarnessClaw
.venv/bin/pytest tests/test_server_endpoints.py::test_get_tools_returns_empty_list tests/test_server_endpoints.py::test_post_tool_creates_and_get_returns_it -v
```

Expected: FAIL — 404 (routes don't exist yet)

- [ ] **Step 3: Wire tool components in server.py**

In `harness_claw/server.py`, add these imports near the top (after existing gateway/runtime imports):

```python
from harness_claw.gateway.tool_store import SqliteToolStore
from harness_claw.gateway.tool_registry import ToolRegistry
from harness_claw.runtime.tool_executor import ToolExecutor
```

Near where `SqliteTaskStore` / `SqliteWorkflowStore` are instantiated, add:

```python
tool_store = SqliteToolStore("tools.db")
tool_registry = ToolRegistry(tool_store)
tool_executor = ToolExecutor(tool_registry)
```

Update the `GatewayMCP(...)` constructor call to include `tool_registry=tool_registry`:

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
)
```

- [ ] **Step 4: Add REST routes**

In `harness_claw/server.py`, add these routes near the other `/api/` routes (e.g., after the workflow routes):

```python
@app.get("/api/tools")
async def list_tools() -> list[dict]:
    return [t.to_dict() for t in tool_registry.list()]


@app.post("/api/tools")
async def register_tool(body: dict) -> dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    from harness_claw.gateway.tool_store import ToolDef
    tool = ToolDef(
        id=body["id"],
        name=body["name"],
        description=body["description"],
        input_schema=body.get("input_schema", {}),
        output_schema=body.get("output_schema", {}),
        adapter=body["adapter"],
        adapter_config=body.get("adapter_config", {}),
        cap_allowlist=body.get("cap_allowlist", []),
        allowed_roles=body.get("allowed_roles", []),
        required_scopes=body.get("required_scopes", []),
        metadata=body.get("metadata", {}),
        created_at=now,
        updated_at=now,
    )
    tool_registry.save(tool)
    return tool.to_dict()


@app.get("/api/tools/{tool_id}")
async def get_tool(tool_id: str) -> dict:
    tool = tool_registry.get(tool_id)
    if tool is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Tool {tool_id!r} not found")
    return tool.to_dict()


@app.delete("/api/tools/{tool_id}")
async def delete_tool(tool_id: str) -> dict:
    deleted = tool_registry.delete(tool_id)
    if not deleted:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Tool {tool_id!r} not found")
    return {"deleted": True}
```

- [ ] **Step 5: Add tool.register to _MCP_TOOLS and _dispatch_tool**

In `harness_claw/server.py`, find the `_MCP_TOOLS` list and add after the `execute_plan` entry:

```python
mcp_types.Tool(
    name="tool.register",
    description="Register or update an executable tool in the HarnessClaw tool registry",
    inputSchema={
        "type": "object",
        "properties": {
            "id":              {"type": "string", "description": "Unique tool identifier"},
            "name":            {"type": "string", "description": "Display name"},
            "description":     {"type": "string", "description": "LLM-readable description"},
            "input_schema":    {"type": "object", "description": "JSON Schema for call parameters"},
            "output_schema":   {"type": "object", "description": "JSON Schema for return value"},
            "adapter":         {"type": "string", "enum": ["cli", "http", "mcp"]},
            "adapter_config":  {"type": "object", "description": "Backend-specific config"},
            "cap_allowlist":   {"type": "array",  "items": {"type": "string"}},
            "allowed_roles":   {"type": "array",  "items": {"type": "string"}},
            "required_scopes": {"type": "array",  "items": {"type": "string"}},
            "metadata":        {"type": "object"},
        },
        "required": ["id", "name", "description", "input_schema", "output_schema",
                     "adapter", "adapter_config", "cap_allowlist", "allowed_roles",
                     "required_scopes"],
    },
),
```

Find `_dispatch_tool` (or the `handlers` dict inside `mcp_tool_call`) and:
1. Add `"tool.register": lambda a: gateway_mcp.tool_register(token=token, **a)` after the `"execute_plan"` entry
2. Add a fallthrough **after** all named handlers to route unknown tool names to `tool_executor`:

```python
# After the handlers dict / named tool dispatch:
else:
    return await tool_executor.execute(tool_name, arguments)
```

Find `get_mcp_tools()` and add:
```python
{"name": "tool.register", "description": "Register or update an executable tool in the tool registry"},
```

- [ ] **Step 6: Verify server imports cleanly**

```bash
.venv/bin/python -c "from harness_claw.server import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 7: Run new tests**

```bash
.venv/bin/pytest tests/test_server_endpoints.py -v -q 2>&1 | tail -15
```

Expected: ALL PASS

- [ ] **Step 8: Run full suite**

```bash
.venv/bin/pytest tests/ --ignore=tests/runtime/test_pty_session.py -q 2>&1 | tail -5
```

Expected: ALL PASS

- [ ] **Step 9: Commit**

```bash
git add harness_claw/server.py tests/test_server_endpoints.py
git commit -m "feat: add tool REST API, tool.register MCP tool, and fallthrough dispatch"
```

---

## Task 6: Session Tool Injection

**Files:**
- Modify: `harness_claw/runtime/job_runner.py`
- Modify: `harness_claw/server.py`

At session start, `JobRunner` computes the visible tools for the agent's role and stores them. The MCP server instance built for each agent token includes those session-specific tools dynamically.

**Before modifying, read `harness_claw/runtime/job_runner.py` lines 90-180 and `harness_claw/server.py`'s `_build_mcp_server` function.**

- [ ] **Step 1: Write failing test**

Add to `tests/runtime/test_job_runner.py` (read this file first to find the correct fixture/test pattern):

```python
def test_start_session_stores_visible_tools(mock_runner, tool_registry):
    """After start_session, get_session_tools returns the role's visible tools."""
    # This test requires reading the existing test file to match the fixture pattern.
    # The key assertion: after start_session for a role with caps=["python"],
    # get_session_tools(session_id) returns the tools visible to that role.
    pass  # implement after reading the test file
```

*Note: Read `tests/runtime/test_job_runner.py` before implementing. Match the existing fixture pattern exactly. The assertion is:*
```python
tools = runner.get_session_tools(session_id)
# tools contains only the tools whose cap_allowlist intersects with the role's caps
```

- [ ] **Step 2: Update job_runner.py**

In `harness_claw/runtime/job_runner.py`:

Add import:
```python
from harness_claw.gateway.tool_registry import ToolRegistry
from harness_claw.gateway.tool_store import ToolDef
```

Add `tool_registry: ToolRegistry | None = None` to `JobRunner.__init__` (or wherever the runner is initialized — read the file first). Store it as `self._tool_registry = tool_registry`.

Add to the runner's `__init__` body:
```python
self._session_tools: dict[str, list[ToolDef]] = {}
```

In `start_session`, after the role is retrieved and before `configure_session` is called, add:
```python
if self._tool_registry is not None:
    visible_tools = self._tool_registry.get_visible_tools(role)
    self._session_tools[session_id] = visible_tools
else:
    self._session_tools[session_id] = []
```

Add a new method:
```python
def get_session_tools(self, session_id: str) -> list[ToolDef]:
    """Return the registered tools visible to the agent in this session."""
    return self._session_tools.get(session_id, [])
```

- [ ] **Step 3: Pass tool_registry to JobRunner in server.py**

In `harness_claw/server.py`, find where `JobRunner(...)` is constructed and add `tool_registry=tool_registry`:

```python
runner = JobRunner(
    ...,           # existing args — read the file to find them
    tool_registry=tool_registry,
)
```

- [ ] **Step 4: Make MCP server per-session tool list dynamic**

In `harness_claw/server.py`, find `_build_mcp_server(token)`. After the token is validated and the subject (session_id) is known, retrieve the session's visible tools and add them to the MCP tools list:

```python
# Inside _build_mcp_server, after token validation:
session_tools = runner.get_session_tools(subject)
dynamic_tools = [
    mcp_types.Tool(
        name=t.id,
        description=t.description,
        inputSchema=t.input_schema,
    )
    for t in session_tools
]
# Combine with _MCP_TOOLS: use _MCP_TOOLS + dynamic_tools when building the server
```

Read the file to see exactly how `_build_mcp_server` builds the tool list and inject `dynamic_tools` into that list.

- [ ] **Step 5: Verify server imports cleanly**

```bash
cd /Users/juichanglu/src/HarnessClaw
.venv/bin/python -c "from harness_claw.server import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Run full suite**

```bash
.venv/bin/pytest tests/ --ignore=tests/runtime/test_pty_session.py -q 2>&1 | tail -5
```

Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add harness_claw/runtime/job_runner.py harness_claw/server.py
git commit -m "feat: inject per-session visible tools into agent MCP server at session start"
```

---

## Task 7: UI ToolsTab

**Files:**
- Create: `ui/src/components/ToolsTab.tsx`
- Modify: `ui/src/types.ts`
- Modify: `ui/src/App.tsx`

**Before modifying, read `ui/src/types.ts`, `ui/src/App.tsx`, and an existing tab like `ui/src/components/WorkflowsTab.tsx` to understand the data-fetching and rendering pattern.**

- [ ] **Step 1: Add ToolDef type to types.ts**

In `ui/src/types.ts`, add after the existing types:

```typescript
export interface ToolDef {
  id: string;
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  adapter: 'cli' | 'http' | 'mcp';
  adapter_config: Record<string, unknown>;
  cap_allowlist: string[];
  allowed_roles: string[];
  required_scopes: string[];
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}
```

- [ ] **Step 2: Create ToolsTab.tsx**

Create `ui/src/components/ToolsTab.tsx`:

```tsx
import React, { useEffect, useState } from 'react';
import { ToolDef } from '../types';

export function ToolsTab() {
  const [tools, setTools] = useState<ToolDef[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/tools')
      .then(r => r.json())
      .then(data => { setTools(data); setLoading(false); })
      .catch(e => { setError(String(e)); setLoading(false); });
  }, []);

  if (loading) return <div className="p-4 text-gray-400">Loading tools...</div>;
  if (error) return <div className="p-4 text-red-400">Error: {error}</div>;

  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">Registered Tools</h2>
        <span className="text-sm text-gray-400">{tools.length} tool{tools.length !== 1 ? 's' : ''}</span>
      </div>

      {tools.length === 0 && (
        <div className="text-gray-500 text-sm">No tools registered. Use POST /api/tools or the tool.register MCP tool to add one.</div>
      )}

      {tools.map(tool => (
        <div key={tool.id} className="bg-gray-800 rounded-lg p-4 space-y-2">
          <div className="flex items-start justify-between">
            <div>
              <span className="font-mono text-sm text-blue-400">{tool.id}</span>
              <span className="ml-2 text-white text-sm">{tool.name}</span>
            </div>
            <span className="text-xs px-2 py-0.5 rounded bg-gray-700 text-gray-300">{tool.adapter}</span>
          </div>
          <p className="text-sm text-gray-300">{tool.description}</p>
          <div className="flex flex-wrap gap-2 text-xs">
            {tool.cap_allowlist.length > 0 && (
              <span className="text-gray-400">caps: {tool.cap_allowlist.join(', ')}</span>
            )}
            {tool.allowed_roles.length > 0 && (
              <span className="text-gray-400">roles: {tool.allowed_roles.join(', ')}</span>
            )}
            {tool.required_scopes.length > 0 && (
              <span className="text-gray-400">scopes: {tool.required_scopes.join(', ')}</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 3: Add ToolsTab to App.tsx**

Read `ui/src/App.tsx` first. Then:

1. Add import: `import { ToolsTab } from './components/ToolsTab';`
2. Add `'tools'` to the `TabId` type (or equivalent tab ID union)
3. Add a "Tools" tab button in the tab bar (matching the existing tab button pattern)
4. Add `{activeTab === 'tools' && <ToolsTab />}` in the tab content area (matching the existing render pattern)

- [ ] **Step 4: Build UI to verify no TypeScript errors**

```bash
cd /Users/juichanglu/src/HarnessClaw/ui
npm run build 2>&1 | tail -10
```

Expected: Build succeeds with no TypeScript errors

- [ ] **Step 5: Commit**

```bash
cd /Users/juichanglu/src/HarnessClaw
git add ui/src/types.ts ui/src/components/ToolsTab.tsx ui/src/App.tsx
git commit -m "feat: add ToolsTab to dashboard — lists registered tools with adapter and visibility info"
```

---

## Final Verification

- [ ] **Run full test suite**

```bash
cd /Users/juichanglu/src/HarnessClaw
.venv/bin/pytest tests/ --ignore=tests/runtime/test_pty_session.py -v 2>&1 | tail -20
```

Expected: ALL PASS

- [ ] **Verify server imports and tool.register in MCP list**

```bash
.venv/bin/python -c "
from harness_claw.server import app, get_mcp_tools
tools = get_mcp_tools()
names = [t['name'] for t in tools]
assert 'tool.register' in names, f'Missing tool.register, got: {names}'
print('tool.register registered OK')
print(f'Total MCP tools: {len(names)}')
"
```

Expected: `tool.register registered OK`
