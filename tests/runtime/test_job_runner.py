from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from harness_claw.runtime.job_runner import JobRunner
from harness_claw.role_registry import RoleRegistry
from harness_claw.session import Session
from harness_claw.runtime.session_store import SessionStore


def make_session(**kwargs) -> Session:
    defaults = dict(role_id="assistant", working_dir="/tmp", model="claude-sonnet-4-6")
    defaults.update(kwargs)
    return Session(**defaults)


def make_runner(sessions=None):
    registry = MagicMock(spec=RoleRegistry)
    role = MagicMock()
    role.system_prompt = "You are helpful."
    role.model = "claude-sonnet-4-6"
    role.scopes = ["agent:list"]
    role.caps = []
    registry.get.return_value = role

    store = MagicMock(spec=SessionStore)
    store.get.return_value = sessions[0] if sessions else make_session()
    store.all.return_value = sessions or []

    return JobRunner(registry, store), registry, store


async def test_start_session_spawns_pty():
    runner, _, store = make_runner()
    session = make_session(session_id="s1")
    store.get.return_value = session

    with patch("harness_claw.runtime.job_runner.PtySession") as MockPty:
        mock_pty = MagicMock()
        mock_pty.start = AsyncMock()
        MockPty.return_value = mock_pty

        with patch("harness_claw.runtime.job_runner.CostPoller"):
            await runner.start_session(session)

        MockPty.assert_called_once_with("s1")
        mock_pty.start.assert_called_once_with("You are helpful.", "claude-sonnet-4-6", "/tmp", extra_env=None)


async def test_write_forwards_to_pty():
    runner, _, store = make_runner()
    session = make_session(session_id="s1")

    with patch("harness_claw.runtime.job_runner.PtySession") as MockPty:
        mock_pty = MagicMock()
        mock_pty.start = AsyncMock()
        MockPty.return_value = mock_pty

        with patch("harness_claw.runtime.job_runner.CostPoller"):
            await runner.start_session(session)

        runner.write("s1", b"hello")
        mock_pty.write.assert_called_once_with(b"hello")


async def test_resize_forwards_to_pty():
    runner, _, store = make_runner()
    session = make_session(session_id="s1")

    with patch("harness_claw.runtime.job_runner.PtySession") as MockPty:
        mock_pty = MagicMock()
        mock_pty.start = AsyncMock()
        MockPty.return_value = mock_pty

        with patch("harness_claw.runtime.job_runner.CostPoller"):
            await runner.start_session(session)

        runner.resize("s1", cols=120, rows=40)
        mock_pty.resize.assert_called_once_with(cols=120, rows=40)


async def test_kill_session_kills_pty():
    runner, _, store = make_runner()
    session = make_session(session_id="s1")

    with patch("harness_claw.runtime.job_runner.PtySession") as MockPty:
        mock_pty = MagicMock()
        mock_pty.start = AsyncMock()
        MockPty.return_value = mock_pty

        with patch("harness_claw.runtime.job_runner.CostPoller"):
            await runner.start_session(session)

        runner.kill_session("s1")
        mock_pty.kill.assert_called_once()


async def test_broadcast_output_to_all_senders():
    runner, _, _ = make_runner()
    received_a = []
    received_b = []

    async def send_a(msg): received_a.append(msg)
    async def send_b(msg): received_b.append(msg)

    runner.add_sender(send_a)
    runner.add_sender(send_b)

    await runner._broadcast({"type": "output", "session_id": "s1", "data": "abc"})

    assert len(received_a) == 1
    assert len(received_b) == 1
    assert received_a[0]["data"] == "abc"
