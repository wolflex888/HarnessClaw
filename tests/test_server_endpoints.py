import pytest
from fastapi.testclient import TestClient
from harness_claw.server import app, broker
from harness_claw.gateway.task_store import Task


@pytest.fixture
def client():
    c = TestClient(app)
    yield c
    # Clean up tools registered during tests to prevent accumulation across runs
    for tool_id in ["my_tool", "get_me", "del_me", "test_cli_tool", "mcp_registered_tool", "stream_test_tool"]:
        c.delete(f"/api/tools/{tool_id}")


def make_paused_task() -> Task:
    task = Task(
        task_id="paused-1",
        delegated_by="orch",
        delegated_to="s1",
        instructions="do it",
        caps_requested=["python"],
        status="paused",
        pause_reason="Cost $0.51 exceeded limit $0.50",
        resume=True,
    )
    return task


def test_resume_paused_task(client):
    task = make_paused_task()
    broker._store.save(task)

    resp = client.post("/api/tasks/paused-1/resume")
    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data

    reloaded = broker.get_task("paused-1")
    assert reloaded.status == "queued"
    assert reloaded.pause_reason is None


def test_resume_non_paused_task_returns_400(client):
    task = Task(
        task_id="running-1",
        delegated_by="orch",
        delegated_to="s1",
        instructions="do it",
        caps_requested=["python"],
        status="running",
    )
    broker._store.save(task)
    resp = client.post("/api/tasks/running-1/resume")
    assert resp.status_code == 400


def test_resume_missing_task_returns_404(client):
    resp = client.post("/api/tasks/nonexistent/resume")
    assert resp.status_code == 404


def test_execute_plan_missing_file_returns_error(client):
    # Calls the REST tool dispatch endpoint with a nonexistent plan path.
    # We test via the /mcp/tools/call HTTP path used by non-SSE clients.
    # Since execute_plan requires a running event loop and broker, we test
    # that a missing file raises a descriptive error, not a 500.
    resp = client.post(
        "/mcp/tools/call",
        json={"name": "execute_plan", "arguments": {"plan_path": "/nonexistent/plan.md"}},
        headers={"Authorization": f"Bearer {_get_bootstrap_token()}"},
    )
    assert resp.status_code == 400
    body = resp.text
    assert "not found" in body.lower() or "FileNotFoundError" in body.lower()


def _get_bootstrap_token():
    from harness_claw.server import token_store
    return token_store.issue("bootstrap", ["agent:delegate", "tool:write"])


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
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("id") == "test_cli_tool"
    assert body.get("status") == "registered"


def test_get_tools_returns_empty_list(client):
    resp = client.get("/api/tools")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


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
    assert resp.status_code == 201
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


def test_fallthrough_tool_broadcasts_output(client):
    """When a registered CLI tool is called via /mcp/tools/call, runner._broadcast
    is called with type='output' messages containing labeled tool output."""
    import base64 as _b64
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, patch
    from harness_claw.server import runner, token_store, tool_registry
    from harness_claw.gateway.tool_store import ToolDef

    now = datetime.now(timezone.utc).isoformat()
    tool = ToolDef(
        id="stream_test_tool",
        name="stream_test_tool",
        description="test",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
        output_schema={"type": "object"},
        adapter="cli",
        adapter_config={"command": "echo {{msg}}", "timeout": 5},
        cap_allowlist=[], allowed_roles=[], required_scopes=[],
        metadata={}, created_at=now, updated_at=now,
    )
    tool_registry.save(tool)

    token = token_store.issue("sess-stream-test", ["tool:use"])
    mock_broadcast = AsyncMock()

    with patch.object(runner, "_broadcast", mock_broadcast):
        resp = client.post(
            "/mcp/tools/call",
            json={"name": "stream_test_tool", "arguments": {"msg": "hello-stream"}},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    output_calls = [
        call.args[0] for call in mock_broadcast.call_args_list
        if call.args[0].get("type") == "output"
    ]
    assert len(output_calls) > 0
    combined = b"".join(_b64.b64decode(m["data"]) for m in output_calls)
    assert b"[tool: stream_test_tool]" in combined
    assert b"hello-stream" in combined

    tool_registry.delete("stream_test_tool")
