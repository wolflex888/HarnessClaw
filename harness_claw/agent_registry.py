from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    id: str
    name: str
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    system_prompt: str = "You are a helpful assistant."
    max_tokens: int = 4096
    orchestrates: list[str] = Field(default_factory=list)


class AgentRegistry:
    def __init__(self, config_path: Path | str = "agents.yaml") -> None:
        self._config_path = Path(config_path)
        self._agents: dict[str, AgentConfig] = {}
        self._load_from_file()

    def _load_from_file(self) -> None:
        if not self._config_path.exists():
            return
        data = yaml.safe_load(self._config_path.read_text()) or {}
        for entry in data.get("agents", []):
            cfg = AgentConfig(**entry)
            self._agents[cfg.id] = cfg

    def get(self, agent_id: str) -> AgentConfig:
        if agent_id not in self._agents:
            raise KeyError(f"Agent '{agent_id}' not found")
        return self._agents[agent_id]

    def all(self) -> list[AgentConfig]:
        return list(self._agents.values())

    def add(self, config: AgentConfig) -> None:
        self._agents[config.id] = config

    def remove(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)

    def update(self, config: AgentConfig) -> None:
        if config.id not in self._agents:
            raise KeyError(f"Agent '{config.id}' not found")
        self._agents[config.id] = config
