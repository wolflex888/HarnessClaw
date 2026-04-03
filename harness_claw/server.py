from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from mcp.server.sse import SseServerTransport
from mcp.server.lowlevel.server import Server as MCPServer
import mcp.types as mcp_types

from harness_claw.gateway.audit import AuditLogger
from harness_claw.gateway.auth import TokenStore
from harness_claw.gateway.broker import Broker, LocalDispatcher
from harness_claw.gateway.event_bus import LocalEventBus
from harness_claw.gateway.capability import LocalConnector, GatewayConnector
from harness_claw.gateway.memory import SqliteMemoryStore
from harness_claw.gateway.task_store import SqliteTaskStore
from harness_claw.gateway.mcp_server import GatewayMCP
from harness_claw.gateway.policy import LocalPolicyEngine
from harness_claw.gateway.workflow_engine import WorkflowEngine
from harness_claw.role_registry import RoleRegistry
from harness_claw.runtime.job_runner import JobRunner
from harness_claw.runtime.session_store import SessionStore
from harness_claw.api import sessions as sessions_api
from harness_claw.api import roles as roles_api
from harness_claw.api import websocket as ws_api

_root = Path(__file__).parent.parent
_agents_yaml = _root / "agents.yaml"
_sessions_json = _root / "sessions.json"
_audit_jsonl = _root / "audit.jsonl"
_memory_db = _root / "memory.db"
_tasks_db = _root / "tasks.db"
_workflows_db = _root / "workflows.db"

# --- Shared state ---
registry = RoleRegistry(_agents_yaml)
cfg = registry.gateway_config

store = SessionStore(_sessions_json)
token_store = TokenStore()
policy = LocalPolicyEngine()
connector = LocalConnector()
gateway_connector = GatewayConnector(
    bootstrap_token=cfg.gateway_bootstrap_token,
    heartbeat_ttl=cfg.gateway_heartbeat_ttl,
)
dispatcher = LocalDispatcher()
event_bus = LocalEventBus()
task_store = SqliteTaskStore(_tasks_db)
broker = Broker(connectors=[connector, gateway_connector], dispatcher=dispatcher, event_bus=event_bus, task_store=task_store)
memory = SqliteMemoryStore(_memory_db)
audit = AuditLogger(_audit_jsonl)
workflow_engine = WorkflowEngine(
    definitions=registry.workflow_definitions,
    broker=broker,
    event_bus=event_bus,
    db_path=_workflows_db,
)

gateway_mcp = GatewayMCP(
    token_store=token_store,
    policy=policy,
    connectors=[connector, gateway_connector],
    broker=broker,
    memory=memory,
    audit=audit,
    workflow_engine=workflow_engine,
)

runner = JobRunner(
    registry=registry,
    store=store,
    token_store=token_store,
    connector=connector,
    dispatcher=dispatcher,
    broker=broker,
    mcp_base_url="http://localhost:8000",
)


# --- FastAPI app ---
app = FastAPI(title="HarnessClaw Gateway")


@app.on_event("startup")
async def startup() -> None:
    task_store.expire(cfg.task_retention_days)

    # Recover interrupted tasks (queued + running) into the scheduler
    interrupted = task_store.get_interrupted()
    broker.scheduler.recover(interrupted)
    task_store.mark_interrupted_as_queued()

    # Wire broker task events into WebSocket broadcast
    async def on_task_event(event: str, task_dict: dict[str, Any]) -> None:
        await runner._broadcast({"type": event, "task": task_dict})

    broker.add_listener(on_task_event)

    async def _wf_broadcast(msg: dict) -> None:
        await runner._broadcast(msg)

    workflow_engine._broadcast_fn = _wf_broadcast

    import json as _json

    def _make_pty_callback_handler(session_id: str):
        async def _on_task_callback(event: Any) -> None:
            result_str = _json.dumps(event.payload.get("task", {}).get("result", ""))
            task_id = event.payload.get("task", {}).get("task_id", "unknown")
            status = event.payload.get("task", {}).get("status", "unknown")
            msg = (
                f"\n[TASK CALLBACK] task_id={task_id} status={status}\n"
                f"Result: {result_str}\n"
            ).encode()
            write_fn = dispatcher._writers.get(session_id)
            if write_fn is not None:
                write_fn(msg)
        return _on_task_callback

    runner._pty_callback_handler_factory = _make_pty_callback_handler
    runner._broker = broker

    for session in store.all():
        if session.status != "killed":
            await runner.start_session(session)

    # Start scheduler poll loop and do an initial drain
    await broker.scheduler.start_poll_loop()
    await broker.scheduler.drain()


