from __future__ import annotations
import pytest
from unittest.mock import AsyncMock
from harness_claw.gateway.broker import Broker, Task, TaskStore, LocalDispatcher
from harness_claw.gateway.capability import LocalConnector, AgentAdvertisement
from harness_claw.gateway.event_bus import Event, LocalEventBus


def make_agent(session_id: str, caps: list[str]) -> AgentAdvertisement:
    return AgentAdvertisement(
        session_id=session_id, role_id="coder",
        caps=caps, status="idle", task_count=0, connector="local",
    )


async def test_delegate_creates_task():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))

    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    task_id = await broker.delegate(
        delegated_by="orchestrator-1",
        caps=["python"],
        instructions="Write a hello world function",
    )
    assert task_id is not None
    task = broker.get_task(task_id)
    assert task is not None
    assert task.delegated_by == "orchestrator-1"
    assert task.delegated_to == "s1"
    assert task.status == "running"
    dispatcher.dispatch.assert_called_once()


async def test_delegate_raises_when_no_agent_matches():
    conn = LocalConnector()
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    with pytest.raises(ValueError, match="no agent"):
        await broker.delegate("orch-1", caps=["nonexistent-cap"], instructions="do it")


async def test_update_progress():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    task_id = await broker.delegate("orch-1", ["python"], "do it")
    broker.update_progress(task_id, pct=50, msg="halfway there")

    task = broker.get_task(task_id)
    assert task.progress_pct == 50
    assert task.progress_msg == "halfway there"
    assert task.status == "running"


async def test_complete_task():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    task_id = await broker.delegate("orch-1", ["python"], "do it")
    await broker.complete_task(task_id, result="done!")

    task = broker.get_task(task_id)
    assert task.status == "completed"
    assert task.result == "done!"


async def test_list_tasks_returns_all():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    await conn.register(make_agent("s2", ["typescript"]))
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)

    await broker.delegate("orch-1", ["python"], "task 1")
    await broker.delegate("orch-1", ["typescript"], "task 2")

    tasks = broker.list_tasks()
    assert len(tasks) == 2


# --- Task 2: Extended Task dataclass tests ---

def test_task_supports_context_callback_and_dict_result():
    task = Task(
        task_id="t1",
        delegated_by="orch",
        delegated_to="coder",
        instructions="write code",
        caps_requested=["python"],
        context={"files": ["a.py"]},
        callback=True,
    )
    assert task.context == {"files": ["a.py"]}
    assert task.callback is True
    task.result = {"verdict": "APPROVE", "summary": "looks good"}
    d = task.to_dict()
    assert d["context"] == {"files": ["a.py"]}
    assert d["callback"] is True
    assert d["result"]["verdict"] == "APPROVE"


def test_task_defaults_backward_compatible():
    task = Task(
        task_id="t2",
        delegated_by="orch",
        delegated_to="coder",
        instructions="do it",
        caps_requested=["python"],
    )
    assert task.context is None
    assert task.callback is False
    assert task.result is None


# --- Task 3: EventBus wired into Broker tests ---

async def test_broker_publishes_completed_event_to_bus():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    bus = LocalEventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    broker = Broker(connectors=[conn], dispatcher=dispatcher, event_bus=bus)
    task_id = await broker.delegate("orch-1", ["python"], "do it")
    await bus.subscribe(f"task:{task_id}:completed", handler)
    await broker.complete_task(task_id, result="done!")

    assert len(received) == 1
    assert received[0].topic == f"task:{task_id}:completed"
    assert received[0].payload["task"]["result"] == "done!"
    assert received[0].payload["task"]["status"] == "completed"


async def test_broker_publishes_failed_event_to_bus():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    bus = LocalEventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    broker = Broker(connectors=[conn], dispatcher=dispatcher, event_bus=bus)
    task_id = await broker.delegate("orch-1", ["python"], "do it")
    await bus.subscribe(f"task:{task_id}:failed", handler)
    await broker.fail_task(task_id, reason="something broke")

    assert len(received) == 1
    assert received[0].topic == f"task:{task_id}:failed"
    assert received[0].payload["task"]["status"] == "failed"


async def test_broker_works_without_event_bus():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)
    task_id = await broker.delegate("orch-1", ["python"], "do it")
    await broker.complete_task(task_id, result="done!")
    task = broker.get_task(task_id)
    assert task.status == "completed"


# --- Task 4: Callback subscription tests ---

async def test_delegate_with_callback_auto_subscribes():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    bus = LocalEventBus()
    broker = Broker(connectors=[conn], dispatcher=dispatcher, event_bus=bus)
    callback_events: list[Event] = []

    async def on_callback(event: Event) -> None:
        callback_events.append(event)

    broker.register_callback_handler("orch-1", on_callback)
    task_id = await broker.delegate(
        delegated_by="orch-1", caps=["python"],
        instructions="do it", callback=True,
    )
    await broker.complete_task(task_id, result={"verdict": "APPROVE"})

    assert len(callback_events) == 1
    assert callback_events[0].payload["task"]["status"] == "completed"


