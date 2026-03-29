from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class RoleConfig:
    id: str
    name: str
    provider: str
    model: str
    system_prompt: str
    max_tokens: int = 8192


class RoleRegistry:
    def __init__(self, path: Path) -> None:
        self._roles: dict[str, RoleConfig] = {}
        data = yaml.safe_load(path.read_text())
        for item in data.get("roles", []):
            role = RoleConfig(
                id=item["id"],
                name=item["name"],
                provider=item.get("provider", "claude-code"),
                model=item["model"],
                system_prompt=item["system_prompt"],
                max_tokens=item.get("max_tokens", 8192),
            )
            self._roles[role.id] = role

    def all(self) -> list[RoleConfig]:
        return list(self._roles.values())

    def get(self, role_id: str) -> RoleConfig | None:
        return self._roles.get(role_id)