def _token_from_request(request: Request) -> str:
    token = request.query_params.get("token", "")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    return token


# Custom MCP HTTP handler that routes tool calls through GatewayMCP with token auth
@app.post("/mcp/tools/call")
async def mcp_tool_call(request: Request) -> JSONResponse:
    body = await request.json()
    token = _token_from_request(request)
    tool = body.get("name")
    args = body.get("arguments", {})

    handlers = {
        "agent.list": lambda a: gateway_mcp.agent_list(token=token, **a),
        "agent.delegate": lambda a: gateway_mcp.agent_delegate(token=token, **a),
        "agent.status": lambda a: gateway_mcp.agent_status(token=token, **a),
        "agent.progress": lambda a: gateway_mcp.agent_progress(token=token, **a),
        "agent.complete": lambda a: gateway_mcp.agent_complete(token=token, **a),
        "agent.spawn": lambda a: gateway_mcp.agent_spawn(token=token, **a),
        "memory.namespaces": lambda a: gateway_mcp.memory_namespaces(token=token),
        "memory.list": lambda a: gateway_mcp.memory_list(token=token, **a),
        "memory.get": lambda a: gateway_mcp.memory_get(token=token, **a),
        "memory.search": lambda a: gateway_mcp.memory_search(token=token, **a),
        "memory.set": lambda a: gateway_mcp.memory_set(token=token, **a),
        "memory.delete": lambda a: gateway_mcp.memory_delete(token=token, **a),
        "memory.tag": lambda a: gateway_mcp.memory_tag(token=token, **a),
        "workflow.run": lambda a: gateway_mcp.workflow_run(token=token, **a),
    }

    handler = handlers.get(tool)
    if handler is None:
        return JSONResponse({"error": f"unknown tool {tool!r}"}, status_code=404)
    try:
        result = await handler(args)
        return JSONResponse({"result": result})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)


# External agent registration endpoints
class ExternalRegisterRequest(BaseModel):
    bootstrap_token: str
    caps: list[str]
    role_id: str


@app.post("/gateway/agents/register", status_code=201)
async def register_external_agent(req: ExternalRegisterRequest) -> dict[str, Any]:
    try:
        session_id = await gateway_connector.register_external(
            bootstrap_token=req.bootstrap_token,
            caps=req.caps,
            role_id=req.role_id,
        )
        return {"session_id": session_id}
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))


@app.post("/gateway/agents/{session_id}/heartbeat", status_code=204)
async def agent_heartbeat(session_id: str) -> None:
    await gateway_connector.heartbeat(session_id)


@app.delete("/gateway/agents/{session_id}", status_code=204)
async def deregister_external_agent(session_id: str) -> None:
    await gateway_connector.deregister(session_id)


# REST API routes
app.include_router(sessions_api.make_router(registry, store, runner))
app.include_router(roles_api.make_router(registry))
app.include_router(ws_api.make_router(runner, store))


@app.get("/api/tasks")
def list_tasks_endpoint() -> list[dict[str, Any]]:
    return [t.to_dict() for t in broker.list_tasks()]


@app.post("/api/tasks/{task_id}/retry")
async def retry_task(task_id: str) -> dict[str, Any]:
    task = broker.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status != "failed":
        raise HTTPException(status_code=400, detail=f"task status is {task.status!r}, not 'failed'")
    try:
        new_task_id = await broker.delegate(
            delegated_by=task.delegated_by,
            caps=task.caps_requested,
            instructions=task.instructions,
            context=task.context,
            callback=task.callback,
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"task_id": new_task_id}


class WorkflowRunRequest(BaseModel):
    input: str
    initiated_by: str = "dashboard"


@app.get("/api/workflows")
def list_workflows() -> list[dict[str, Any]]:
    return [d.to_dict() for d in workflow_engine.list_definitions()]


@app.post("/api/workflows/{workflow_id}/run", status_code=201)
async def run_workflow(workflow_id: str, req: WorkflowRunRequest) -> dict[str, Any]:
    try:
        run_id = await workflow_engine.start(
            workflow_id=workflow_id,
            input=req.input,
            initiated_by=req.initiated_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"run_id": run_id}


@app.get("/api/workflows/runs")
def list_workflow_runs() -> list[dict[str, Any]]:
    return [r.to_dict() for r in workflow_engine.list_runs()]


