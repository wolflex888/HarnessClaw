from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from harness_claw.gateway.capability import AgentAdvertisement, LocalConnector
from harness_claw.role_registry import RoleConfig
from harness_claw.runtime.agent_pool import AgentPool
from harness_claw.session import Session


def make_role(role_id: str, caps: list[str]) -> RoleConfig:
    return RoleConfig(
        id=role_id,
        name=role_id,
        provider="claude-code",
        model="claude-sonnet-4-6",
        system_prompt="you are helpful",
        caps=caps,
    )


def make_registry(roles: list[RoleConfig]):
    reg = MagicMock()
    reg.all.return_value = roles
    reg.get.side_effect = lambda rid: next((r for r in roles if r.id == rid), None)
    return reg


def make_runner(connector: LocalConnector, roles: list[RoleConfig]) -> MagicMock:
    caps_by_role = {r.id: r.caps for r in roles}

    async def fake_start(session: Session) -> None:
        await connector.register(AgentAdvertisement(
            session_id=session.session_id,
            role_id=session.role_id,
            caps=caps_by_role.get(session.role_id, []),
            status="idle",
            task_count=0,
            connector="local",
        ))

    runner = MagicMock()
    runner.start_session = AsyncMock(side_effect=fake_start)
    runner.kill_session = MagicMock()
    runner._pty_sessions = {}  # real dict so _wait_until_ready short-circuits
    return runner


@pytest.mark.asyncio
async def test_acquire_spawns_agent_and_returns_session_id():
    roles = [make_role("code-writer", ["python", "typescript"])]
    connector = LocalConnector()
    runner = make_runner(connector, roles)
    registry = make_registry(roles)
    pool = AgentPool(runner=runner, connector=connector, registry=registry, working_dir="/tmp")

    session_id = await pool.acquire(["python", "typescript"])

    runner.start_session.assert_called_once()
    spawned: Session = runner.start_session.call_args[0][0]
    assert session_id == spawned.session_id
    assert spawned.role_id == "code-writer"
    assert spawned.working_dir == "/tmp"
    assert spawned.model == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_acquire_is_idempotent():
    roles = [make_role("code-writer", ["python", "typescript"])]
    connector = LocalConnector()
    runner = make_runner(connector, roles)
    registry = make_registry(roles)
    pool = AgentPool(runner=runner, connector=connector, registry=registry, working_dir="/tmp")

    sid1 = await pool.acquire(["python", "typescript"])
    sid2 = await pool.acquire(["python", "typescript"])

    assert sid1 == sid2
    assert runner.start_session.call_count == 1


@pytest.mark.asyncio
async def test_acquire_unknown_caps_raises_value_error():
    connector = LocalConnector()
    registry = make_registry([])
    runner = MagicMock()
    pool = AgentPool(runner=runner, connector=connector, registry=registry, working_dir="/tmp")

    with pytest.raises(ValueError, match="no role"):
        await pool.acquire(["unknown-cap"])


@pytest.mark.asyncio
async def test_acquire_none_runner_raises_runtime_error():
    roles = [make_role("code-writer", ["python"])]
    connector = LocalConnector()
    registry = make_registry(roles)
    pool = AgentPool(runner=None, connector=connector, registry=registry, working_dir="/tmp")

    with pytest.raises(RuntimeError, match="requires a runner"):
        await pool.acquire(["python"])


@pytest.mark.asyncio
async def test_acquire_timeout_raises():
    roles = [make_role("code-writer", ["python"])]
    connector = LocalConnector()

    async def slow_start(session: Session) -> None:
        pass  # doesn't register in connector

    runner = MagicMock()
    runner.start_session = AsyncMock(side_effect=slow_start)
    runner._pty_sessions = {}
    registry = make_registry(roles)
    pool = AgentPool(runner=runner, connector=connector, registry=registry,
                     working_dir="/tmp", acquire_timeout=1)

    with pytest.raises(TimeoutError):
        await pool.acquire(["python"])


@pytest.mark.asyncio
async def test_release_all_kills_owned_sessions():
    roles = [
        make_role("code-writer", ["python", "typescript"]),
        make_role("spec-reviewer", ["spec-review"]),
    ]
    connector = LocalConnector()
    runner = make_runner(connector, roles)
    registry = make_registry(roles)
    pool = AgentPool(runner=runner, connector=connector, registry=registry, working_dir="/tmp")

    await pool.acquire(["python", "typescript"])
    await pool.acquire(["spec-review"])
    await pool.release_all()

    assert runner.kill_session.call_count == 2
    assert pool._owned == {}


@pytest.mark.asyncio
async def test_release_all_continues_on_kill_error():
    roles = [
        make_role("code-writer", ["python"]),
        make_role("spec-reviewer", ["spec-review"]),
    ]
    connector = LocalConnector()
    runner = make_runner(connector, roles)
    runner.kill_session = MagicMock(side_effect=RuntimeError("boom"))
    registry = make_registry(roles)
    pool = AgentPool(runner=runner, connector=connector, registry=registry, working_dir="/tmp")

    await pool.acquire(["python"])
    await pool.acquire(["spec-review"])

    await pool.release_all()  # must not raise
    assert pool._owned == {}


@pytest.mark.asyncio
async def test_acquire_subset_caps_reuses_existing_session():
    """acquire(["python"]) reuses code-writer session already spawned for ["python", "typescript"]."""
    roles = [make_role("code-writer", ["python", "typescript"])]
    connector = LocalConnector()
    runner = make_runner(connector, roles)
    registry = make_registry(roles)
    pool = AgentPool(runner=runner, connector=connector, registry=registry, working_dir="/tmp")

    sid1 = await pool.acquire(["python", "typescript"])
    sid2 = await pool.acquire(["python"])

    assert sid1 == sid2
    assert runner.start_session.call_count == 1
