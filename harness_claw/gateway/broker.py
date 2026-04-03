from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Protocol

from harness_claw.gateway.capability import AgentAdvertisement, CapabilityConnector
from harness_claw.gateway.event_bus import EventBus
from harness_claw.gateway.task_store import Task, TaskStore


class TaskDispatcher(Protocol):
    async def dispatch(self, task: Task, agent: AgentAdvertisement) -> None: ...
    async def cancel(self, task_id: str) -> None: ...


class LocalDispatcher:
    """Writes task instructions to the target agent's PTY via a registered write callback."""

    def __init__(self) -> None:
        self._writers: dict[str, Any] = {}

    def register_writer(self, session_id: str, write_fn: Any) -> None:
        self._writers[session_id] = write_fn

    def unregister_writer(self, session_id: str) -> None:
        self._writers.pop(session_id, None)

    async def dispatch(self, task: Task, agent: AgentAdvertisement) -> None:
        write_fn = self._writers.get(agent.session_id)
        if write_fn is None:
            raise RuntimeError(f"No writer registered for session {agent.session_id!r}")
        payload = (
            f"\n[HARNESS_TASK:{task.task_id}]\n"
            f"{task.instructions}\n"
        ).encode()
        write_fn(payload)

    async def cancel(self, task_id: str) -> None:
        pass  # PTY cancellation handled by kill_session


class Broker:
    """Routes delegation requests to capability-matched agents."""

    def __init__(
        self,
        connectors: list[CapabilityConnector],
        dispatcher: TaskDispatcher,
        event_bus: EventBus | None = None,
        task_store: TaskStore | None = None,
    ) -> None:
        self._connectors = connectors
        self._dispatcher = dispatcher
        self._event_bus = event_bus
        self._store = task_store or TaskStore()
        self._listeners: list[Any] = []
        self._callback_handlers: dict[str, Any] = {}  # session_id -> handler
        self._callback_subs: dict[str, list[Any]] = {}  # task_id -> [Subscription]

    def add_listener(self, fn: Any) -> None:
        self._listeners.append(fn)

    def remove_listener(self, fn: Any) -> None:
        try:
            self._listeners.remove(fn)
        except ValueError:
            pass

    def register_callback_handler(self, session_id: str, handler: Any) -> None:
        self._callback_handlers[session_id] = handler

    def unregister_callback_handler(self, session_id: str) -> None:
        self._callback_handlers.pop(session_id, None)

    async def _notify(self, event: str, task: Task) -> None:
        for fn in list(self._listeners):
            await fn(event, task.to_dict())

    async def delegate(
        self,
        delegated_by: str,
        caps: list[str],
        instructions: str,
        context: dict[str, Any] | None = None,
        callback: bool = False,
    ) -> str:
        candidates: list[AgentAdvertisement] = []
        for connector in self._connectors:
            candidates.extend(await connector.query(caps))

        if not candidates:
            raise ValueError(f"no agent found matching caps {caps}")

        agent = candidates[0]

        task = Task(
            task_id=str(uuid.uuid4()),
            delegated_by=delegated_by,
            delegated_to=agent.session_id,
            instructions=instructions,
            caps_requested=caps,
            context=context,
            status="running",
            callback=callback,
        )
        self._store.save(task)
        await self._dispatcher.dispatch(task, agent)

        if callback and self._event_bus is not None:
            handler = self._callback_handlers.get(delegated_by)
            if handler is not None:
                subs = []
                sub_ok = await self._event_bus.subscribe(f"task:{task.task_id}:completed", handler)
                subs.append(sub_ok)
                sub_fail = await self._event_bus.subscribe(f"task:{task.task_id}:failed", handler)
                subs.append(sub_fail)
                self._callback_subs[task.task_id] = subs

        try:
            asyncio.create_task(self._notify("task.created", task))
        except RuntimeError:
            pass  # no event loop running (e.g., during testing sync calls)
        return task.task_id

    def update_progress(self, task_id: str, pct: int, msg: str) -> Task:
        task = self._store.get(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} not found")
        task.progress_pct = pct
        task.progress_msg = msg
        self._store.save(task)
        try:
            asyncio.create_task(self._notify("task.updated", task))
        except RuntimeError:
            pass  # no event loop running (e.g., during testing sync calls)
        return task

    async def complete_task(self, task_id: str, result: dict[str, Any] | str) -> Task:
        task = self._store.get(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} not found")
        task.status = "completed"
        task.progress_pct = 100
        task.result = result
        self._store.save(task)
        if self._event_bus is not None:
            await self._event_bus.publish(
                f"task:{task_id}:completed",
                payload={"task": task.to_dict()},
                source="broker",
            )
            for sub in self._callback_subs.pop(task_id, []):
                await self._event_bus.unsubscribe(sub)
        try:
            asyncio.create_task(self._notify("task.completed", task))
        except RuntimeError:
            pass
        return task

    async def fail_task(self, task_id: str, reason: str) -> Task:
        task = self._store.get(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} not found")
        task.status = "failed"
        task.result = reason
        self._store.save(task)
        if self._event_bus is not None:
            await self._event_bus.publish(
                f"task:{task_id}:failed",
                payload={"task": task.to_dict()},
                source="broker",
            )
            for sub in self._callback_subs.pop(task_id, []):
                await self._event_bus.unsubscribe(sub)
        try:
            asyncio.create_task(self._notify("task.failed", task))
        except RuntimeError:
            pass
        return task

    def get_task(self, task_id: str) -> Task | None:
        return self._store.get(task_id)

    def list_tasks(self) -> list[Task]:
        return self._store.all()