@app.get("/api/workflows/runs/{run_id}")
def get_workflow_run(run_id: str) -> dict[str, Any]:
    run = workflow_engine.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run.to_dict()


# Audit log endpoint
@app.get("/api/audit")
def get_audit(limit: int = 100) -> list[dict[str, Any]]:
    if not _audit_jsonl.exists():
        return []
    lines = _audit_jsonl.read_text().strip().splitlines()
    events = [json.loads(l) for l in lines[-limit:]]
    return list(reversed(events))


# Memory REST endpoints (for dashboard)
@app.get("/api/memory/namespaces")
async def get_memory_namespaces() -> list[str]:
    return await memory.namespaces()


@app.get("/api/memory/{namespace}/entries")
async def list_memory_entries(namespace: str) -> list[dict[str, Any]]:
    entries = await memory.list(namespace)
    return [
        {"key": e.key, "summary": e.summary, "tags": e.tags,
         "size_bytes": e.size_bytes, "updated_at": e.updated_at}
        for e in entries
    ]


@app.get("/api/memory/{namespace}/entries/{key}")
async def get_memory_entry(namespace: str, key: str) -> dict[str, Any]:
    entry = await memory.get(namespace, key)
    if entry is None:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "key": entry.key, "value": entry.value, "summary": entry.summary,
        "tags": entry.tags, "size_bytes": entry.size_bytes, "updated_at": entry.updated_at,
    }


@app.delete("/api/memory/{namespace}/entries/{key}", status_code=204)
async def delete_memory_entry(namespace: str, key: str) -> None:
    await memory.delete(namespace, key)


# MCP tools list (for dashboard Tools tab)
@app.get("/api/mcp/tools")
def get_mcp_tools() -> list[dict[str, str]]:
    return [
        {"name": "agent.list", "description": "List agents in the capability registry, optionally filtered by caps"},
        {"name": "agent.delegate", "description": "Delegate a task to the best-matched agent; returns task_id"},
        {"name": "agent.status", "description": "Check status and progress of a delegated task"},
        {"name": "agent.spawn", "description": "Spawn a new agent session with a given role"},
        {"name": "agent.progress", "description": "Report progress on the current task (message + % complete)"},
        {"name": "agent.complete", "description": "Signal task completion with a result payload"},
        {"name": "memory.namespaces", "description": "List all memory namespaces"},
        {"name": "memory.list", "description": "List keys and metadata within a namespace"},
        {"name": "memory.get", "description": "Load a specific memory entry into context"},
        {"name": "memory.search", "description": "Hybrid FTS5 + semantic search across a namespace"},
        {"name": "memory.set", "description": "Write a value to a namespace"},
        {"name": "memory.delete", "description": "Delete a key from a namespace"},
        {"name": "memory.tag", "description": "Add tags to a memory entry"},
        {"name": "workflow.run", "description": "Start a named workflow by ID with an input string; returns run_id"},
    ]


# --- MCP SSE endpoint ---
_sse_transport = SseServerTransport("/mcp/messages/")

