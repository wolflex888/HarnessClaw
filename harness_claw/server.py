from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from harness_claw.agent_registry import AgentConfig, AgentRegistry
from harness_claw.job_runner import JobRunner

app = FastAPI(title="HarnessClaw")

registry = AgentRegistry(Path(__file__).parent.parent / "agents.yaml")
runner = JobRunner(registry)


# ── REST endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/agents")
async def list_agents() -> list[dict]:
    return [a.model_dump() for a in registry.all()]


class AgentCreateRequest(BaseModel):
    id: str
    name: str
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    system_prompt: str = "You are a helpful assistant."
    max_tokens: int = 4096
    orchestrates: list[str] = []


@app.post("/api/agents", status_code=201)
async def create_agent(req: AgentCreateRequest) -> dict:
    config = AgentConfig(**req.model_dump())
    registry.add(config)
    return config.model_dump()


@app.put("/api/agents/{agent_id}")
async def update_agent(agent_id: str, req: AgentCreateRequest) -> dict:
    data = req.model_dump()
    data["id"] = agent_id
    config = AgentConfig(**data)
    registry.update(config)
    return config.model_dump()


@app.delete("/api/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str) -> None:
    registry.remove(agent_id)


@app.get("/api/sessions/{agent_id}")
async def get_session(agent_id: str) -> dict:
    session = runner.get_session(agent_id)
    if session is None:
        return {
            "messages": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "model": "",
        }
    return {
        "messages": session.messages,
        "input_tokens": session.input_tokens,
        "output_tokens": session.output_tokens,
        "cost_usd": session.cost_usd,
        "model": session.model,
    }


# ── WebSocket ──────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    send_queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def send(msg: dict) -> None:
        await send_queue.put(msg)

    async def sender() -> None:
        while True:
            msg = await send_queue.get()
            if msg is None:
                break
            try:
                await websocket.send_json(msg)
            except Exception:
                return

    sender_task = asyncio.create_task(sender())

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "chat":
                asyncio.create_task(
                    runner.run_job(data["agent_id"], data["text"], send)
                )
    except WebSocketDisconnect:
        pass
    finally:
        await send_queue.put(None)
        await sender_task


# ── Serve React build (production) ────────────────────────────────────────────

_UI_DIST = Path(__file__).parent.parent / "ui" / "dist"

if _UI_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_UI_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> FileResponse:
        return FileResponse(_UI_DIST / "index.html")