async def test_callback_auto_unsubscribes_after_completion():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    bus = LocalEventBus()
    broker = Broker(connectors=[conn], dispatcher=dispatcher, event_bus=bus)
    callback_events: list[Event] = []

    async def on_callback(event: Event) -> None:
        callback_events.append(event)

    broker.register_callback_handler("orch-1", on_callback)
    task_id = await broker.delegate(
        delegated_by="orch-1", caps=["python"],
        instructions="do it", callback=True,
    )
    await broker.complete_task(task_id, result="done")
    assert len(callback_events) == 1

    await bus.publish(f"task:{task_id}:completed", payload={}, source="test")
    assert len(callback_events) == 1  # still 1 — unsubscribed


async def test_task_persists_across_broker_instances(tmp_path):
    from harness_claw.gateway.task_store import SqliteTaskStore

    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    store = SqliteTaskStore(tmp_path / "tasks.db")
    broker1 = Broker(connectors=[conn], dispatcher=AsyncMock(), task_store=store)

    task_id = await broker1.delegate(
        delegated_by="orchestrator-1",
        caps=["python"],
        instructions="survive the restart",
    )

    # New Broker with the same store — confirms Broker reads through to store on get_task
    broker2 = Broker(connectors=[conn], dispatcher=AsyncMock(), task_store=store)
    task = broker2.get_task(task_id)
    assert task is not None
    assert task.task_id == task_id
    assert task.instructions == "survive the restart"


# --- Task 2: Scheduler tests ---

from harness_claw.gateway.broker import Scheduler


def make_task(task_id: str, caps: list[str], priority: int = 2, status: str = "queued") -> Task:
    return Task(
        task_id=task_id,
        delegated_by="orch",
        delegated_to="",
        instructions=f"do task {task_id}",
        caps_requested=caps,
        priority=priority,
        status=status,
    )


async def test_scheduler_push_adds_to_queue():
    store = TaskStore()
    scheduler = Scheduler(connectors=[], dispatcher=AsyncMock(), store=store)
    task = make_task("t1", ["python"])
    scheduler.push(task)
    assert len(scheduler._queue) == 1
    assert store.get("t1").status == "queued"


async def test_scheduler_drain_dispatches_matching_task():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    store = TaskStore()
    scheduler = Scheduler(connectors=[conn], dispatcher=dispatcher, store=store)

    task = make_task("t1", ["python"])
    scheduler.push(task)
    await scheduler.drain()

    assert len(scheduler._queue) == 0
    assert store.get("t1").status == "running"
    dispatcher.dispatch.assert_called_once()


async def test_scheduler_drain_respects_priority():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    store = TaskStore()
    scheduler = Scheduler(connectors=[conn], dispatcher=dispatcher, store=store)

    low = make_task("low", ["python"], priority=3)
    high = make_task("high", ["python"], priority=1)
    normal = make_task("normal", ["python"], priority=2)

    scheduler.push(low)
    scheduler.push(high)
    scheduler.push(normal)

    # Only one agent — drain dispatches highest priority first
    await scheduler.drain()

    dispatched_task = dispatcher.dispatch.call_args[0][0]
    assert dispatched_task.task_id == "high"
    assert len(scheduler._queue) == 2  # low and normal still queued


async def test_scheduler_recover_re_enqueues_queued_task():
    store = TaskStore()
    scheduler = Scheduler(connectors=[], dispatcher=AsyncMock(), store=store)
    task = make_task("t1", ["python"], status="queued")
    scheduler.recover([task])
    assert len(scheduler._queue) == 1
    assert scheduler._tasks["t1"].resume is False


async def test_scheduler_recover_sets_resume_on_running_task():
    store = TaskStore()
    scheduler = Scheduler(connectors=[], dispatcher=AsyncMock(), store=store)
    task = make_task("t1", ["python"], status="running")
    scheduler.recover([task])
    assert scheduler._tasks["t1"].resume is True


async def test_scheduler_drain_prepends_resume_preamble():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    store = TaskStore()
    scheduler = Scheduler(connectors=[conn], dispatcher=dispatcher, store=store)

    task = make_task("t1", ["python"], status="running")
    task.resume = True
    scheduler.recover([task])
    await scheduler.drain()

    dispatched_task = dispatcher.dispatch.call_args[0][0]
    assert "[RESUME]" in dispatched_task.instructions
    assert "do task t1" in dispatched_task.instructions


async def test_scheduler_drain_skips_task_when_no_agent():
    conn = LocalConnector()  # no agents registered
    dispatcher = AsyncMock()
    store = TaskStore()
    scheduler = Scheduler(connectors=[conn], dispatcher=dispatcher, store=store)

    task = make_task("t1", ["python"])
    scheduler.push(task)
    await scheduler.drain()

    assert len(scheduler._queue) == 1  # still queued
    assert store.get("t1").status == "queued"
    dispatcher.dispatch.assert_not_called()


async def test_scheduler_drain_retries_on_dispatch_failure():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    dispatcher.dispatch.side_effect = RuntimeError("writer not registered")
    store = TaskStore()
    scheduler = Scheduler(connectors=[conn], dispatcher=dispatcher, store=store)

    task = make_task("t1", ["python"])
    scheduler.push(task)
    await scheduler.drain()

    # Task should still be in queue (dispatch failed)
    assert len(scheduler._queue) == 1
