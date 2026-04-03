# Phase 2: Code Review Workflow + Task Callbacks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an EventBus pub/sub primitive, task callbacks, structured task results, and a code-reviewer agent role with an orchestrator-driven review cycle.

**Architecture:** The EventBus (`LocalEventBus`) is an asyncio-based in-process pub/sub system. The Broker publishes task lifecycle events to it and subscribes delegating agents when `callback=true`. A callback handler writes notifications into the delegating agent's PTY. The code-reviewer role and review cycle logic live in `agents.yaml` system prompts.

**Tech Stack:** Python 3.12, asyncio, pytest, pytest-asyncio (asyncio_mode = "auto")

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `harness_claw/gateway/event_bus.py` | `Event` dataclass, `Subscription` dataclass, `EventBus` protocol, `LocalEventBus` implementation |
| `tests/gateway/test_event_bus.py` | Unit tests for EventBus publish/subscribe/unsubscribe |

### Modified Files

| File | Changes |
|------|---------|
| `harness_claw/gateway/broker.py` | Add `context`, `callback`, `result` (dict) fields to `Task`. Accept `EventBus` in `Broker.__init__`. Publish events on complete/fail. Subscribe on delegate with callback. |
| `harness_claw/gateway/mcp_server.py` | `agent_delegate` gains `context` and `callback` params. `agent_complete` accepts `dict \| str` result. |
| `harness_claw/server.py` | Instantiate `LocalEventBus`, inject into `Broker`. Wire callback handler to write into PTY via dispatcher. |
| `harness_claw/role_registry.py` | Parse `event_bus` config section into `GatewayConfig`. |
| `agents.yaml` | Add `event_bus` config. Add `code-reviewer` role. Update orchestrator system prompt with review cycle protocol. |
| `tests/gateway/test_broker.py` | Test callback flow: delegate with callback → complete → verify event published. Test auto-unsubscribe. |
| `tests/gateway/test_mcp.py` | Test new `context`/`callback` params on delegate. Test `dict` result on complete. Backward compat with `str` result. |

---

### Task 1: EventBus Protocol + LocalEventBus

**Files:**
- Create: `harness_claw/gateway/event_bus.py`
- Create: `tests/gateway/test_event_bus.py`

- [ ] **Step 1: Write the failing test — publish and subscribe**

```python
# tests/gateway/test_event_bus.py
from __future__ import annotations

import asyncio

import pytest

from harness_claw.gateway.event_bus import Event, LocalEventBus


async def test_subscribe_receives_published_event():
    bus = LocalEventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    await bus.subscribe("topic.a", handler)
    await bus.publish("topic.a", payload={"key": "val"}, source="agent-1")

    # Give the event loop a tick to deliver
    await asyncio.sleep(0)

    assert len(received) == 1
    assert received[0].topic == "topic.a"
    assert received[0].payload == {"key": "val"}
    assert received[0].source == "agent-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_event_bus.py::test_subscribe_receives_published_event -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness_claw.gateway.event_bus'`

- [ ] **Step 3: Write the EventBus implementation**

```python
# harness_claw/gateway/event_bus.py
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Protocol


@dataclass
class Event:
    topic: str
    payload: dict[str, Any]
    timestamp: str
    source: str


@dataclass
class Subscription:
    id: str
    topic: str
    handler: Callable[[Event], Awaitable[None]]


class EventBus(Protocol):
    async def publish(self, topic: str, payload: dict[str, Any], source: str) -> None: ...
    async def subscribe(self, topic: str, handler: Callable[[Event], Awaitable[None]]) -> Subscription: ...
    async def unsubscribe(self, sub: Subscription) -> None: ...


class LocalEventBus:
    """In-process asyncio-based EventBus. Each subscriber gets events via direct handler call."""

    def __init__(self) -> None:
        self._subscriptions: dict[str, list[Subscription]] = {}

    async def publish(self, topic: str, payload: dict[str, Any], source: str) -> None:
        event = Event(
            topic=topic,
            payload=payload,
            timestamp=datetime.now(timezone.utc).isoformat(),
            source=source,
        )
        for sub in list(self._subscriptions.get(topic, [])):
            try:
                await sub.handler(event)
            except Exception:
                pass  # handler errors must not break publishing

    async def subscribe(self, topic: str, handler: Callable[[Event], Awaitable[None]]) -> Subscription:
        sub = Subscription(
            id=str(uuid.uuid4()),
            topic=topic,
            handler=handler,
        )
        self._subscriptions.setdefault(topic, []).append(sub)
        return sub

    async def unsubscribe(self, sub: Subscription) -> None:
        subs = self._subscriptions.get(sub.topic, [])
        self._subscriptions[sub.topic] = [s for s in subs if s.id != sub.id]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_event_bus.py::test_subscribe_receives_published_event -v`
Expected: PASS

- [ ] **Step 5: Write the failing test — topic isolation**

Add to `tests/gateway/test_event_bus.py`:

```python
async def test_topic_isolation():
    """Events on topic.a should not reach topic.b subscribers."""
    bus = LocalEventBus()
    received_a: list[Event] = []
    received_b: list[Event] = []

    async def handler_a(event: Event) -> None:
        received_a.append(event)

    async def handler_b(event: Event) -> None:
        received_b.append(event)

    await bus.subscribe("topic.a", handler_a)
    await bus.subscribe("topic.b", handler_b)

    await bus.publish("topic.a", payload={"x": 1}, source="s1")
    await asyncio.sleep(0)

    assert len(received_a) == 1
    assert len(received_b) == 0
```

