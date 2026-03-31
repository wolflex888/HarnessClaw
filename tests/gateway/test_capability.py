from __future__ import annotations
import pytest
from harness_claw.gateway.capability import LocalConnector, AgentAdvertisement


def make_agent(session_id: str, caps: list[str], task_count: int = 0) -> AgentAdvertisement:
    return AgentAdvertisement(
        session_id=session_id,
        role_id="coder",
        caps=caps,
        status="idle",
        task_count=task_count,
        connector="local",
    )


async def test_register_and_query():
    conn = LocalConnector()
    agent = make_agent("s1", ["python", "typescript"])
    await conn.register(agent)
    results = await conn.query(["python"])
    assert any(a.session_id == "s1" for a in results)


async def test_query_requires_all_caps():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    await conn.register(make_agent("s2", ["python", "typescript"]))
    results = await conn.query(["python", "typescript"])
    assert len(results) == 1
    assert results[0].session_id == "s2"


async def test_deregister_removes_agent():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    await conn.deregister("s1")
    results = await conn.query(["python"])
    assert results == []


async def test_query_returns_least_loaded_first():
    conn = LocalConnector()
    await conn.register(make_agent("busy", ["python"], task_count=5))
    await conn.register(make_agent("idle", ["python"], task_count=0))
    results = await conn.query(["python"])
    assert results[0].session_id == "idle"


async def test_query_empty_caps_returns_all():
    conn = LocalConnector()
    await conn.register(make_agent("s1", ["python"]))
    await conn.register(make_agent("s2", ["typescript"]))
    results = await conn.query([])
    assert len(results) == 2
