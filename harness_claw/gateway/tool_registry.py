from __future__ import annotations

from harness_claw.gateway.tool_store import SqliteToolStore, ToolDef
from harness_claw.role_registry import RoleConfig


class ToolRegistry:
    """Wraps SqliteToolStore and applies the per-role visibility filter.

    Visibility = cap_allowlist ∩ role.caps  AND  role_id ∈ allowed_roles  AND
                 role.scopes ⊇ required_scopes
    """

    def __init__(self, store: SqliteToolStore) -> None:
        self._store = store

    def save(self, tool: ToolDef) -> None:
        self._store.save(tool)

    def get(self, tool_id: str) -> ToolDef | None:
        return self._store.get(tool_id)

    def list(self) -> list[ToolDef]:
        return self._store.list()

    def delete(self, tool_id: str) -> bool:
        return self._store.delete(tool_id)

    def get_visible_tools(self, role: RoleConfig) -> list[ToolDef]:
        """Return tools visible to this role — triple-gate intersection."""
        return [t for t in self._store.list() if self._is_visible(t, role)]

    def _is_visible(self, tool: ToolDef, role: RoleConfig) -> bool:
        caps_ok = bool(set(tool.cap_allowlist) & set(role.caps))
        role_ok = not tool.allowed_roles or role.id in tool.allowed_roles
        scopes_ok = all(s in role.scopes for s in tool.required_scopes)
        return caps_ok and role_ok and scopes_ok
