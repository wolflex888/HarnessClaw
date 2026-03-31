from __future__ import annotations

import asyncio
import base64
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


@app.on_event("startup")
async def startup() -> None:
    """Start PTY processes for all non-killed sessions on server boot."""
    for session in store.all():
        if session.status != "killed":
            await runner.start_session(session)


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
async def create_session(req: CreateSessionRequest) -> dict[str, Any]:
    role = registry.get(req.role_id)
    if role is None:
        raise HTTPException(status_code=404, detail=f"Role {req.role_id!r} not found")
    session = Session(
        role_id=req.role_id,
        working_dir=req.working_dir,
        model=role.model,
    )
    store.save(session)
    await runner.start_session(session)
    return session.to_dict()


@app.delete("/api/sessions/{session_id}", status_code=204)
async def delete_session(session_id: str) -> None:
    if store.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    runner.delete_session(session_id)
    await runner._broadcast({"type": "session_deleted", "session_id": session_id})


# --- WebSocket ---

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def send(msg: dict[str, Any]) -> None:
        await queue.put(msg)

    runner.add_sender(send)

    async def sender() -> None:
        while True:
            msg = await queue.get()
            await ws.send_json(msg)

    sender_task = asyncio.create_task(sender())
    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")

            if msg_type == "input":
                raw = base64.b64decode(data["data"])
                runner.write(data["session_id"], raw)

            elif msg_type == "resize":
                runner.resize(data["session_id"], cols=data["cols"], rows=data["rows"])

            elif msg_type == "cancel":
                session_id = data["session_id"]
                runner.kill_session(session_id)
                session = store.get(session_id)
                if session:
                    await runner._broadcast({
                        "type": "session_update",
                        "session_id": session_id,
                        "status": "killed",
                        "name": session.name,
                    })

    except WebSocketDisconnect:
        pass
    finally:
        runner.remove_sender(send)
        sender_task.cancel()


# --- SPA ---

_dist = _root / "ui" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> FileResponse:
        return FileResponse(str(_dist / "index.html"))
