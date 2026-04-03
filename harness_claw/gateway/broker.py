from __future__ import annotations

import asyncio
import heapq
import logging
import uuid

_logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from harness_claw.gateway.capability import AgentAdvertisement, CapabilityConnector
from harness_claw.gateway.event_bus import EventBus
from harness_claw.gateway.task_store import Task, TaskStore, TaskStoreProtocol


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


class Scheduler:
    """Priority queue that holds tasks waiting for an available agent.

    In-memory heapq ordered by (priority, created_at, task_id).
    SQLite is the source of truth — push() persists before enqueuing.
    """

    def __init__(
        self,
        connectors: list[CapabilityConnector],
        dispatcher: TaskDispatcher,
        store: TaskStoreProtocol,
        notify_fn: Callable | None = None,
        poll_interval: int = 30,
    ) -> None:
        self._connectors = connectors
        self._dispatcher = dispatcher
        self._store = store
        self._notify_fn = notify_fn
        self._poll_interval = poll_interval
        self._queue: list[tuple[int, str, str]] = []   # (priority, created_at, task_id)
        self._tasks: dict[str, Task] = {}               # task_id → Task
        self._poll_task: asyncio.Task | None = None

    def push(self, task: Task) -> None:
        """Persist task as queued and enqueue it."""
        task.status = "queued"
        self._store.save(task)
        heapq.heappush(self._queue, (task.priority, task.created_at, task.task_id))
        self._tasks[task.task_id] = task

    def recover(self, tasks: list[Task]) -> None:
        """Re-enqueue interrupted tasks on startup.
        Tasks that were running get resume=True."""
        for task in tasks:
            if task.status == "running":
                task.resume = True
            heapq.heappush(self._queue, (task.priority, task.created_at, task.task_id))
            self._tasks[task.task_id] = task

    async def drain(self) -> None:
        """Dispatch queued tasks to available agents, in priority order.
        Continues past tasks whose cap set has no available agent."""
        pending = sorted(self._queue)
        dispatched: set[str] = set()
        used_agents: set[str] = set()

        for _, _, task_id in pending:
            task = self._tasks.get(task_id)
            if task is None:
                dispatched.add(task_id)
                continue

            candidates: list[AgentAdvertisement] = []
            for connector in self._connectors:
                candidates.extend(await connector.query(task.caps_requested))

            # Filter out agents already assigned a task in this drain pass
            candidates = [a for a in candidates if a.session_id not in used_agents]

            if not candidates:
                continue

            agent = candidates[0]

            instructions = task.instructions
            if task.resume:
                instructions = (
                    "[RESUME] You were previously working on this task. "
                    "Your conversation history is intact — continue where you left off.\n\n"
                    + instructions
                )

            dispatch_task = Task(
                task_id=task.task_id,
                delegated_by=task.delegated_by,
                delegated_to=agent.session_id,
                instructions=instructions,
                caps_requested=task.caps_requested,
                context=task.context,
                status="running",
                priority=task.priority,
                resume=task.resume,
                callback=task.callback,
                created_at=task.created_at,
            )

            try:
                await self._dispatcher.dispatch(dispatch_task, agent)
                self._store.save(dispatch_task)
                dispatched.add(task_id)
                used_agents.add(agent.session_id)
                del self._tasks[task_id]
                if self._notify_fn is not None:
                    try:
                        asyncio.create_task(self._notify_fn("task.updated", dispatch_task))
                    except RuntimeError:
                        pass
            except Exception as exc:
                _logger.warning("Scheduler: dispatch failed for task %s, will retry: %s", task_id, exc)

        if dispatched:
            self._queue = [
                (p, c, tid) for p, c, tid in self._queue if tid not in dispatched
            ]
            heapq.heapify(self._queue)

    async def start_poll_loop(self) -> None:
        """Start background asyncio task that calls drain() every poll_interval seconds."""
        if self._poll_task is not None:
            return
        async def _loop() -> None:
            while True:
                await asyncio.sleep(self._poll_interval)
                await self.drain()

        self._poll_task = asyncio.create_task(_loop())

    async def stop(self) -> None:
        """Cancel the background poll loop."""
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None


class Broker:
    """Routes delegation requests to capability-matched agents."""

    def __init__(
        self,
        connectors: list[CapabilityConnector],
        dispatcher: TaskDispatcher,
        event_bus: EventBus | None = None,
        task_store: TaskStoreProtocol | None = None,
    ) -> None:
        self._connectors = connectors
        self._dispatcher = dispatcher
        self._event_bus = event_bus
        self._store = task_store or TaskStore()
        self._listeners: list[Any] = []
        self._callback_handlers: dict[str, Any] = {}  # session_id -> handler
        self._callback_subs: dict[str, list[Any]] = {}  # task_id -> [Subscription]
        self.scheduler = Scheduler(
            connectors=connectors,
            dispatcher=dispatcher,
            store=self._store,
            notify_fn=self._notify,
        )

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
        priority: int = 2,
    ) -> str:
        candidates: list[AgentAdvertisement] = []
        for connector in self._connectors:
            candidates.extend(await connector.query(caps))

        task = Task(
            task_id=str(uuid.uuid4()),
            delegated_by=delegated_by,
            delegated_to="",
            instructions=instructions,
            caps_requested=caps,
            context=context,
            status="queued",
            callback=callback,
            priority=priority,
        )

        if candidates:
            agent = candidates[0]
            task.delegated_to = agent.session_id
            task.status = "running"
            self._store.save(task)
            await self._dispatcher.dispatch(task, agent)
        else:
            self.scheduler.push(task)

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
        try:
            asyncio.create_task(self.scheduler.drain())
        except RuntimeError:
            pass
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
        try:
            asyncio.create_task(self.scheduler.drain())
        except RuntimeError:
            pass
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
