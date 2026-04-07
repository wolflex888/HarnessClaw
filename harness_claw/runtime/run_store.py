from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class Run:
    run_id: str
    agent_id: str
    trigger_type: str          # manual|delegate|spawn|workflow|schedule|event|recovery
    caller: str                # "user", task_id, workflow_id, etc.
    goal: str
    input_payload: dict
    budget_usd: float | None
    timeout_secs: int | None
    permissions_snapshot: dict  # {"scopes": [...], "role": {...}}
    status: str                 # pending|running|paused|completed|failed
    task_ids: list[str]
    checkpoint: dict | None     # {provider_session_id, last_task_id, progress_pct, pause_reason}
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "agent_id": self.agent_id,
            "trigger_type": self.trigger_type,
            "caller": self.caller,
            "goal": self.goal,
            "input_payload": self.input_payload,
            "budget_usd": self.budget_usd,
            "timeout_secs": self.timeout_secs,
            "permissions_snapshot": self.permissions_snapshot,
            "status": self.status,
            "task_ids": self.task_ids,
            "checkpoint": self.checkpoint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Run:
        return cls(
            run_id=data["run_id"],
            agent_id=data["agent_id"],
            trigger_type=data["trigger_type"],
            caller=data["caller"],
            goal=data["goal"],
            input_payload=data["input_payload"],
            budget_usd=data.get("budget_usd"),
            timeout_secs=data.get("timeout_secs"),
            permissions_snapshot=data["permissions_snapshot"],
            status=data["status"],
            task_ids=data.get("task_ids", []),
            checkpoint=data.get("checkpoint"),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS runs (
    run_id               TEXT PRIMARY KEY,
    agent_id             TEXT NOT NULL,
    trigger_type         TEXT NOT NULL,
    caller               TEXT NOT NULL,
    goal                 TEXT NOT NULL,
    input_payload        TEXT NOT NULL,
    budget_usd           REAL,
    timeout_secs         INTEGER,
    permissions_snapshot TEXT NOT NULL,
    status               TEXT NOT NULL,
    task_ids             TEXT NOT NULL,
    checkpoint           TEXT,
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL
)
"""


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        run_id=row["run_id"],
        agent_id=row["agent_id"],
        trigger_type=row["trigger_type"],
        caller=row["caller"],
        goal=row["goal"],
        input_payload=json.loads(row["input_payload"]),
        budget_usd=row["budget_usd"],
        timeout_secs=row["timeout_secs"],
        permissions_snapshot=json.loads(row["permissions_snapshot"]),
        status=row["status"],
        task_ids=json.loads(row["task_ids"]),
        checkpoint=json.loads(row["checkpoint"]) if row["checkpoint"] else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SqliteRunStore:
    def __init__(self, path: Path) -> None:
        self._path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, run: Run) -> None:
        run.updated_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runs
                    (run_id, agent_id, trigger_type, caller, goal, input_payload,
                     budget_usd, timeout_secs, permissions_snapshot, status,
                     task_ids, checkpoint, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status               = excluded.status,
                    task_ids             = excluded.task_ids,
                    checkpoint           = excluded.checkpoint,
                    updated_at           = excluded.updated_at
                """,
                (
                    run.run_id, run.agent_id, run.trigger_type, run.caller,
                    run.goal, json.dumps(run.input_payload),
                    run.budget_usd, run.timeout_secs,
                    json.dumps(run.permissions_snapshot), run.status,
                    json.dumps(run.task_ids),
                    json.dumps(run.checkpoint) if run.checkpoint is not None else None,
                    run.created_at, run.updated_at,
                ),
            )

    def get(self, run_id: str) -> Run | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return _row_to_run(row) if row else None

    def all(self) -> list[Run]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY created_at ASC"
            ).fetchall()
        return [_row_to_run(r) for r in rows]

    def get_by_agent(self, agent_id: str) -> list[Run]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE agent_id = ? ORDER BY created_at ASC",
                (agent_id,),
            ).fetchall()
        return [_row_to_run(r) for r in rows]

    def get_by_status(self, status: str) -> list[Run]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runs WHERE status = ? ORDER BY created_at ASC",
                (status,),
            ).fetchall()
        return [_row_to_run(r) for r in rows]
