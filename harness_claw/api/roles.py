from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from harness_claw.role_registry import RoleRegistry

router = APIRouter(prefix="/api")


def make_router(registry: RoleRegistry) -> APIRouter:
    @router.get("/roles")
    def list_roles() -> list[dict[str, Any]]:
        return [
            {
                "id": r.id, "name": r.name, "provider": r.provider,
                "model": r.model, "system_prompt": r.system_prompt,
                "max_tokens": r.max_tokens,
            }
            for r in registry.all()
        ]

    return router