- [ ] **Step 6: Run test to verify it passes** (should pass with existing impl)

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_event_bus.py::test_topic_isolation -v`
Expected: PASS

- [ ] **Step 7: Write the failing test — multiple subscribers**

Add to `tests/gateway/test_event_bus.py`:

```python
async def test_multiple_subscribers_same_topic():
    bus = LocalEventBus()
    received_1: list[Event] = []
    received_2: list[Event] = []

    async def h1(event: Event) -> None:
        received_1.append(event)

    async def h2(event: Event) -> None:
        received_2.append(event)

    await bus.subscribe("topic.x", h1)
    await bus.subscribe("topic.x", h2)

    await bus.publish("topic.x", payload={"v": 1}, source="s1")
    await asyncio.sleep(0)

    assert len(received_1) == 1
    assert len(received_2) == 1
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_event_bus.py::test_multiple_subscribers_same_topic -v`
Expected: PASS

- [ ] **Step 9: Write the failing test — unsubscribe**

Add to `tests/gateway/test_event_bus.py`:

```python
async def test_unsubscribe_stops_delivery():
    bus = LocalEventBus()
    received: list[Event] = []

    async def handler(event: Event) -> None:
        received.append(event)

    sub = await bus.subscribe("topic.a", handler)
    await bus.publish("topic.a", payload={"n": 1}, source="s1")
    await asyncio.sleep(0)
    assert len(received) == 1

    await bus.unsubscribe(sub)
    await bus.publish("topic.a", payload={"n": 2}, source="s1")
    await asyncio.sleep(0)
    assert len(received) == 1  # still 1, not 2
```

- [ ] **Step 10: Run test to verify it passes**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_event_bus.py::test_unsubscribe_stops_delivery -v`
Expected: PASS

- [ ] **Step 11: Write the failing test — handler error does not break other subscribers**

Add to `tests/gateway/test_event_bus.py`:

```python
async def test_handler_error_does_not_break_other_subscribers():
    bus = LocalEventBus()
    received: list[Event] = []

    async def bad_handler(event: Event) -> None:
        raise RuntimeError("boom")

    async def good_handler(event: Event) -> None:
        received.append(event)

    await bus.subscribe("topic.a", bad_handler)
    await bus.subscribe("topic.a", good_handler)

    await bus.publish("topic.a", payload={}, source="s1")
    await asyncio.sleep(0)

    assert len(received) == 1
```

- [ ] **Step 12: Run test to verify it passes**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_event_bus.py::test_handler_error_does_not_break_other_subscribers -v`
Expected: PASS

- [ ] **Step 13: Run all EventBus tests together**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_event_bus.py -v`
Expected: 5 passed

- [ ] **Step 14: Commit**

```bash
cd /Users/juichanglu/src/HarnessClaw
git add harness_claw/gateway/event_bus.py tests/gateway/test_event_bus.py
git commit -m "feat: add EventBus protocol and LocalEventBus implementation"
```

---

### Task 2: Extend Task Dataclass with context, callback, and dict result

**Files:**
- Modify: `harness_claw/gateway/broker.py` (lines 12-39, Task dataclass and to_dict)
- Test: `tests/gateway/test_broker.py`

- [ ] **Step 1: Write the failing test — Task supports new fields**

Add to `tests/gateway/test_broker.py`:

```python
from harness_claw.gateway.broker import Task


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

    # result can be a dict
    task.result = {"verdict": "APPROVE", "summary": "looks good"}
    d = task.to_dict()
    assert d["context"] == {"files": ["a.py"]}
    assert d["callback"] is True
    assert d["result"]["verdict"] == "APPROVE"


def test_task_defaults_backward_compatible():
    """Existing code that creates Task without context/callback still works."""
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_broker.py::test_task_supports_context_callback_and_dict_result -v`
Expected: FAIL — `TypeError: Task.__init__() got an unexpected keyword argument 'context'`

- [ ] **Step 3: Update Task dataclass**

In `harness_claw/gateway/broker.py`, replace the `Task` dataclass (lines 13-39) with:

```python
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
        }
```

