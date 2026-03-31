from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from harness_claw.gateway.audit import AuditLogger
from harness_claw.gateway.auth import TokenStore
from harness_claw.gateway.broker import Broker, LocalDispatcher
from harness_claw.gateway.capability import LocalConnector, GatewayConnector
from harness_claw.gateway.memory import SqliteMemoryStore
from harness_claw.gateway.mcp_server import GatewayMCP
from harness_claw.gateway.policy import LocalPolicyEngine
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
broker = Broker(connectors=[connector, gateway_connector], dispatcher=dispatcher)
memory = SqliteMemoryStore(_memory_db)
audit = AuditLogger(_audit_jsonl)

gateway_mcp = GatewayMCP(
    token_store=token_store,
    policy=policy,
    connectors=[connector, gateway_connector],
    broker=broker,
    memory=memory,
    audit=audit,
)

runner = JobRunner(
    registry=registry,
    store=store,
    token_store=token_store,
    connector=connector,
    dispatcher=dispatcher,
    mcp_base_url="http://localhost:8000",
)


# --- FastAPI app ---
app = FastAPI(title="HarnessClaw Gateway")


@app.on_event("startup")
async def startup() -> None:
    # Wire broker task events into WebSocket broadcast
    async def on_task_event(event: str, task_dict: dict[str, Any]) -> None:
        await runner._broadcast({"type": event, "task": task_dict})

    broker.add_listener(on_task_event)

    for session in store.all():
        if session.status != "killed":
            await runner.start_session(session)


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


@app.get("/api/memory/{namespace:path}")
async def list_memory_entries(namespace: str) -> list[dict[str, Any]]:
    entries = await memory.list(namespace)
    return [
        {"key": e.key, "summary": e.summary, "tags": e.tags,
         "size_bytes": e.size_bytes, "updated_at": e.updated_at}
        for e in entries
    ]


@app.get("/api/memory/{namespace:path}/{key}")
async def get_memory_entry(namespace: str, key: str) -> dict[str, Any]:
    entry = await memory.get(namespace, key)
    if entry is None:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "key": entry.key, "value": entry.value, "summary": entry.summary,
        "tags": entry.tags, "size_bytes": entry.size_bytes, "updated_at": entry.updated_at,
    }


@app.delete("/api/memory/{namespace:path}/{key}", status_code=204)
async def delete_memory_entry(namespace: str, key: str) -> None:
    await memory.delete(namespace, key)


# SPA
_dist = _root / "ui" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        return FileResponse(str(_dist / "index.html"))
