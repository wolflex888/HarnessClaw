import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from harness_claw.providers.claude_code import ClaudeCodeProvider


def make_jsonl(*events: dict) -> bytes:
    return b"\n".join(json.dumps(e).encode() for e in events) + b"\n"


async def mock_subprocess(stdout_data: bytes):
    """Returns a mock asyncio subprocess whose stdout yields lines."""
    proc = MagicMock()
    proc.returncode = 0

    lines = [line + b"\n" for line in stdout_data.split(b"\n") if line.strip()]

    async def readline():
        if lines:
            return lines.pop(0)
        return b""

    proc.stdout = MagicMock()
    proc.stdout.readline = readline

    async def wait():
        return 0

    proc.wait = wait
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock()
    proc.terminate = MagicMock()
    return proc


async def test_stream_chat_yields_tokens() -> None:
    stdout = make_jsonl(
        {"type": "system", "subtype": "init", "session_id": "abc-123", "tools": []},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "Hello"}]}},
        {"type": "result", "subtype": "success", "result": "Hello", "usage": {"input_tokens": 10, "output_tokens": 5}, "total_cost_usd": 0.001},
    )
    proc = await mock_subprocess(stdout)

    provider = ClaudeCodeProvider()
    events = []

    with patch("asyncio.create_subprocess_exec", return_value=proc):
        async for event in provider.stream_chat(
            messages=[{"role": "user", "content": "Hi"}],
            system="You are helpful.",
            model="claude-sonnet-4-6",
            max_tokens=1024,
            cwd="/tmp",
            claude_session_id=None,
        ):
            events.append(event)

    types = [e["type"] for e in events]
    assert "session_init" in types
    assert "token" in types
    assert "usage" in types

    session_init = next(e for e in events if e["type"] == "session_init")
    assert session_init["claude_session_id"] == "abc-123"

    token = next(e for e in events if e["type"] == "token")
    assert token["delta"] == "Hello"

    usage = next(e for e in events if e["type"] == "usage")
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 5


async def test_stream_chat_uses_resume_flag() -> None:
    stdout = make_jsonl(
        {"type": "system", "subtype": "init", "session_id": "existing-id", "tools": []},
        {"type": "result", "subtype": "success", "result": "Done", "usage": {"input_tokens": 1, "output_tokens": 1}, "cost_usd": 0.0},
    )
    proc = await mock_subprocess(stdout)

    provider = ClaudeCodeProvider()
    captured_args = []

    async def capture(*args, **kwargs):
        captured_args.extend(args)
        return proc

    with patch("asyncio.create_subprocess_exec", side_effect=capture):
        async for _ in provider.stream_chat(
            messages=[],
            system="sys",
            model="claude-sonnet-4-6",
            max_tokens=1024,
            cwd="/tmp",
            claude_session_id="existing-id",
        ):
            pass

    assert "--resume" in captured_args
    idx = captured_args.index("--resume")
    assert captured_args[idx + 1] == "existing-id"

