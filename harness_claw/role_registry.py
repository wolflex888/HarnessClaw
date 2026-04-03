from __future__ import annotations

from dataclasses import dataclass, field
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
    scopes: list[str] = field(default_factory=list)
    caps: list[str] = field(default_factory=list)


@dataclass
class GatewayConfig:
    policy_engine: str = "local"
    memory_backend: str = "sqlite"
    memory_path: str = "./memory.db"
    dispatcher: str = "local"
    event_bus_backend: str = "local"
    gateway_bootstrap_token: str = ""
    gateway_heartbeat_ttl: int = 30
    task_retention_days: int = 7


class RoleRegistry:
    def __init__(self, path: Path) -> None:
        self._roles: dict[str, RoleConfig] = {}
        data = yaml.safe_load(path.read_text())

        # Parse gateway config sections
        policy = data.get("policy", {})
        memory = data.get("memory", {})
        broker = data.get("broker", {})
        event_bus = data.get("event_bus", {})
        tasks = data.get("tasks", {})
        gateway_connector = next(
            (c for c in data.get("connectors", []) if c.get("type") == "gateway"),
            {}
        )
        self.gateway_config = GatewayConfig(
            policy_engine=policy.get("engine", "local"),
            memory_backend=memory.get("backend", "sqlite"),
            memory_path=memory.get("path", "./memory.db"),
            dispatcher=broker.get("dispatcher", "local"),
            event_bus_backend=event_bus.get("backend", "local"),
            gateway_bootstrap_token=gateway_connector.get("bootstrap_token", ""),
            gateway_heartbeat_ttl=gateway_connector.get("heartbeat_ttl", 30),
            task_retention_days=tasks.get("retention_days", 7),
        )

        for item in data.get("roles", []):
            scopes = list(item.get("scopes", []))
            # agent:report is granted to every role by default
            if "agent:report" not in scopes:
                scopes.append("agent:report")
            role = RoleConfig(
                id=item["id"],
                name=item["name"],
                provider=item.get("provider", "claude-code"),
                model=item["model"],
                system_prompt=item["system_prompt"],
                max_tokens=item.get("max_tokens", 8192),
                scopes=scopes,
                caps=list(item.get("caps", [])),
            )
            self._roles[role.id] = role

    def all(self) -> list[RoleConfig]:
        return list(self._roles.values())

    def get(self, role_id: str) -> RoleConfig | None:
        return self._roles.get(role_id)
