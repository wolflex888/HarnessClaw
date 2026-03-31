from __future__ import annotations

import asyncio
import base64
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from harness_claw.runtime.job_runner import JobRunner
from harness_claw.runtime.session_store import SessionStore

router = APIRouter()


def make_router(runner: JobRunner, store: SessionStore) -> APIRouter:
    @router.websocket("/ws")
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

    return router
