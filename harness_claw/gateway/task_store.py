from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass
class Task:
    task_id: str
    delegated_by: str
    delegated_to: str
    instructions: str
    caps_requested: list[str]
    context: dict[str, Any] | None = None
    status: str = "queued"       # queued | running | completed | failed
    progress_pct: int = 0
    progress_msg: str = ""
    result: dict[str, Any] | str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    callback: bool = False
    priority: int = 2
    resume: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "delegated_by": self.delegated_by,
            "delegated_to": self.delegated_to,
            "instructions": self.instructions,
            "caps_requested": self.caps_requested,
            "context": self.context,
            "status": self.status,
            "progress_pct": self.progress_pct,
            "progress_msg": self.progress_msg,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "callback": self.callback,
            "priority": self.priority,
            "resume": self.resume,
        }


@runtime_checkable
class TaskStoreProtocol(Protocol):
    """Structural interface for task stores — used as the type for Broker.task_store."""
    def save(self, task: Task) -> None: ...
    def get(self, task_id: str) -> Task | None: ...
    def all(self) -> list[Task]: ...
    def get_interrupted(self) -> list[Task]: ...
    def mark_interrupted_as_queued(self) -> int: ...


class TaskStore:
    """In-memory task store. Default fallback; used in tests."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def save(self, task: Task) -> None:
        task.updated_at = datetime.now(timezone.utc).isoformat()
        self._tasks[task.task_id] = task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def all(self) -> list[Task]:
        return list(self._tasks.values())

    def get_interrupted(self) -> list[Task]:
        return [t for t in self._tasks.values() if t.status in ("queued", "running")]

    def mark_interrupted_as_queued(self) -> int:
        count = 0
        for task in self._tasks.values():
            if task.status in ("queued", "running"):
                task.status = "queued"
                count += 1
        return count


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id        TEXT PRIMARY KEY,
    delegated_by   TEXT NOT NULL,
    delegated_to   TEXT NOT NULL,
    instructions   TEXT NOT NULL,
    caps_requested TEXT NOT NULL,
    context        TEXT,
    status         TEXT NOT NULL,
    progress_pct   INTEGER NOT NULL DEFAULT 0,
    progress_msg   TEXT NOT NULL DEFAULT '',
    result         TEXT,
    callback       INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    priority       INTEGER NOT NULL DEFAULT 2,
    resume         INTEGER NOT NULL DEFAULT 0
)
"""

_MIGRATE_SQL = [
    "ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 2",
    "ALTER TABLE tasks ADD COLUMN resume   INTEGER NOT NULL DEFAULT 0",
]


def _row_to_task(row: sqlite3.Row) -> Task:
    result_raw = row["result"]
    if result_raw is None:
        result: dict[str, Any] | str | None = None
    else:
        try:
            result = json.loads(result_raw)
        except (json.JSONDecodeError, TypeError):
            result = result_raw

    keys = row.keys()
    return Task(
        task_id=row["task_id"],
        delegated_by=row["delegated_by"],
        delegated_to=row["delegated_to"],
        instructions=row["instructions"],
        caps_requested=json.loads(row["caps_requested"]),
        context=json.loads(row["context"]) if row["context"] else None,
        status=row["status"],
        progress_pct=row["progress_pct"],
        progress_msg=row["progress_msg"],
        result=result,
        callback=bool(row["callback"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        priority=row["priority"] if "priority" in keys else 2,
        resume=bool(row["resume"]) if "resume" in keys else False,
    )


class SqliteTaskStore:
    """SQLite-backed task store. Same save/get/all interface as TaskStore."""

    def __init__(self, path: Path) -> None:
        self._path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)
            for sql in _MIGRATE_SQL:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError:
                    pass  # column already exists

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        return conn

    def save(self, task: Task) -> None:
        task.updated_at = datetime.now(timezone.utc).isoformat()
        result_json = json.dumps(task.result) if task.result is not None else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks
                    (task_id, delegated_by, delegated_to, instructions, caps_requested,
                     context, status, progress_pct, progress_msg, result, callback,
                     created_at, updated_at, priority, resume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status       = excluded.status,
                    progress_pct = excluded.progress_pct,
                    progress_msg = excluded.progress_msg,
                    result       = excluded.result,
                    callback     = excluded.callback,
                    updated_at   = excluded.updated_at,
                    priority     = excluded.priority,
                    resume       = excluded.resume
                """,
                (
                    task.task_id, task.delegated_by, task.delegated_to, task.instructions,
                    json.dumps(task.caps_requested),
                    json.dumps(task.context) if task.context is not None else None,
                    task.status, task.progress_pct, task.progress_msg,
                    result_json, int(task.callback),
                    task.created_at, task.updated_at,
                    task.priority, int(task.resume),
                ),
            )

    def get(self, task_id: str) -> Task | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return _row_to_task(row) if row else None

    def all(self) -> list[Task]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at ASC"
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def get_interrupted(self) -> list[Task]:
        """Return all tasks with status in ('queued', 'running')."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status IN ('queued', 'running') ORDER BY priority ASC, created_at ASC"
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def mark_interrupted_as_queued(self) -> int:
        """Set status='queued' for all interrupted tasks. Returns count updated."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            count = conn.execute(
                "UPDATE tasks SET status = 'queued', updated_at = ? WHERE status IN ('queued', 'running')",
                (now,),
            ).rowcount
        return count

    def mark_stale_as_failed(self) -> int:
        """Mark queued/running tasks failed with reason 'server_restart'. Returns count updated."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE tasks
                SET status = 'failed', result = ?, updated_at = ?
                WHERE status IN ('queued', 'running')
                """,
                (json.dumps("server_restart"), now),
            )
        return cursor.rowcount

    def expire(self, days: int) -> int:
        """Delete tasks with updated_at older than `days` days. Returns count deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM tasks WHERE updated_at < ?", (cutoff,)
            )
        return cursor.rowcount
