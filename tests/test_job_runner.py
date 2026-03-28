from __future__ import annotations

import pytest
from collections.abc import AsyncIterator
from typing import Any, Callable, Awaitable

from harness_claw.agent_registry import AgentConfig, AgentRegistry
from harness_claw.job_runner import JobRunner, PROVIDERS
from harness_claw.providers.base import BaseProvider


class MockProvider(BaseProvider):
    def __init__(
        self,
        tokens: list[str],
        input_tokens: int = 10,
        output_tokens: int = 5,
    ) -> None:
        self._tokens = tokens
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens

    async def stream_chat(self, messages, system, model, max_tokens) -> AsyncIterator[dict]:
        for token in self._tokens:
            yield {"type": "token", "delta": token}
        yield {"type": "usage", "input_tokens": self._input_tokens, "output_tokens": self._output_tokens}

    async def stream_with_tools(
        self, messages, system, model, tools, tool_executor, max_tokens
    ) -> AsyncIterator[dict]:
        for token in self._tokens:
            yield {"type": "token", "delta": token}
        yield {"type": "usage", "input_tokens": self._input_tokens, "output_tokens": self._output_tokens}


@pytest.fixture
def registry():
    r = AgentRegistry.__new__(AgentRegistry)
    r._agents = {
        "test-agent": AgentConfig(
            id="test-agent",
            name="Test Agent",
            provider="mock",
            model="claude-sonnet-4-6",
        ),
        "orchestrator": AgentConfig(
            id="orchestrator",
            name="Orchestrator",
            provider="mock",
            model="claude-sonnet-4-6",
            orchestrates=["test-agent"],
        ),
    }
    return r


@pytest.fixture(autouse=True)
def patch_providers(monkeypatch):
    monkeypatch.setitem(PROVIDERS, "mock", MockProvider(tokens=["Hello", ", ", "world", "!"]))


async def test_run_job_streams_tokens(registry):
    runner = JobRunner(registry)
    received: list[dict] = []

    async def send(msg: dict) -> None:
        received.append(msg)

    await runner.run_job("test-agent", "Hi", send)

    token_events = [m for m in received if m["type"] == "token"]
    assert [e["delta"] for e in token_events] == ["Hello", ", ", "world", "!"]


async def test_run_job_sends_running_then_completed(registry):
    runner = JobRunner(registry)
    received: list[dict] = []

    async def send(msg: dict) -> None:
        received.append(msg)

    await runner.run_job("test-agent", "Hi", send)

    updates = [m for m in received if m["type"] == "job_update"]
    statuses = [u["status"] for u in updates]
    assert statuses[0] == "running"
    assert statuses[-1] == "completed"


async def test_run_job_tracks_usage(registry):
    runner = JobRunner(registry)
    received: list[dict] = []

    async def send(msg: dict) -> None:
        received.append(msg)

    await runner.run_job("test-agent", "Hi", send)

    usage = [m for m in received if m["type"] == "usage"]
    assert len(usage) == 1
    assert usage[0]["input_tokens"] == 10
    assert usage[0]["output_tokens"] == 5
    assert usage[0]["cost_usd"] > 0


async def test_session_persists_messages(registry):
    runner = JobRunner(registry)

    async def send(msg: dict) -> None:
        pass

    await runner.run_job("test-agent", "Hello", send)

    session = runner.get_session("test-agent")
    assert session is not None
    assert session.messages[0] == {"role": "user", "content": "Hello"}
    assert session.messages[1]["role"] == "assistant"
    assert "Hello" in session.messages[1]["content"]


async def test_second_message_appends_to_session(registry):
    runner = JobRunner(registry)

    async def send(msg: dict) -> None:
        pass

    await runner.run_job("test-agent", "First", send)
    await runner.run_job("test-agent", "Second", send)

    session = runner.get_session("test-agent")
    assert len(session.messages) == 4  # user, assistant, user, assistant


async def test_orchestrator_uses_stream_with_tools(registry, monkeypatch):
    calls: list[str] = []
    original = MockProvider.stream_with_tools

    async def tracking_stream_with_tools(self, *args, **kwargs) -> AsyncIterator[dict]:
        calls.append("stream_with_tools")
        async for e in original(self, *args, **kwargs):
            yield e

    monkeypatch.setattr(MockProvider, "stream_with_tools", tracking_stream_with_tools)

    runner = JobRunner(registry)

    async def send(msg: dict) -> None:
        pass

    await runner.run_job("orchestrator", "Write code", send)
    assert "stream_with_tools" in calls
