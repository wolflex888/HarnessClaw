# Tool Registration API Design

**Date:** 2026-04-05
**Status:** Approved

## Overview

Add a runtime Tool Registration API to HarnessClaw. Tools are first-class objects with name, description, input/output schemas, execution adapter config, and visibility policy. Agents do not execute tools directly — they issue syscall-style requests; HarnessClaw is the kernel that validates, routes, and executes. The LLM sees only the tools it is permitted to call, injected as MCP tools at session start.

## Goals

- `POST /api/tools` registers a tool at runtime (upsert by id); tools persist in SQLite
- Each tool carries: name, description, input schema, output schema, adapter config, and visibility gates
- Visibility = `tool.cap_allowlist ∩ role.caps` AND `role_id ∈ tool.allowed_roles` AND `role.scopes ⊇ tool.required_scopes`
- Three execution adapters at launch: CLI (shell command template), HTTP (external endpoint), MCP forward (proxy to another MCP server)
- LLM sees filtered tool schemas injected into its session via `.claude/settings.json`
- Agents can register tools programmatically via a new `tool.register` MCP tool (requires `tool:write` scope)
- Dashboard `ToolsTab` lists registered tools (read-only)

## Non-Goals

- UI for creating/editing tools (use the REST API)
- Tool versioning or rollback
- Streaming tool output
- Tool chaining / composition

---

## Tool Definition

```python
@dataclass
class ToolDef:
    id: str                     # unique key, e.g. "run_tests"
    name: str                   # display name
    description: str            # LLM-readable description of what this tool does
    input_schema: dict          # JSON Schema for call parameters
    output_schema: dict         # JSON Schema for structured return value
    adapter: str                # "cli" | "http" | "mcp"
    adapter_config: dict        # backend-specific config (see Adapters section)
    cap_allowlist: list[str]    # caps required to see this tool ([] = no agent sees it)
    allowed_roles: list[str]    # role IDs allowed; empty list = all roles pass
    required_scopes: list[str]  # scopes required; empty list = no scope gate
    metadata: dict              # scheduler hints, timeout overrides, tags
    created_at: str             # ISO 8601
    updated_at: str             # ISO 8601
```

### Adapters

**CLI:**
```json
{
  "command": "pytest {{path}} -v",
  "timeout": 30,
  "working_dir": "/repo"
}
```
Input args are substituted into `command` using `{{arg_name}}` template syntax. Returns `{"stdout": "...", "stderr": "...", "exit_code": 0}`.

**HTTP:**
```json
{
  "url": "https://api.example.com/search",
  "method": "POST",
  "headers": {"Authorization": "Bearer {{token}}"}
}
```
Request body is the validated input args as JSON. Returns the parsed JSON response body.

**MCP forward:**
```json
{
  "server_url": "http://localhost:9000",
  "tool_name": "original_tool_name"
}
```
Forwards the call to another MCP server's tool. Returns that tool's result.

---

## Visibility Filter

`ToolRegistry.get_visible_tools(role)` applies the triple-gate intersection:

```python
def _is_visible(self, tool: ToolDef, role: RoleConfig) -> bool:
    caps_ok = bool(set(tool.cap_allowlist) & set(role.caps))
    role_ok = not tool.allowed_roles or role.id in tool.allowed_roles
    scopes_ok = all(s in role.scopes for s in tool.required_scopes)
    return caps_ok and role_ok and scopes_ok
```

All three conditions must be true. A tool with an empty `cap_allowlist` is visible to no one.

---

## Session Injection

At session start, `JobRunner.start_session` calls `tool_registry.get_visible_tools(role)` and passes the filtered `list[ToolDef]` to `configure_session`. The provider serialises each tool's `name`, `description`, and `input_schema` into `.claude/settings.json` as additional MCP tools alongside existing permissions. The LLM sees them as callable MCP tools — no special agent-side handling required.

---

## Tool Execution

`ToolExecutor` handles all registered-tool calls:

