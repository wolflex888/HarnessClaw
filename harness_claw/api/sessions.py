from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from harness_claw.role_registry import RoleRegistry
from harness_claw.runtime.session_store import SessionStore
from harness_claw.runtime.job_runner import JobRunner
from harness_claw.session import Session

router = APIRouter(prefix="/api")


class CreateSessionRequest(BaseModel):
    role_id: str
    working_dir: str


class AttachSessionRequest(BaseModel):
    role_id: str
    working_dir: str


def make_router(registry: RoleRegistry, store: SessionStore, runner: JobRunner) -> APIRouter:
    @router.get("/sessions")
    def list_sessions() -> dict[str, list[dict[str, Any]]]:
        grouped = store.grouped_by_dir()
        return {
            wd: [s.to_dict() for s in sessions]
            for wd, sessions in grouped.items()
        }

    @router.post("/sessions", status_code=201)
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

    @router.delete("/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> None:
        if store.get(session_id) is None:
            raise HTTPException(status_code=404, detail="Session not found")
        runner.delete_session(session_id)
        await runner._broadcast({"type": "session_deleted", "session_id": session_id})

    @router.post("/sessions/attach", status_code=201)
    def attach_session(req: AttachSessionRequest) -> dict[str, Any]:
        """Issue a token and MCP config for an externally-run claude process (e.g. iTerm)."""
        role = registry.get(req.role_id)
        if role is None:
            raise HTTPException(status_code=404, detail=f"Role {req.role_id!r} not found")
        session = Session(
            role_id=req.role_id,
            working_dir=req.working_dir,
            model=role.model,
            status="running",
        )
        store.save(session)
        token = ""
        if runner._token_store is not None:
            token = runner._token_store.issue(session.session_id, role.scopes)
            runner._session_tokens[session.session_id] = token
            runner._write_mcp_config(req.working_dir, token)
        return {
            "session_id": session.session_id,
            "token": token,
            "role_id": req.role_id,
            "working_dir": req.working_dir,
            "cmd": f"HARNESS_TOKEN={token} claude",
        }

    return router
