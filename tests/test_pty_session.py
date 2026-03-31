from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch
import pytest

from harness_claw.pty_session import PtySession


@pytest.fixture
def mock_proc():
    proc = MagicMock()
    proc.isalive.return_value = True
    proc.read.return_value = b"Hello\r\n"
    proc.fd = 0  # select.select requires an integer fd
    return proc


# Patch select so tests don't block waiting for real I/O.
# Default: no data ready (timeout) — read loop relies on isalive() + task cancellation.
@pytest.fixture(autouse=True)
def mock_select(mock_proc):
    with patch("harness_claw.pty_session.select") as m:
        m.select.return_value = ([], [], [])
        yield m


async def test_start_spawns_claude_with_correct_args(mock_proc):
    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc) as mock_spawn:
        pty = PtySession("sess-1")
        await pty.start("You are helpful.", "claude-sonnet-4-6", "/tmp")
        mock_spawn.assert_called_once()
        args = mock_spawn.call_args
        cmd = args[0][0]
        assert cmd[0] == "claude"
        assert "--system-prompt" in cmd
        assert "You are helpful." in cmd
        assert "--model" in cmd
        assert "claude-sonnet-4-6" in cmd
        pty.kill()


async def test_write_sends_bytes_to_proc(mock_proc):
    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc):
        pty = PtySession("sess-1")
        await pty.start("sys", "model", "/tmp")
        pty.write(b"hello")
        mock_proc.write.assert_called_once_with(b"hello")
        pty.kill()


async def test_resize_calls_setwinsize(mock_proc):
    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc):
        pty = PtySession("sess-1")
        await pty.start("sys", "model", "/tmp")
        pty.resize(cols=120, rows=40)
        mock_proc.setwinsize.assert_called_once_with(40, 120)
        pty.kill()


async def test_output_callback_receives_data(mock_proc, mock_select):
    received = []
    done = asyncio.Event()

    async def cb(data: bytes) -> None:
        received.append(data)
        if b"chunk2" in data:
            done.set()

    # Make select report data ready so read is actually called
    mock_select.select.return_value = ([mock_proc.fd], [], [])
    mock_proc.read.side_effect = [b"chunk1", b"chunk2", EOFError()]

    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc):
        pty = PtySession("sess-1")
        pty.add_output_callback(cb)
        await pty.start("sys", "model", "/tmp")
        await asyncio.wait_for(done.wait(), timeout=2.0)
        assert b"chunk1" in received
        assert b"chunk2" in received
        pty.kill()


async def test_kill_terminates_proc(mock_proc):
    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc):
        pty = PtySession("sess-1")
        await pty.start("sys", "model", "/tmp")
        pty.kill()
        mock_proc.terminate.assert_called()
