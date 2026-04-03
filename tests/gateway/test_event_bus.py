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
    await asyncio.sleep(0)

    assert len(received) == 1
    assert received[0].topic == "topic.a"
    assert received[0].payload == {"key": "val"}
    assert received[0].source == "agent-1"


async def test_topic_isolation():
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
