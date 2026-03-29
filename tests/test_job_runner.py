import asyncio
from collections.abc import AsyncIterator
from typing import Any
from pathlib import Path

from harness_claw.job_runner import JobRunner, PROVIDERS
from harness_claw.providers.base import BaseProvider
from harness_claw.role_registry import RoleRegistry
from harness_claw.session import Session
from harness_claw.session_store import SessionStore


class MockProvider(BaseProvider):
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    async def stream_chat(self, messages, system, model, max_tokens, cwd=None, claude_session_id=None) -> AsyncIterator[dict[str, Any]]:
        for event in self._events:
            yield event


def make_registry(tmp_path: Path) -> RoleRegistry:
    yaml_file = tmp_path / "agents.yaml"
    yaml_file.write_text("""
roles:
  - id: general-purpose
    name: General Purpose
    provider: mock
    model: claude-sonnet-4-6
    system_prompt: "You are helpful."
    max_tokens: 1024
""")
    return RoleRegistry(yaml_file)


def make_runner(tmp_path: Path, events: list[dict[str, Any]]) -> tuple[JobRunner, SessionStore]:
    registry = make_registry(tmp_path)
    store = SessionStore(tmp_path / "sessions.json")
    PROVIDERS["mock"] = MockProvider(events)
    runner = JobRunner(registry, store)
    return runner, store


async def test_run_job_streams_tokens(tmp_path: Path) -> None:
    events = [
        {"type": "token", "delta": "Hello"},
        {"type": "token", "delta": " world"},
        {"type": "usage", "input_tokens": 5, "output_tokens": 10},
    ]
    runner, store = make_runner(tmp_path, events)
    session = Session(role_id="general-purpose", working_dir="~/src", model="claude-sonnet-4-6")
    store.save(session)

    sent = []
    await runner.run_job(session.session_id, "Hi", sent.append)

    token_events = [e for e in sent if e["type"] == "token"]
    assert len(token_events) == 2
    assert token_events[0]["delta"] == "Hello"
    assert token_events[1]["delta"] == " world"


async def test_run_job_sets_session_name(tmp_path: Path) -> None:
    runner, store = make_runner(tmp_path, [{"type": "token", "delta": "ok"}])
    session = Session(role_id="general-purpose", working_dir="~/src", model="claude-sonnet-4-6")
    store.save(session)

    sent = []
    await runner.run_job(session.session_id, "Write a sorting algorithm", sent.append)

    updated = store.get(session.session_id)
    assert updated.name == "Write a sorting algorithm"


async def test_run_job_status_lifecycle(tmp_path: Path) -> None:
    runner, store = make_runner(tmp_path, [{"type": "token", "delta": "done"}])
    session = Session(role_id="general-purpose", working_dir="~/src", model="claude-sonnet-4-6")
    store.save(session)

    sent = []
    await runner.run_job(session.session_id, "hello", sent.append)

    job_updates = [e for e in sent if e["type"] == "job_update"]
    statuses = [e["status"] for e in job_updates]
    assert "running" in statuses
    assert "completed" in statuses


async def test_run_job_accumulates_usage(tmp_path: Path) -> None:
    events = [
        {"type": "usage", "input_tokens": 10, "output_tokens": 20},
    ]
    runner, store = make_runner(tmp_path, events)
    session = Session(role_id="general-purpose", working_dir="~/src", model="claude-sonnet-4-6")
    store.save(session)

    sent = []
    await runner.run_job(session.session_id, "Hi", sent.append)

    updated = store.get(session.session_id)
    assert updated.input_tokens == 10
    assert updated.output_tokens == 20


async def test_delete_session(tmp_path: Path) -> None:
    runner, store = make_runner(tmp_path, [])
    session = Session(role_id="general-purpose", working_dir="~/src", model="claude-sonnet-4-6")
    store.save(session)

    runner.delete_session(session.session_id)
    assert store.get(session.session_id) is None
