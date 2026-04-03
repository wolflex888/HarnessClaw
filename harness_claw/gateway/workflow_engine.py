# harness_claw/gateway/workflow_engine.py
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable


@dataclass
class WorkflowStep:
    id: str
    caps: list[str]
    instructions: str
    on_success: str   # step id or "stop"
    on_failure: str   # step id or "stop"


@dataclass
class WorkflowDefinition:
    id: str
    name: str
    steps: list[WorkflowStep]

    def step_by_id(self, step_id: str) -> WorkflowStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    @property
    def first_step(self) -> WorkflowStep:
        return self.steps[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "steps": [
                {
                    "id": s.id,
                    "caps": s.caps,
                    "instructions": s.instructions,
                    "on_success": s.on_success,
                    "on_failure": s.on_failure,
                }
                for s in self.steps
            ],
        }
