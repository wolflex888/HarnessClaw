from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

from harness_claw.pty_session import PtySession


@pytest.fixture
def mock_proc():
    proc = MagicMock()
    proc.isalive.return_value = True
    proc.read.return_value = b"Hello\r\n"
    return proc


async def test_start_spawns_claude_with_correct_args(mock_proc):
    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc) as mock_spawn:
        mock_proc.read.side_effect = [b"data", EOFError()]
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
        mock_proc.read.side_effect = EOFError()
        pty = PtySession("sess-1")
        await pty.start("sys", "model", "/tmp")
        pty.write(b"hello")
        mock_proc.write.assert_called_once_with(b"hello")
        pty.kill()


async def test_resize_calls_setwinsize(mock_proc):
    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc):
        mock_proc.read.side_effect = EOFError()
        pty = PtySession("sess-1")
        await pty.start("sys", "model", "/tmp")
        pty.resize(cols=120, rows=40)
        mock_proc.setwinsize.assert_called_once_with(40, 120)
        pty.kill()


async def test_output_callback_receives_data(mock_proc):
    received = []

    async def cb(data: bytes) -> None:
        received.append(data)

    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc):
        mock_proc.read.side_effect = [b"chunk1", b"chunk2", EOFError()]
        pty = PtySession("sess-1")
        pty.add_output_callback(cb)
        await pty.start("sys", "model", "/tmp")
        # Give read_loop time to run
        await asyncio.sleep(0.05)
        assert b"chunk1" in received
        assert b"chunk2" in received
        pty.kill()


async def test_kill_terminates_proc(mock_proc):
    with patch("ptyprocess.PtyProcess.spawn", return_value=mock_proc):
        mock_proc.read.side_effect = EOFError()
        pty = PtySession("sess-1")
        await pty.start("sys", "model", "/tmp")
        pty.kill()
        mock_proc.terminate.assert_called()