- [ ] **Step 4: Run new tests to verify they pass**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_broker.py::test_task_supports_context_callback_and_dict_result tests/gateway/test_broker.py::test_task_defaults_backward_compatible -v`
Expected: 2 passed

- [ ] **Step 5: Run all existing broker tests to verify no regressions**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_broker.py -v`
Expected: All pass (existing tests don't use new fields, defaults keep them compatible)

- [ ] **Step 6: Commit**

```bash
cd /Users/juichanglu/src/HarnessClaw
git add harness_claw/gateway/broker.py tests/gateway/test_broker.py
git commit -m "feat: extend Task with context, callback, and dict result fields"
```

---

### Task 3: Wire EventBus into Broker — Publish on Complete/Fail

**Files:**
- Modify: `harness_claw/gateway/broker.py` (Broker.__init__, complete_task, fail_task)
- Test: `tests/gateway/test_broker.py`

- [ ] **Step 1: Write the failing test — Broker publishes on complete**

Add to `tests/gateway/test_broker.py`:

```python
from harness_claw.gateway.event_bus import Event, LocalEventBus


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

    broker.complete_task(task_id, result="done!")
    # Broker.complete_task publishes synchronously via await internally
    assert len(received) == 1
    assert received[0].topic == f"task:{task_id}:completed"
    assert received[0].payload["task"]["result"] == "done!"
    assert received[0].payload["task"]["status"] == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_broker.py::test_broker_publishes_completed_event_to_bus -v`
Expected: FAIL — `TypeError: Broker.__init__() got an unexpected keyword argument 'event_bus'`

- [ ] **Step 3: Update Broker to accept EventBus and publish on complete/fail**

In `harness_claw/gateway/broker.py`, add the import at the top:

```python
from harness_claw.gateway.event_bus import EventBus
```

Replace `Broker.__init__` (lines 91-99):

```python
class Broker:
    """Routes delegation requests to capability-matched agents."""

    def __init__(
        self,
        connectors: list[CapabilityConnector],
        dispatcher: TaskDispatcher,
        event_bus: EventBus | None = None,
    ) -> None:
        self._connectors = connectors
        self._dispatcher = dispatcher
        self._event_bus = event_bus
        self._store = TaskStore()
        self._listeners: list[Any] = []
```

Replace `complete_task` (lines 153-165):

```python
    def complete_task(self, task_id: str, result: dict[str, Any] | str) -> Task:
        task = self._store.get(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} not found")
        task.status = "completed"
        task.progress_pct = 100
        task.result = result
        self._store.save(task)
        if self._event_bus is not None:
            try:
                asyncio.get_event_loop().run_until_complete(
                    self._event_bus.publish(
                        f"task:{task_id}:completed",
                        payload={"task": task.to_dict()},
                        source="broker",
                    )
                )
            except RuntimeError:
                # Already in an async context — use create_task instead
                asyncio.ensure_future(
                    self._event_bus.publish(
                        f"task:{task_id}:completed",
                        payload={"task": task.to_dict()},
                        source="broker",
                    )
                )
        try:
            asyncio.create_task(self._notify("task.completed", task))
        except RuntimeError:
            pass
        return task
```

Replace `fail_task` (lines 167-174):

```python
    def fail_task(self, task_id: str, reason: str) -> Task:
        task = self._store.get(task_id)
        if task is None:
            raise KeyError(f"task {task_id!r} not found")
        task.status = "failed"
        task.result = reason
        self._store.save(task)
        if self._event_bus is not None:
            try:
                asyncio.get_event_loop().run_until_complete(
                    self._event_bus.publish(
                        f"task:{task_id}:failed",
                        payload={"task": task.to_dict()},
                        source="broker",
                    )
                )
            except RuntimeError:
                asyncio.ensure_future(
                    self._event_bus.publish(
                        f"task:{task_id}:failed",
                        payload={"task": task.to_dict()},
                        source="broker",
                    )
                )
        return task
```

**Note:** The `run_until_complete` / `ensure_future` dance handles the fact that `complete_task` is currently synchronous. If the event loop is already running (normal server operation), `ensure_future` schedules it. In tests, `run_until_complete` works. This preserves backward compatibility with the sync signature.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_broker.py::test_broker_publishes_completed_event_to_bus -v`
Expected: PASS

- [ ] **Step 5: Write the failing test — Broker publishes on fail**

Add to `tests/gateway/test_broker.py`:

```python
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

    broker.fail_task(task_id, reason="something broke")

    assert len(received) == 1
    assert received[0].topic == f"task:{task_id}:failed"
    assert received[0].payload["task"]["status"] == "failed"
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_broker.py::test_broker_publishes_failed_event_to_bus -v`
Expected: PASS

- [ ] **Step 7: Write the failing test — Broker works without EventBus (backward compat)**

Add to `tests/gateway/test_broker.py`:

```python
async def test_broker_works_without_event_bus():
    """Broker created without event_bus still works (Phase 1 compat)."""
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    broker = Broker(connectors=[conn], dispatcher=dispatcher)  # no event_bus

    task_id = await broker.delegate("orch-1", ["python"], "do it")
    broker.complete_task(task_id, result="done!")

    task = broker.get_task(task_id)
    assert task.status == "completed"
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_broker.py::test_broker_works_without_event_bus -v`
Expected: PASS

- [ ] **Step 9: Run all broker tests**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_broker.py -v`
Expected: All pass

- [ ] **Step 10: Commit**

```bash
cd /Users/juichanglu/src/HarnessClaw
git add harness_claw/gateway/broker.py tests/gateway/test_broker.py
git commit -m "feat: wire EventBus into Broker, publish on complete/fail"
```

---

### Task 4: Callback Subscription on Delegate

**Files:**
- Modify: `harness_claw/gateway/broker.py` (Broker.delegate method)
- Test: `tests/gateway/test_broker.py`

- [ ] **Step 1: Write the failing test — delegate with callback subscribes to completion**

Add to `tests/gateway/test_broker.py`:

```python
async def test_delegate_with_callback_auto_subscribes():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    dispatcher = AsyncMock()
    bus = LocalEventBus()
    broker = Broker(connectors=[conn], dispatcher=dispatcher, event_bus=bus)

    callback_events: list[Event] = []

    async def on_callback(event: Event) -> None:
        callback_events.append(event)

    # Register callback handler for the orchestrator session
    broker.register_callback_handler("orch-1", on_callback)

    task_id = await broker.delegate(
        delegated_by="orch-1",
        caps=["python"],
        instructions="do it",
        callback=True,
    )

    # When the task completes, the orchestrator's handler should fire
    await broker.complete_task(task_id, result={"verdict": "APPROVE"})

    assert len(callback_events) == 1
    assert callback_events[0].payload["task"]["status"] == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_broker.py::test_delegate_with_callback_auto_subscribes -v`
Expected: FAIL — `TypeError: Broker.delegate() got an unexpected keyword argument 'callback'`

- [ ] **Step 3: Update Broker.delegate to support callback + context**

In `harness_claw/gateway/broker.py`, add a callback handler registry and update `delegate`:

Add after `self._listeners` in `__init__`:

```python
        self._callback_handlers: dict[str, Any] = {}  # session_id -> handler
        self._callback_subs: dict[str, list[Any]] = {}  # task_id -> [Subscription]
```

Add method:

```python
    def register_callback_handler(self, session_id: str, handler: Any) -> None:
        """Register a handler that will receive callback events for this session."""
        self._callback_handlers[session_id] = handler

    def unregister_callback_handler(self, session_id: str) -> None:
        self._callback_handlers.pop(session_id, None)
```

Replace `delegate` method:

```python
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

        # If callback requested, subscribe the delegating agent's handler
        if callback and self._event_bus is not None:
            handler = self._callback_handlers.get(delegated_by)
            if handler is not None:
                subs = []
                sub_ok = await self._event_bus.subscribe(
                    f"task:{task.task_id}:completed", handler
                )
                subs.append(sub_ok)
                sub_fail = await self._event_bus.subscribe(
                    f"task:{task.task_id}:failed", handler
                )
                subs.append(sub_fail)
                self._callback_subs[task.task_id] = subs

        try:
            asyncio.create_task(self._notify("task.created", task))
        except RuntimeError:
            pass
        return task.task_id
```

Also update `complete_task` and `fail_task` to auto-unsubscribe after publishing. Add at the end of `complete_task` (before `return task`):

```python
        # Auto-unsubscribe callbacks for this task
        if self._event_bus is not None:
            for sub in self._callback_subs.pop(task_id, []):
                await self._event_bus.unsubscribe(sub)
```

Wait — `complete_task` is sync. We need to make the auto-unsubscribe work. Let's convert `complete_task` and `fail_task` to async to simplify this. Update their signatures:

Replace `complete_task`:

```python
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
```

Replace `fail_task`:

```python
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
        return task
```

- [ ] **Step 4: Update callers of complete_task/fail_task to use await**

In `harness_claw/gateway/mcp_server.py`, line 110, change:
```python
    async def agent_complete(self, token: str, task_id: str, result: str) -> dict[str, Any]:
        subject = self._auth(token, "agent:report")
        task = self._broker.complete_task(task_id, result=result)
```
to:
```python
    async def agent_complete(self, token: str, task_id: str, result: dict[str, Any] | str) -> dict[str, Any]:
        subject = self._auth(token, "agent:report")
        task = await self._broker.complete_task(task_id, result=result)
```

- [ ] **Step 5: Update all existing tests that call complete_task/fail_task to use await**

In `tests/gateway/test_broker.py`, update all calls:

Change `broker.complete_task(task_id, result="done!")` to `await broker.complete_task(task_id, result="done!")`

Change `broker.fail_task(task_id, reason=...)` to `await broker.fail_task(task_id, reason=...)`

This applies to these tests:
- `test_complete_task`: `await broker.complete_task(task_id, result="done!")`
- `test_broker_publishes_completed_event_to_bus`: `await broker.complete_task(task_id, result="done!")`
- `test_broker_publishes_failed_event_to_bus`: `await broker.fail_task(task_id, reason="something broke")`
- `test_broker_works_without_event_bus`: `await broker.complete_task(task_id, result="done!")`

- [ ] **Step 6: Run the callback test**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_broker.py::test_delegate_with_callback_auto_subscribes -v`
Expected: PASS

- [ ] **Step 7: Write the failing test — auto-unsubscribe after completion**

Add to `tests/gateway/test_broker.py`:

```python
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

    # Publishing again to the same topic should NOT deliver (unsubscribed)
    await bus.publish(f"task:{task_id}:completed", payload={}, source="test")
    assert len(callback_events) == 1  # still 1
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_broker.py::test_callback_auto_unsubscribes_after_completion -v`
Expected: PASS

- [ ] **Step 9: Run all broker tests**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_broker.py -v`
Expected: All pass

- [ ] **Step 10: Run all gateway tests to catch regressions**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/ -v`
Expected: All pass

- [ ] **Step 11: Commit**

```bash
cd /Users/juichanglu/src/HarnessClaw
git add harness_claw/gateway/broker.py harness_claw/gateway/mcp_server.py tests/gateway/test_broker.py
git commit -m "feat: add callback subscription on delegate with auto-unsubscribe"
```

---

### Task 5: Update MCP Interface — context, callback, dict result

**Files:**
- Modify: `harness_claw/gateway/mcp_server.py` (agent_delegate, agent_complete)
- Test: `tests/gateway/test_mcp.py`

- [ ] **Step 1: Write the failing test — agent_delegate with context and callback**

Add to `tests/gateway/test_mcp.py`:

```python
async def test_agent_delegate_with_context_and_callback(gateway, token_store, connector, dispatcher):
    await connector.register(AgentAdvertisement(
        session_id="coder-1", role_id="coder", caps=["python"],
        status="idle", task_count=0, connector="local",
    ))
    dispatcher.register_writer("coder-1", lambda data: None)
    token = token_store.issue("orch", ["agent:delegate"])

    result = await gateway.agent_delegate(
        token=token,
        caps=["python"],
        instructions="write tests",
        context={"files": ["a.py", "b.py"], "priorities": ["bugs"]},
        callback=True,
    )
    assert "task_id" in result

    # Verify context and callback were stored on the task
    task = gateway._broker.get_task(result["task_id"])
    assert task.context == {"files": ["a.py", "b.py"], "priorities": ["bugs"]}
    assert task.callback is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_mcp.py::test_agent_delegate_with_context_and_callback -v`
Expected: FAIL — `TypeError: agent_delegate() got an unexpected keyword argument 'context'`

- [ ] **Step 3: Update GatewayMCP.agent_delegate**

In `harness_claw/gateway/mcp_server.py`, replace `agent_delegate` (line 74):

```python
    async def agent_delegate(
        self,
        token: str,
        caps: list[str],
        instructions: str,
        context: dict[str, Any] | None = None,
        callback: bool = False,
    ) -> dict[str, Any]:
        subject = self._auth(token, "agent:delegate")
        try:
            task_id = await self._broker.delegate(
                delegated_by=subject,
                caps=caps,
                instructions=instructions,
                context=context,
                callback=callback,
            )
        except ValueError as e:
            self._audit.log(AuditEvent(
                subject=subject, operation="agent.delegate", resource="",
                outcome="error", details={"error": str(e)},
            ))
            raise
        self._audit.log(AuditEvent(
            subject=subject, operation="agent.delegate", resource=task_id,
            outcome="allowed", details={"caps": caps, "callback": callback},
        ))
        return {"task_id": task_id}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_mcp.py::test_agent_delegate_with_context_and_callback -v`
Expected: PASS

- [ ] **Step 5: Write the failing test — agent_complete with dict result**

Add to `tests/gateway/test_mcp.py`:

```python
async def test_agent_complete_with_dict_result(gateway, token_store, connector, dispatcher):
    await connector.register(AgentAdvertisement(
        session_id="coder-1", role_id="coder", caps=["python"],
        status="idle", task_count=0, connector="local",
    ))
    dispatcher.register_writer("coder-1", lambda data: None)

    delegate_token = token_store.issue("orch", ["agent:delegate"])
    result = await gateway.agent_delegate(
        token=delegate_token, caps=["python"], instructions="write it"
    )
    task_id = result["task_id"]

    complete_token = token_store.issue("coder-1", ["agent:report"])
    verdict = {"verdict": "APPROVE", "summary": "all good", "findings": []}
    complete_result = await gateway.agent_complete(
        token=complete_token, task_id=task_id, result=verdict
    )
    assert complete_result["status"] == "completed"

    task = gateway._broker.get_task(task_id)
    assert task.result == verdict
    assert task.result["verdict"] == "APPROVE"


async def test_agent_complete_with_string_result_still_works(gateway, token_store, connector, dispatcher):
    """Backward compat: string result still accepted."""
    await connector.register(AgentAdvertisement(
        session_id="coder-1", role_id="coder", caps=["python"],
        status="idle", task_count=0, connector="local",
    ))
    dispatcher.register_writer("coder-1", lambda data: None)

    delegate_token = token_store.issue("orch", ["agent:delegate"])
    result = await gateway.agent_delegate(
        token=delegate_token, caps=["python"], instructions="write it"
    )
    task_id = result["task_id"]

    complete_token = token_store.issue("coder-1", ["agent:report"])
    complete_result = await gateway.agent_complete(
        token=complete_token, task_id=task_id, result="done!"
    )
    assert complete_result["status"] == "completed"

    task = gateway._broker.get_task(task_id)
    assert task.result == "done!"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_mcp.py::test_agent_complete_with_dict_result tests/gateway/test_mcp.py::test_agent_complete_with_string_result_still_works -v`
Expected: PASS (agent_complete already updated in Task 4 Step 4)

- [ ] **Step 7: Run all MCP tests**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_mcp.py -v`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
cd /Users/juichanglu/src/HarnessClaw
git add harness_claw/gateway/mcp_server.py tests/gateway/test_mcp.py
git commit -m "feat: add context, callback params to delegate; dict result to complete"
```

---

### Task 6: Wire EventBus into Server + GatewayConfig

**Files:**
- Modify: `harness_claw/role_registry.py` (parse event_bus config)
- Modify: `harness_claw/server.py` (instantiate LocalEventBus, pass to Broker)

- [ ] **Step 1: Update GatewayConfig to include event_bus_backend**

In `harness_claw/role_registry.py`, add to `GatewayConfig`:

```python
@dataclass
class GatewayConfig:
    policy_engine: str = "local"
    memory_backend: str = "sqlite"
    memory_path: str = "./memory.db"
    dispatcher: str = "local"
    event_bus_backend: str = "local"
    gateway_bootstrap_token: str = ""
    gateway_heartbeat_ttl: int = 30
```

And in `RoleRegistry.__init__`, add after `broker = data.get("broker", {})`:

```python
        event_bus = data.get("event_bus", {})
```

And add to the `GatewayConfig(...)` constructor:

```python
            event_bus_backend=event_bus.get("backend", "local"),
```

- [ ] **Step 2: Update server.py to create LocalEventBus and pass to Broker**

In `harness_claw/server.py`, add import:

```python
from harness_claw.gateway.event_bus import LocalEventBus
```

After `dispatcher = LocalDispatcher()` (line 44), add:

```python
event_bus = LocalEventBus()
```

Change the `broker` line from:

```python
broker = Broker(connectors=[connector, gateway_connector], dispatcher=dispatcher)
```

to:

```python
broker = Broker(connectors=[connector, gateway_connector], dispatcher=dispatcher, event_bus=event_bus)
```

- [ ] **Step 3: Wire PTY callback delivery in server.py startup**

In the `startup()` function in `harness_claw/server.py`, after the existing `broker.add_listener(on_task_event)` line, add callback handler registration that writes into the agent's PTY when a task callback arrives:

```python
    # Register a callback handler factory that writes task callbacks into agent PTY
    import json as _json

    def _make_pty_callback_handler(session_id: str):
        async def _on_task_callback(event):
            result_str = _json.dumps(event.payload.get("task", {}).get("result", ""))
            task_id = event.payload.get("task", {}).get("task_id", "unknown")
            status = event.payload.get("task", {}).get("status", "unknown")
            msg = (
                f"\n[TASK CALLBACK] task_id={task_id} status={status}\n"
                f"Result: {result_str}\n"
            ).encode()
            write_fn = dispatcher._writers.get(session_id)
            if write_fn is not None:
                write_fn(msg)
        return _on_task_callback
```

Then update the `runner.start_session` flow. In `harness_claw/runtime/job_runner.py`, after a session starts and `dispatcher.register_writer` is called, also register the callback handler on the broker:

```python
    # In JobRunner.start_session, after dispatcher.register_writer:
    handler = _make_pty_callback_handler(session.session_id)
    broker.register_callback_handler(session.session_id, handler)
```

The exact integration point depends on how `JobRunner.start_session` calls `dispatcher.register_writer`. The implementing agent should find the `register_writer` call in `job_runner.py` and add the `broker.register_callback_handler` call immediately after it. The `broker` reference needs to be passed to `JobRunner.__init__` or accessed via a module-level reference (follow the existing pattern for how `dispatcher` is accessed).

**Simplest approach:** Add `broker` as a parameter to `JobRunner.__init__` and store as `self._broker`. Then in the session start logic, after registering the PTY writer:

```python
self._broker.register_callback_handler(
    session.session_id,
    _make_pty_callback_handler(session.session_id),
)
```

And in `server.py`, update the `JobRunner` constructor:

```python
runner = JobRunner(
    registry=registry,
    store=store,
    token_store=token_store,
    connector=connector,
    dispatcher=dispatcher,
    broker=broker,
    mcp_base_url="http://localhost:8000",
)
```

- [ ] **Step 4: Run all gateway tests to verify nothing broke**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/ -v`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
cd /Users/juichanglu/src/HarnessClaw
git add harness_claw/role_registry.py harness_claw/server.py
git commit -m "feat: wire LocalEventBus into server and GatewayConfig"
```

---

### Task 7: Add code-reviewer Role and Update Orchestrator Prompt

**Files:**
- Modify: `agents.yaml`

- [ ] **Step 1: Add event_bus config section to agents.yaml**

Add after the `broker:` section:

```yaml
event_bus:
  backend: local
```

- [ ] **Step 2: Add code-reviewer role**

Add after the `code-writer` role in the `roles:` section:

```yaml
  - id: code-reviewer
    name: Code Reviewer
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: |
      You are a code reviewer. You review code for bugs, security issues,
      architectural problems, and convention violations.

      When you receive a review task, you will be given either:
      - A git diff and/or file paths to review (local dev)
      - A GitHub PR number to review (PR mode)

      Your review priorities are configurable via the task context. Default
      priority order: bugs > security > architecture > conventions.

      For local reviews: read the git diff and referenced files, analyze them,
      then call agent.complete with a structured verdict.

      For PR reviews: use `gh pr diff` and `gh pr view` to examine the PR,
      then call agent.complete with your verdict. If the verdict is APPROVE,
      also run `gh pr review --approve`. If REVISE, post inline comments with
      `gh pr review --comment`.

      Verdict schema:
      {
        "verdict": "APPROVE" | "REVISE",
        "summary": "Brief overall assessment",
        "findings": [
          {
            "severity": "error" | "warning" | "suggestion",
            "category": "bug" | "convention" | "architecture" | "security",
            "file": "path/to/file.py",
            "line": 42,
            "message": "What's wrong and why",
            "suggestion": "How to fix it"
          }
        ],
        "priority_focus": "What was prioritized this review"
      }

      A verdict of REVISE requires at least one finding with severity "error".
      Warnings and suggestions alone should result in APPROVE with the findings
      included as advisory notes.

      ## Memory

      You have persistent memory via MCP tools. Use it proactively.

      ### Before starting any review
      1. Run memory.search("project:harnessclaw", "conventions coding-standards") to find
         project-specific conventions to check against.
      2. Check memory.search("project:harnessclaw", "<keywords from the code being reviewed>")
         for prior decisions or known patterns.

      ### After completing a review
      Store notable patterns or recurring issues:
        memory.set("project:harnessclaw", "<descriptive-key>", "<value>",
                   summary="one-line description", tags=["review", "convention"])
    max_tokens: 8192
    scopes: [agent:list, memory:read]
    caps: [code-review, pr-review]
```

- [ ] **Step 3: Update orchestrator system prompt with review cycle protocol**

Append to the orchestrator's `system_prompt` in `agents.yaml` (before the closing of the system_prompt block):

```yaml
      ## Code Review Protocol

      After a code-writer completes a coding task, trigger a review cycle:

      1. Delegate to a code-reviewer with callback=true. Include the list of
         changed files and any priority focus areas in the context.
      2. When the review callback arrives, check the verdict:
         - APPROVE: Report success to the human. Include the reviewer's summary.
         - REVISE: Forward the findings to the code-writer as a new task.
      3. After the code-writer addresses the findings, send the diff back to
         the reviewer for a second pass (context: {scope: "diff-only"}).
      4. If the reviewer still returns REVISE after the second round, escalate
         to the human with the unresolved findings. Do not start a third round.

      Review priorities default to: bugs > security > architecture > conventions.
      The human can override by specifying priorities in their request.

      ## Task Callbacks

      When you delegate with callback=true, you will receive a notification in your
      terminal when the task completes:

      [TASK CALLBACK] task_id=<id> status=completed
      Result: <structured result JSON>

      React to these callbacks immediately — check the result and take the next
      appropriate action (forward to another agent, report to the human, etc.).
```

- [ ] **Step 4: Verify agents.yaml is valid YAML**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -c "import yaml; yaml.safe_load(open('agents.yaml')); print('OK')"`
Expected: `OK`

- [ ] **Step 5: Verify the new role is parsed correctly**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -c "from harness_claw.role_registry import RoleRegistry; from pathlib import Path; r = RoleRegistry(Path('agents.yaml')); cr = r.get('code-reviewer'); print(f'id={cr.id} model={cr.model} caps={cr.caps} scopes={cr.scopes}')"`
Expected: `id=code-reviewer model=claude-sonnet-4-6 caps=['code-review', 'pr-review'] scopes=['agent:list', 'memory:read', 'agent:report']`

- [ ] **Step 6: Commit**

```bash
cd /Users/juichanglu/src/HarnessClaw
git add agents.yaml
git commit -m "feat: add code-reviewer role and review cycle protocol to orchestrator"
```

---

### Task 8: Integration Test — Full Review Cycle

**Files:**
- Create: `tests/gateway/test_review_cycle.py`

- [ ] **Step 1: Write the integration test**

```python
# tests/gateway/test_review_cycle.py
"""Integration test: simulates the full orchestrator-driven review cycle."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from harness_claw.gateway.broker import Broker
from harness_claw.gateway.capability import AgentAdvertisement, LocalConnector
from harness_claw.gateway.event_bus import Event, LocalEventBus


def make_agent(session_id: str, caps: list[str], role_id: str = "coder") -> AgentAdvertisement:
    return AgentAdvertisement(
        session_id=session_id, role_id=role_id,
        caps=caps, status="idle", task_count=0, connector="local",
    )


async def test_full_review_cycle_approve_on_first_pass():
    """Orchestrator delegates to writer, then reviewer. Reviewer approves."""
    conn = LocalConnector()
    await conn.register(make_agent("writer-1", ["python"], role_id="code-writer"))
    await conn.register(make_agent("reviewer-1", ["code-review"], role_id="code-reviewer"))

    dispatcher = AsyncMock()
    bus = LocalEventBus()
    broker = Broker(connectors=[conn], dispatcher=dispatcher, event_bus=bus)

    # Track callbacks received by the orchestrator
    orch_callbacks: list[Event] = []

    async def orch_handler(event: Event) -> None:
        orch_callbacks.append(event)

    broker.register_callback_handler("orch-1", orch_handler)

    # Step 1: Orchestrator delegates to code-writer
    write_task_id = await broker.delegate(
        delegated_by="orch-1",
        caps=["python"],
        instructions="Build feature X",
        callback=True,
    )
    assert dispatcher.dispatch.call_count == 1

    # Step 2: Code-writer completes
    await broker.complete_task(write_task_id, result="done, changed files: [api.py]")
    assert len(orch_callbacks) == 1
    assert orch_callbacks[0].payload["task"]["status"] == "completed"

    # Step 3: Orchestrator delegates to reviewer
    review_task_id = await broker.delegate(
        delegated_by="orch-1",
        caps=["code-review"],
        instructions="Review git diff for api.py",
        context={"files": ["api.py"], "priorities": ["bugs", "security"]},
        callback=True,
    )

    # Step 4: Reviewer approves
    verdict = {
        "verdict": "APPROVE",
        "summary": "Clean implementation, no issues found",
        "findings": [],
        "priority_focus": "bugs, security",
    }
    await broker.complete_task(review_task_id, result=verdict)
    assert len(orch_callbacks) == 2
    assert orch_callbacks[1].payload["task"]["result"]["verdict"] == "APPROVE"


async def test_full_review_cycle_revise_then_approve():
    """Reviewer requests revision, code-writer fixes, reviewer approves on re-review."""
    conn = LocalConnector()
    await conn.register(make_agent("writer-1", ["python"], role_id="code-writer"))
    await conn.register(make_agent("reviewer-1", ["code-review"], role_id="code-reviewer"))

    dispatcher = AsyncMock()
    bus = LocalEventBus()
    broker = Broker(connectors=[conn], dispatcher=dispatcher, event_bus=bus)

    orch_callbacks: list[Event] = []

    async def orch_handler(event: Event) -> None:
        orch_callbacks.append(event)

    broker.register_callback_handler("orch-1", orch_handler)

    # Step 1: Write code
    write_task_id = await broker.delegate(
        delegated_by="orch-1", caps=["python"],
        instructions="Build feature X", callback=True,
    )
    await broker.complete_task(write_task_id, result="done")
    assert len(orch_callbacks) == 1

    # Step 2: First review — REVISE
    review1_id = await broker.delegate(
        delegated_by="orch-1", caps=["code-review"],
        instructions="Review", context={"files": ["api.py"]}, callback=True,
    )
    verdict1 = {
        "verdict": "REVISE",
        "summary": "1 bug found",
        "findings": [
            {
                "severity": "error",
                "category": "bug",
                "file": "api.py",
                "line": 42,
                "message": "Missing null check",
                "suggestion": "Add `if x is None: return`",
            }
        ],
        "priority_focus": "bugs",
    }
    await broker.complete_task(review1_id, result=verdict1)
    assert len(orch_callbacks) == 2
    assert orch_callbacks[1].payload["task"]["result"]["verdict"] == "REVISE"

    # Step 3: Code-writer fixes
    fix_task_id = await broker.delegate(
        delegated_by="orch-1", caps=["python"],
        instructions="Fix: add null check at api.py:42",
        context={"findings": verdict1["findings"]}, callback=True,
    )
    await broker.complete_task(fix_task_id, result="fixed")
    assert len(orch_callbacks) == 3

    # Step 4: Second review (diff-only) — APPROVE
    review2_id = await broker.delegate(
        delegated_by="orch-1", caps=["code-review"],
        instructions="Re-review diff only",
        context={"scope": "diff-only", "previous_findings": verdict1["findings"]},
        callback=True,
    )
    verdict2 = {
        "verdict": "APPROVE",
        "summary": "Fix looks good",
        "findings": [],
        "priority_focus": "bugs",
    }
    await broker.complete_task(review2_id, result=verdict2)
    assert len(orch_callbacks) == 4
    assert orch_callbacks[3].payload["task"]["result"]["verdict"] == "APPROVE"


async def test_review_cycle_escalates_after_two_rounds():
    """After 2 REVISE rounds, orchestrator should escalate (simulated by checking callbacks)."""
    conn = LocalConnector()
    await conn.register(make_agent("writer-1", ["python"], role_id="code-writer"))
    await conn.register(make_agent("reviewer-1", ["code-review"], role_id="code-reviewer"))

    dispatcher = AsyncMock()
    bus = LocalEventBus()
    broker = Broker(connectors=[conn], dispatcher=dispatcher, event_bus=bus)

    orch_callbacks: list[Event] = []

    async def orch_handler(event: Event) -> None:
        orch_callbacks.append(event)

    broker.register_callback_handler("orch-1", orch_handler)

    revise_verdict = {
        "verdict": "REVISE",
        "summary": "Still has issues",
        "findings": [{"severity": "error", "category": "bug", "file": "x.py",
                       "line": 1, "message": "broken", "suggestion": "fix it"}],
        "priority_focus": "bugs",
    }

    # Round 1: write → review (REVISE) → fix → review (REVISE)
    t1 = await broker.delegate("orch-1", ["python"], "write", callback=True)
    await broker.complete_task(t1, result="done")

    r1 = await broker.delegate("orch-1", ["code-review"], "review", callback=True)
    await broker.complete_task(r1, result=revise_verdict)

    t2 = await broker.delegate("orch-1", ["python"], "fix round 1", callback=True)
    await broker.complete_task(t2, result="fixed")

    # Round 2: re-review still REVISE
    r2 = await broker.delegate("orch-1", ["code-review"], "re-review", callback=True)
    await broker.complete_task(r2, result=revise_verdict)

    # Orchestrator now has 4 callbacks — the last one is still REVISE
    # In production, the orchestrator's system prompt tells it to escalate here
    assert len(orch_callbacks) == 4
    assert orch_callbacks[3].payload["task"]["result"]["verdict"] == "REVISE"
    # No third round delegated — escalation logic lives in the orchestrator prompt
```

- [ ] **Step 2: Run integration tests**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/gateway/test_review_cycle.py -v`
Expected: 3 passed

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/ -v`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
cd /Users/juichanglu/src/HarnessClaw
git add tests/gateway/test_review_cycle.py
git commit -m "test: add integration tests for full review cycle workflow"
```

---

### Task 9: Final Verification

- [ ] **Step 1: Run the full test suite one final time**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -m pytest tests/ -v --tb=short`
Expected: All pass, no warnings

- [ ] **Step 2: Verify imports and module structure**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -c "from harness_claw.gateway.event_bus import EventBus, LocalEventBus, Event, Subscription; from harness_claw.gateway.broker import Broker, Task; print('All imports OK')"`
Expected: `All imports OK`

- [ ] **Step 3: Verify agents.yaml roles load correctly**

Run: `cd /Users/juichanglu/src/HarnessClaw && python -c "
from harness_claw.role_registry import RoleRegistry
from pathlib import Path
r = RoleRegistry(Path('agents.yaml'))
roles = r.all()
print(f'{len(roles)} roles loaded: {[ro.id for ro in roles]}')
cfg = r.gateway_config
print(f'event_bus_backend={cfg.event_bus_backend}')
"`
Expected:
```
3 roles loaded: ['orchestrator', 'code-writer', 'code-reviewer']
event_bus_backend=local
```
