import asyncio
import pytest
from harness_claw.runtime.limit_enforcer import LimitEnforcer


@pytest.mark.asyncio
async def test_cost_limit_fires_callback():
    fired = []

    async def on_exceeded(reason: str, detail: str) -> None:
        fired.append((reason, detail))

    enforcer = LimitEnforcer("s1", max_cost=1.0, max_time=None, on_exceeded=on_exceeded)
    enforcer.check_cost(0.5)
    await asyncio.sleep(0)
    assert fired == []

    enforcer.check_cost(1.01)
    await asyncio.sleep(0)
    assert len(fired) == 1
    assert fired[0][0] == "cost"
    assert "1.01" in fired[0][1]
    assert "1.0" in fired[0][1]


@pytest.mark.asyncio
async def test_cost_limit_fires_only_once():
    fired = []

    async def on_exceeded(reason: str, detail: str) -> None:
        fired.append(reason)

    enforcer = LimitEnforcer("s1", max_cost=1.0, max_time=None, on_exceeded=on_exceeded)
    enforcer.check_cost(1.01)
    enforcer.check_cost(1.50)
    enforcer.check_cost(2.00)
    await asyncio.sleep(0)
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_time_limit_fires_callback(monkeypatch):
    import time
    fired = []

    async def on_exceeded(reason: str, detail: str) -> None:
        fired.append((reason, detail))

    enforcer = LimitEnforcer("s1", max_cost=None, max_time=10, on_exceeded=on_exceeded)

    # Simulate 11 seconds elapsed
    monkeypatch.setattr(time, "monotonic", lambda: enforcer._started_at + 11)
    enforcer.check_time()
    await asyncio.sleep(0)
    assert len(fired) == 1
    assert fired[0][0] == "time"


@pytest.mark.asyncio
async def test_no_limit_never_fires():
    fired = []

    async def on_exceeded(reason: str, detail: str) -> None:
        fired.append(reason)

    enforcer = LimitEnforcer("s1", max_cost=None, max_time=None, on_exceeded=on_exceeded)
    enforcer.check_cost(9999.0)
    enforcer.check_time()
    await asyncio.sleep(0)
    assert fired == []


@pytest.mark.asyncio
async def test_time_limit_does_not_fire_before_exceeded(monkeypatch):
    import time
    fired = []

    async def on_exceeded(reason: str, detail: str) -> None:
        fired.append(reason)

    enforcer = LimitEnforcer("s1", max_cost=None, max_time=60, on_exceeded=on_exceeded)
    monkeypatch.setattr(time, "monotonic", lambda: enforcer._started_at + 30)
    enforcer.check_time()
    await asyncio.sleep(0)
    assert fired == []