_MCP_TOOLS = [
    mcp_types.Tool(name="agent.list", description="List agents filtered by caps",
        inputSchema={"type": "object", "properties": {"caps": {"type": "array", "items": {"type": "string"}}}, "required": ["caps"]}),
    mcp_types.Tool(name="agent.delegate", description="Delegate a task to the best-matched agent; returns task_id",
        inputSchema={"type": "object", "properties": {
            "caps": {"type": "array", "items": {"type": "string"}},
            "instructions": {"type": "string"},
            "context": {"type": "object"},
            "callback": {"type": "boolean"},
            "priority": {"type": "integer", "description": "1=high, 2=normal (default), 3=low"},
        }, "required": ["caps", "instructions"]}),
    mcp_types.Tool(name="agent.status", description="Check status of a delegated task",
        inputSchema={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]}),
    mcp_types.Tool(name="agent.progress", description="Report progress on the current task",
        inputSchema={"type": "object", "properties": {
            "task_id": {"type": "string"}, "pct": {"type": "integer"}, "msg": {"type": "string"},
        }, "required": ["task_id", "pct", "msg"]}),
    mcp_types.Tool(name="agent.complete", description="Signal task completion with a result payload",
        inputSchema={"type": "object", "properties": {
            "task_id": {"type": "string"}, "result": {},
        }, "required": ["task_id", "result"]}),
    mcp_types.Tool(name="agent.spawn", description="Spawn a new agent session with a given role",
        inputSchema={"type": "object", "properties": {
            "role_id": {"type": "string"}, "working_dir": {"type": "string"},
        }, "required": ["role_id", "working_dir"]}),
    mcp_types.Tool(name="memory.namespaces", description="List all memory namespaces",
        inputSchema={"type": "object", "properties": {}}),
    mcp_types.Tool(name="memory.list", description="List keys and metadata within a namespace",
        inputSchema={"type": "object", "properties": {"namespace": {"type": "string"}}, "required": ["namespace"]}),
    mcp_types.Tool(name="memory.get", description="Load a specific memory entry",
        inputSchema={"type": "object", "properties": {
            "namespace": {"type": "string"}, "key": {"type": "string"},
        }, "required": ["namespace", "key"]}),
    mcp_types.Tool(name="memory.search", description="Hybrid FTS5 + semantic search across a namespace",
        inputSchema={"type": "object", "properties": {
            "namespace": {"type": "string"}, "query": {"type": "string"},
        }, "required": ["namespace", "query"]}),
    mcp_types.Tool(name="memory.set", description="Write a value to a namespace",
        inputSchema={"type": "object", "properties": {
            "namespace": {"type": "string"}, "key": {"type": "string"}, "value": {"type": "string"},
            "summary": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
        }, "required": ["namespace", "key", "value", "tags"]}),
    mcp_types.Tool(name="memory.delete", description="Delete a key from a namespace",
        inputSchema={"type": "object", "properties": {
            "namespace": {"type": "string"}, "key": {"type": "string"},
        }, "required": ["namespace", "key"]}),
    mcp_types.Tool(name="memory.tag", description="Add tags to a memory entry",
        inputSchema={"type": "object", "properties": {
            "namespace": {"type": "string"}, "key": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        }, "required": ["namespace", "key", "tags"]}),
    mcp_types.Tool(name="workflow.run", description="Start a named workflow by ID",
        inputSchema={"type": "object", "properties": {
            "workflow_id": {"type": "string"}, "input": {"type": "string"},
        }, "required": ["workflow_id", "input"]}),
]


async def _dispatch_tool(token: str, name: str, args: dict[str, Any]) -> Any:
    handlers: dict[str, Any] = {
        "agent.list": lambda a: gateway_mcp.agent_list(token=token, **a),
        "agent.delegate": lambda a: gateway_mcp.agent_delegate(token=token, **a),
        "agent.status": lambda a: gateway_mcp.agent_status(token=token, **a),
        "agent.progress": lambda a: gateway_mcp.agent_progress(token=token, **a),
        "agent.complete": lambda a: gateway_mcp.agent_complete(token=token, **a),
        "agent.spawn": lambda a: gateway_mcp.agent_spawn(token=token, **a),
        "memory.namespaces": lambda a: gateway_mcp.memory_namespaces(token=token),
        "memory.list": lambda a: gateway_mcp.memory_list(token=token, **a),
        "memory.get": lambda a: gateway_mcp.memory_get(token=token, **a),
        "memory.search": lambda a: gateway_mcp.memory_search(token=token, **a),
        "memory.set": lambda a: gateway_mcp.memory_set(token=token, **a),
        "memory.delete": lambda a: gateway_mcp.memory_delete(token=token, **a),
        "memory.tag": lambda a: gateway_mcp.memory_tag(token=token, **a),
        "workflow.run": lambda a: gateway_mcp.workflow_run(token=token, **a),
    }
    handler = handlers.get(name)
    if handler is None:
        raise ValueError(f"unknown tool {name!r}")
    return await handler(args)


def _build_mcp_server(token: str) -> MCPServer:
    server = MCPServer("harnessclaw")

    @server.list_tools()
    async def list_tools() -> list[mcp_types.Tool]:
        return _MCP_TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> list[mcp_types.TextContent]:
        try:
            result = await _dispatch_tool(token, name, arguments or {})
            return [mcp_types.TextContent(type="text", text=json.dumps(result))]
        except Exception as e:
            return [mcp_types.TextContent(type="text", text=f"Error: {e}")]

    return server


@app.get("/mcp/sse")
async def mcp_sse(request: Request) -> Response:
    token = request.query_params.get("token", "")
    server = _build_mcp_server(token)
    async with _sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())
    return Response()


app.mount("/mcp/messages/", app=_sse_transport.handle_post_message)


# SPA
_dist = _root / "ui" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        return FileResponse(str(_dist / "index.html"))
