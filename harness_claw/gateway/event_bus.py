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
