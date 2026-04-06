from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from harness_claw.gateway.event_bus import LocalEventBus
from harness_claw.gateway.task_store import Task
from harness_claw.runtime.plan_executor import PlanExecutor
from harness_claw.runtime.plan_parser import PlanTask


def make_task(tid: str, caps: list[str], result: dict) -> Task:
    return Task(
        task_id=tid,
        delegated_by="plan-executor",
        delegated_to="s1",
        instructions="do it",
        caps_requested=caps,
        status="completed",
        result=result,
    )


def make_executor(broker, event_bus):
    progress = []

    async def on_progress(msg: str) -> None:
        progress.append(msg)

    executor = PlanExecutor(broker=broker, event_bus=event_bus, on_progress=on_progress)
    executor._progress_log = progress  # expose for assertions
    return executor


def build_fake_delegate(event_bus, tasks_store, result_factory):
    """
    Returns an async fake for broker.delegate that:
    - Creates a completed Task with result_factory(call_number, caps)
    - Fires the EventBus completed event after yielding
    """
    counter = [0]

    async def fake_delegate(delegated_by, caps, instructions, **kwargs):
        counter[0] += 1
        tid = f"t{counter[0]}"
        result = result_factory(counter[0], caps)
        task = make_task(tid, caps, result)
        tasks_store[tid] = task

        async def fire():
            await asyncio.sleep(0)
            await event_bus.publish(f"task:{tid}:completed", {}, "test")

        asyncio.ensure_future(fire())
        return tid

    return fake_delegate


@pytest.mark.asyncio
async def test_run_task_all_approve():
    """Happy path: writer implements, both reviewers approve, task is DONE."""
    event_bus = LocalEventBus()
    broker = MagicMock()
    tasks_store: dict[str, Task] = {}
    broker.get_task.side_effect = lambda tid: tasks_store.get(tid)

    def result_factory(call_num, caps):
        if "python" in caps or "typescript" in caps:
            return {"summary": "implemented", "files_changed": ["foo.py"]}
        return {"verdict": "APPROVE", "summary": "ok", "findings": []}

    broker.delegate = AsyncMock(side_effect=build_fake_delegate(event_bus, tasks_store, result_factory))
    executor = make_executor(broker, event_bus)

    plan_task = PlanTask(index=1, title="My Task", full_text="## Task 1: My Task\n\nDo it.")
    result = await executor._run_task(plan_task, total=3)

    assert result == "DONE: My Task"
    # 1 writer + 2 parallel reviewers
    assert broker.delegate.call_count == 3
    assert any("APPROVED" in m for m in executor._progress_log)


@pytest.mark.asyncio
async def test_run_task_revise_then_approve():
    """Round 1 reviewers REVISE, round 2 reviewers APPROVE."""
    event_bus = LocalEventBus()
    broker = MagicMock()
    tasks_store: dict[str, Task] = {}
    broker.get_task.side_effect = lambda tid: tasks_store.get(tid)

    review_call = [0]

    def result_factory(call_num, caps):
        if "python" in caps or "typescript" in caps:
            return {"summary": "done", "files_changed": []}
        review_call[0] += 1
        if review_call[0] <= 2:
            return {"verdict": "REVISE", "summary": "issues", "findings": [
                {"type": "missing", "description": "test missing"}
            ]}
        return {"verdict": "APPROVE", "summary": "ok", "findings": []}

    broker.delegate = AsyncMock(side_effect=build_fake_delegate(event_bus, tasks_store, result_factory))
    executor = make_executor(broker, event_bus)

    plan_task = PlanTask(index=1, title="My Task", full_text="## Task 1: My Task\n\nDo it.")
    result = await executor._run_task(plan_task, total=1)

    assert result == "DONE: My Task"
    # writer + 2 reviewers (revise) + rewrite + 2 reviewers (approve)
    assert broker.delegate.call_count == 6
    assert any("REVISE" in m for m in executor._progress_log)
    assert any("APPROVED" in m for m in executor._progress_log)


