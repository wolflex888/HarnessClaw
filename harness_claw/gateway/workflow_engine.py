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

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError(f"workflow {self.id!r} must have at least one step")

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


@dataclass
class WorkflowRun:
    run_id: str
    workflow_id: str
    status: str          # running | completed | failed
    current_step_id: str
    step_results: dict[str, Any]
    input: str
    initiated_by: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "current_step_id": self.current_step_id,
            "step_results": self.step_results,
            "input": self.input,
            "initiated_by": self.initiated_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


_CREATE_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id          TEXT PRIMARY KEY,
    workflow_id     TEXT NOT NULL,
    status          TEXT NOT NULL,
    current_step_id TEXT NOT NULL,
    step_results    TEXT NOT NULL DEFAULT '{}',
    input           TEXT NOT NULL,
    initiated_by    TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)
"""


class WorkflowRunStore:
    def __init__(self, path: Path) -> None:
        self._path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_CREATE_RUNS_TABLE)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, run: WorkflowRun) -> None:
        run.updated_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workflow_runs
                    (run_id, workflow_id, status, current_step_id, step_results,
                     input, initiated_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status          = excluded.status,
                    current_step_id = excluded.current_step_id,
                    step_results    = excluded.step_results,
                    updated_at      = excluded.updated_at
                """,
                (
                    run.run_id, run.workflow_id, run.status, run.current_step_id,
                    json.dumps(run.step_results), run.input, run.initiated_by,
                    run.created_at, run.updated_at,
                ),
            )

    def get(self, run_id: str) -> WorkflowRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        return WorkflowRun(
            run_id=row["run_id"],
            workflow_id=row["workflow_id"],
            status=row["status"],
            current_step_id=row["current_step_id"],
            step_results=json.loads(row["step_results"]),
            input=row["input"],
            initiated_by=row["initiated_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def all(self) -> list[WorkflowRun]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workflow_runs ORDER BY created_at ASC"
            ).fetchall()
        return [
            WorkflowRun(
                run_id=r["run_id"],
                workflow_id=r["workflow_id"],
                status=r["status"],
                current_step_id=r["current_step_id"],
                step_results=json.loads(r["step_results"]),
                input=r["input"],
                initiated_by=r["initiated_by"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]
