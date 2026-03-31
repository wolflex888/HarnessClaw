from __future__ import annotations
import pytest
from unittest.mock import AsyncMock
from harness_claw.gateway.broker import Broker, Task, TaskStore, LocalDispatcher
from harness_claw.gateway.capability import LocalConnector, AgentAdvertisement


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
    broker.complete_task(task_id, result="done!")

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