@pytest.mark.asyncio
async def test_run_task_passes_previous_findings_on_rewrite():
    """Round 2 review instructions must mention the finding from round 1."""
    event_bus = LocalEventBus()
    broker = MagicMock()
    tasks_store: dict[str, Task] = {}
    broker.get_task.side_effect = lambda tid: tasks_store.get(tid)

    review_call = [0]
    review_instructions: list[str] = []

    def result_factory(call_num, caps):
        if "python" in caps or "typescript" in caps:
            return {"summary": "done", "files_changed": []}
        review_call[0] += 1
        if review_call[0] <= 2:
            return {"verdict": "REVISE", "summary": "bad", "findings": [
                {"type": "missing", "description": "unique_marker_xyz"}
            ]}
        return {"verdict": "APPROVE", "summary": "ok", "findings": []}

    async def fake_delegate(delegated_by, caps, instructions, **kwargs):
        if "spec-review" in caps or "code-review" in caps:
            review_instructions.append(instructions)

        counter = [0]
        counter[0] = len(tasks_store) + 1
        tid = f"t{counter[0]}"
        result = result_factory(counter[0], caps)
        task = make_task(tid, caps, result)
        tasks_store[tid] = task

        async def fire():
            await asyncio.sleep(0)
            await event_bus.publish(f"task:{tid}:completed", {}, "test")

        asyncio.ensure_future(fire())
        return tid

    broker.delegate = AsyncMock(side_effect=fake_delegate)
    executor = make_executor(broker, event_bus)

    plan_task = PlanTask(index=1, title="My Task", full_text="## Task 1: My Task\n\nDo it.")
    await executor._run_task(plan_task, total=1)

    # Round 2 review instructions should contain the finding from round 1
    assert len(review_instructions) >= 4  # 2 per round × 2 rounds
    assert "unique_marker_xyz" in review_instructions[2]  # round 2 spec-review
    assert "unique_marker_xyz" in review_instructions[3]  # round 2 code-review


@pytest.mark.asyncio
async def test_run_task_stuck_after_max_rounds():
    """If reviewers always REVISE, task is marked STUCK after 5 rounds."""
    event_bus = LocalEventBus()
    broker = MagicMock()
    tasks_store: dict[str, Task] = {}
    broker.get_task.side_effect = lambda tid: tasks_store.get(tid)

    def result_factory(call_num, caps):
        if "python" in caps or "typescript" in caps:
            return {"summary": "done", "files_changed": []}
        return {"verdict": "REVISE", "summary": "still bad", "findings": [
            {"type": "missing", "description": "still broken"}
        ]}

    broker.delegate = AsyncMock(side_effect=build_fake_delegate(event_bus, tasks_store, result_factory))
    executor = make_executor(broker, event_bus)

    plan_task = PlanTask(index=1, title="Hard Task", full_text="## Task 1: Hard Task\n\nThis is hard.")
    result = await executor._run_task(plan_task, total=1)

    assert result.startswith("STUCK: Hard Task")
    assert any("STUCK" in m for m in executor._progress_log)


@pytest.mark.asyncio
async def test_run_task_calls_pool_acquire_before_delegate():
    """pool.acquire() must be called with the right caps before each broker.delegate()."""
    event_bus = LocalEventBus()
    broker = MagicMock()
    tasks_store: dict[str, Task] = {}
    broker.get_task.side_effect = lambda tid: tasks_store.get(tid)

    def result_factory(call_num, caps):
        if "python" in caps or "typescript" in caps:
            return {"summary": "done", "files_changed": []}
        return {"verdict": "APPROVE", "summary": "ok", "findings": []}

    broker.delegate = AsyncMock(side_effect=build_fake_delegate(event_bus, tasks_store, result_factory))

    acquired_caps: list[list[str]] = []
    pool = MagicMock()
    pool.acquire = AsyncMock(side_effect=lambda caps: acquired_caps.append(caps))

    progress = []
    async def on_progress(msg: str) -> None:
        progress.append(msg)

    executor = PlanExecutor(broker=broker, event_bus=event_bus, on_progress=on_progress, pool=pool)
    plan_task = PlanTask(index=1, title="My Task", full_text="## Task 1: My Task\n\nDo it.")
    result = await executor._run_task(plan_task, total=1)

    assert result == "DONE: My Task"
    assert ["python", "typescript"] in acquired_caps
    assert ["spec-review"] in acquired_caps
    assert ["code-review"] in acquired_caps