```python
class ToolExecutor:
    async def execute(self, tool_id: str, arguments: dict) -> dict:
        tool = self._registry.get(tool_id)
        if tool is None:
            raise KeyError(f"Tool {tool_id!r} not registered")
        validate(arguments, tool.input_schema)       # jsonschema; raises ValidationError
        result = await self._dispatch(tool, arguments)
        validate(result, tool.output_schema)         # raises ValidationError
        return result
```

In `server.py`'s `_dispatch_tool`, after all known built-in tool handlers, unknown tool names fall through to `tool_executor.execute(tool_name, arguments)`.

---

## REST API

| Method | Path | Scope required | Description |
|--------|------|----------------|-------------|
| `POST` | `/api/tools` | `tool:write` | Register or update a tool (upsert by `id`) |
| `GET` | `/api/tools` | `tool:read` | List all registered tools |
| `GET` | `/api/tools/{id}` | `tool:read` | Get one tool definition |
| `DELETE` | `/api/tools/{id}` | `tool:write` | Remove a tool |

Request body for `POST /api/tools` is a JSON object matching `ToolDef` fields (minus `created_at`/`updated_at`).

---

## MCP Tool

A new `tool.register` MCP tool allows agents to register tools programmatically:

```
tool.register(id, name, description, input_schema, output_schema,
              adapter, adapter_config, cap_allowlist, allowed_roles,
              required_scopes, metadata)
```

Requires `tool:write` scope. Returns `{"id": "...", "status": "registered"}`.

---

## New Scopes

Two new scopes added to `PolicyEngine` and available in `agents.yaml`:

| Scope | Description |
|-------|-------------|
| `tool:read` | List and inspect registered tool definitions |
| `tool:write` | Register, update, and delete tools |

---

## Dashboard

A new `ToolsTab` in the React UI. Lists all registered tools with: id, name, description, adapter type, cap_allowlist, allowed_roles, required_scopes. Read-only — no create/edit form.

---

## File Map

| File | Change |
|------|--------|
| `harness_claw/gateway/tool_store.py` | New: `ToolDef` dataclass + `SqliteToolStore` CRUD |
| `harness_claw/gateway/tool_registry.py` | New: `ToolRegistry` with `get_visible_tools` filter |
| `harness_claw/runtime/tool_executor.py` | New: `ToolExecutor` with CLI/HTTP/MCP adapters |
| `harness_claw/gateway/mcp_server.py` | Add `tool_register` MCP method |
| `harness_claw/gateway/policy.py` | Add `tool:read`, `tool:write` scopes |
| `harness_claw/server.py` | REST routes; fallthrough dispatch; wire ToolRegistry + ToolExecutor |
| `harness_claw/observability/provider.py` | Inject visible tool schemas into session settings |
| `harness_claw/runtime/job_runner.py` | Pass filtered tools to `configure_session` |
| `agents.yaml` | Add `tool:read`/`tool:write` scopes to relevant roles |
| `ui/src/components/ToolsTab.tsx` | New: read-only tools list tab |
| `ui/src/types.ts` | Add `ToolDef` type |
| `ui/src/App.tsx` | Add ToolsTab to dashboard |
| `tests/gateway/test_tool_store.py` | New: CRUD unit tests |
| `tests/gateway/test_tool_registry.py` | New: visibility filter unit tests |
| `tests/runtime/test_tool_executor.py` | New: adapter execution tests (mocked subprocesses/HTTP) |
| `tests/test_server_endpoints.py` | Add tool REST endpoint tests |

---

## Testing

1. `ToolDef` round-trips through SQLite (create, read, update, delete)
2. `get_visible_tools` correctly applies cap / role / scope intersection
3. `ToolExecutor` CLI adapter substitutes args and captures stdout/stderr/exit_code
4. `ToolExecutor` raises `ValidationError` on invalid input or output
5. Unknown tool name in `_dispatch_tool` routes to `ToolExecutor`
6. `POST /api/tools` requires `tool:write` scope; `GET /api/tools` requires `tool:read`
7. Agent session settings include injected tool schemas for visible tools only
