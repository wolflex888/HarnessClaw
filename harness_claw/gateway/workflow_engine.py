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


class WorkflowEngine:
    def __init__(
        self,
        definitions: dict[str, WorkflowDefinition],
        broker: Any,
        event_bus: Any,
        broadcast_fn: Callable[[dict[str, Any]], Any] | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._definitions = definitions
        self._broker = broker
        self._event_bus = event_bus
        self._broadcast_fn = broadcast_fn
        self._store = WorkflowRunStore(db_path) if db_path else _InMemoryRunStore()
        self._task_subs: dict[str, list[Any]] = {}  # task_id -> [sub_completed, sub_failed]

    # -- Public API --

    async def start(self, workflow_id: str, input: str, initiated_by: str) -> str:
        defn = self._definitions.get(workflow_id)
        if defn is None:
            raise ValueError(f"workflow {workflow_id!r} not found")

        run_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        run = WorkflowRun(
            run_id=run_id,
            workflow_id=workflow_id,
            status="running",
            current_step_id=defn.first_step.id,
            step_results={},
            input=input,
            initiated_by=initiated_by,
            created_at=now,
            updated_at=now,
        )
        self._store.save(run)

        instructions = self._render(defn.first_step.instructions, input=input, prev_result=None, step_results={})
        task_id = await self._broker.delegate(
            delegated_by=run_id,
            caps=defn.first_step.caps,
            instructions=instructions,
        )
        await self._subscribe_step(run_id=run_id, step_id=defn.first_step.id, task_id=task_id)
        await self._broadcast({"type": "workflow.started", "run_id": run_id, "workflow_id": workflow_id, "step_id": defn.first_step.id})
        return run_id

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self._store.get(run_id)

    def list_runs(self) -> list[WorkflowRun]:
        return self._store.all()

    def list_definitions(self) -> list[WorkflowDefinition]:
        return list(self._definitions.values())

    # -- Internal --

    def _render(self, template: str, input: str, prev_result: Any, step_results: dict[str, Any]) -> str:
        result = template
        result = result.replace("{{input}}", input)
        prev_str = prev_result if isinstance(prev_result, str) else (json.dumps(prev_result) if prev_result is not None else "")
        result = result.replace("{{prev.result}}", prev_str)
        for match in re.finditer(r"\{\{steps\.([\w-]+)\.result\}\}", result):
            step_id = match.group(1)
            val = step_results.get(step_id)
            val_str = val if isinstance(val, str) else (json.dumps(val) if val is not None else "")
            result = result.replace(match.group(0), val_str)
        return result

    async def _subscribe_step(self, run_id: str, step_id: str, task_id: str) -> None:
        async def on_completed(event: Any) -> None:
            await self._on_step_event(run_id=run_id, step_id=step_id, task_id=task_id, outcome="completed", event=event)

        async def on_failed(event: Any) -> None:
            await self._on_step_event(run_id=run_id, step_id=step_id, task_id=task_id, outcome="failed", event=event)

        sub_ok = await self._event_bus.subscribe(f"task:{task_id}:completed", on_completed)
        sub_fail = await self._event_bus.subscribe(f"task:{task_id}:failed", on_failed)
        self._task_subs[task_id] = [sub_ok, sub_fail]

    async def _on_step_event(self, run_id: str, step_id: str, task_id: str, outcome: str, event: Any) -> None:
        for sub in self._task_subs.pop(task_id, []):
            await self._event_bus.unsubscribe(sub)

        run = self._store.get(run_id)
        if run is None or run.status != "running":
            return

        defn = self._definitions.get(run.workflow_id)
        if defn is None:
            return

        step = defn.step_by_id(step_id)
        if step is None:
            return

        result = event.payload.get("task", {}).get("result")
        run.step_results[step_id] = result

        await self._broadcast({"type": "workflow.step", "run_id": run_id, "step_id": step_id, "status": outcome, "result": result})

        next_step_id = step.on_success if outcome == "completed" else step.on_failure

        if next_step_id == "stop":
            run.status = "completed" if outcome == "completed" else "failed"
            self._store.save(run)
            if outcome == "completed":
                await self._broadcast({"type": "workflow.completed", "run_id": run_id})
            else:
                await self._broadcast({"type": "workflow.failed", "run_id": run_id, "reason": f"step {step_id!r} failed"})
            return

        next_step = defn.step_by_id(next_step_id)
        if next_step is None:
            run.status = "failed"
            self._store.save(run)
            await self._broadcast({"type": "workflow.failed", "run_id": run_id, "reason": f"step {next_step_id!r} not found"})
            return

        run.current_step_id = next_step_id
        self._store.save(run)

        instructions = self._render(next_step.instructions, input=run.input, prev_result=result, step_results=run.step_results)
        new_task_id = await self._broker.delegate(
            delegated_by=run_id,
            caps=next_step.caps,
            instructions=instructions,
        )
        await self._subscribe_step(run_id=run_id, step_id=next_step_id, task_id=new_task_id)

    async def _broadcast(self, payload: dict[str, Any]) -> None:
        if self._broadcast_fn is not None:
            try:
                result = self._broadcast_fn(payload)
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                pass


class _InMemoryRunStore:
    """In-memory fallback used in tests when no db_path is provided."""

    def __init__(self) -> None:
        self._runs: dict[str, WorkflowRun] = {}

    def save(self, run: WorkflowRun) -> None:
        run.updated_at = datetime.now(timezone.utc).isoformat()
        self._runs[run.run_id] = run

    def get(self, run_id: str) -> WorkflowRun | None:
        return self._runs.get(run_id)

    def all(self) -> list[WorkflowRun]:
        return list(self._runs.values())
