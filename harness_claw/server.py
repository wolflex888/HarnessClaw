from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from harness_claw.role_registry import RoleRegistry
from harness_claw.session import Session
from harness_claw.session_store import SessionStore
from harness_claw.job_runner import JobRunner

_root = Path(__file__).parent.parent
_agents_yaml = _root / "agents.yaml"
_sessions_json = _root / "sessions.json"

registry = RoleRegistry(_agents_yaml)
store = SessionStore(_sessions_json)
runner = JobRunner(registry, store)

app = FastAPI()


# --- REST ---

@app.get("/api/roles")
def list_roles() -> list[dict[str, Any]]:
    return [
        {"id": r.id, "name": r.name, "provider": r.provider,
         "model": r.model, "system_prompt": r.system_prompt, "max_tokens": r.max_tokens}
        for r in registry.all()
    ]


class CreateSessionRequest(BaseModel):
    role_id: str
    working_dir: str


@app.get("/api/sessions")
def list_sessions() -> dict[str, list[dict[str, Any]]]:
    grouped = store.grouped_by_dir()
    return {
        wd: [s.to_dict() for s in sessions]
        for wd, sessions in grouped.items()
    }


@app.post("/api/sessions", status_code=201)
def create_session(req: CreateSessionRequest) -> dict[str, Any]:
    role = registry.get(req.role_id)
    if role is None:
        raise HTTPException(status_code=404, detail=f"Role {req.role_id!r} not found")
    session = Session(
        role_id=req.role_id,
        working_dir=req.working_dir,
        model=role.model,
    )
    store.save(session)
    return session.to_dict()


@app.delete("/api/sessions/{session_id}", status_code=204)
def delete_session(session_id: str) -> None:
    if store.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    runner.delete_session(session_id)


# --- WebSocket ---

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def send(msg: dict[str, Any]) -> None:
        await queue.put(msg)

    async def sender() -> None:
        while True:
            msg = await queue.get()
            await ws.send_json(msg)

    sender_task = asyncio.create_task(sender())
    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")

            if msg_type == "chat":
                session_id = data["session_id"]
                text = data["text"]
                asyncio.create_task(runner.run_job(session_id, text, send))

            elif msg_type == "cancel":
                session_id = data.get("session_id", "")
                runner.kill_job(session_id)

            elif msg_type == "resume":
                session_id = data["session_id"]
                session = store.get(session_id)
                if session:
                    session.status = "idle"
                    store.save(session)
                    await send({"type": "session_update", "session_id": session_id, "name": session.name, "status": "idle"})

            elif msg_type == "permission_response":
                runner.resolve_permission(data["request_id"], approved=data["approved"])

    except WebSocketDisconnect:
        pass
    finally:
        sender_task.cancel()


# --- SPA ---

_dist = _root / "ui" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        return FileResponse(str(_dist / "index.html"))
