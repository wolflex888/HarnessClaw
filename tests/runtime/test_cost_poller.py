from __future__ import annotations

import asyncio
import json
from pathlib import Path
import pytest

from harness_claw.runtime.cost_poller import CostPoller


async def test_poll_reads_jsonl_and_calls_callback(tmp_path):
    project_dir = tmp_path / "projects" / "tmp-myproject"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / "session-abc.jsonl"
    jsonl.write_text(
        json.dumps({"type": "result", "total_cost_usd": 0.05, "usage": {"input_tokens": 100, "output_tokens": 50}}) + "\n"
        + json.dumps({"type": "result", "total_cost_usd": 0.03, "usage": {"input_tokens": 60, "output_tokens": 30}}) + "\n"
    )

    updates = []

    async def on_update(session_id, cost, input_tokens, output_tokens):
        updates.append((session_id, cost, input_tokens, output_tokens))

    poller = CostPoller("sess-1", "/tmp/myproject", on_update, claude_home=tmp_path)
    await poller._poll()

    assert len(updates) == 1
    sid, cost, inp, out = updates[0]
    assert sid == "sess-1"
    assert abs(cost - 0.08) < 0.001
    assert inp == 160
    assert out == 80


async def test_poll_skips_when_no_project_dir(tmp_path):
    updates = []

    async def on_update(session_id, cost, input_tokens, output_tokens):
        updates.append((session_id, cost, input_tokens, output_tokens))

    poller = CostPoller("sess-1", "/tmp/nonexistent", on_update, claude_home=tmp_path)
    await poller._poll()

    assert updates == []


async def test_poll_only_calls_callback_when_cost_changes(tmp_path):
    project_dir = tmp_path / "projects" / "tmp-proj"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / "s.jsonl"
    jsonl.write_text(
        json.dumps({"type": "result", "total_cost_usd": 0.01, "usage": {"input_tokens": 10, "output_tokens": 5}}) + "\n"
    )

    updates = []

    async def on_update(session_id, cost, input_tokens, output_tokens):
        updates.append(cost)

    poller = CostPoller("sess-1", "/tmp/proj", on_update, claude_home=tmp_path)
    await poller._poll()
    await poller._poll()  # same data, should not call again

    assert len(updates) == 1


async def test_poll_ignores_non_result_events(tmp_path):
    project_dir = tmp_path / "projects" / "tmp-x"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / "s.jsonl"
    jsonl.write_text(
        json.dumps({"type": "assistant", "message": "hi"}) + "\n"
        + json.dumps({"type": "result", "total_cost_usd": 0.02, "usage": {"input_tokens": 20, "output_tokens": 10}}) + "\n"
    )

    updates = []

    async def on_update(session_id, cost, input_tokens, output_tokens):
        updates.append(cost)

    poller = CostPoller("sess-1", "/tmp/x", on_update, claude_home=tmp_path)
    await poller._poll()

    assert len(updates) == 1
    assert abs(updates[0] - 0.02) < 0.001


async def test_poll_extracts_tool_events(tmp_path):
    project_dir = tmp_path / "projects" / "tmp-myproject"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / "session-abc.jsonl"
    jsonl.write_text(
        json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}],
            },
        }) + "\n"
        + json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": []}],
            },
        }) + "\n"
    )

    from harness_claw.observability.store import AgentObservabilityStore
    obs_store = AgentObservabilityStore()
    obs_store.init_session("sess-1")
    updates = []

    async def on_update(session_id, cost, input_tokens, output_tokens):
        updates.append(cost)

    poller = CostPoller("sess-1", "/tmp/myproject", on_update, claude_home=tmp_path, obs_store=obs_store)
    await poller._poll()

    status = obs_store.get_status("sess-1")
    assert len(status.recent_activity) == 1
    assert status.recent_activity[0].tool_name == "Bash"


async def test_poll_does_not_duplicate_tool_events(tmp_path):
    project_dir = tmp_path / "projects" / "tmp-myproject"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / "session-abc.jsonl"
    entry = (
        json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_2", "name": "Read", "input": {}}]},
        }) + "\n"
        + json.dumps({
            "type": "user",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_2", "content": []}]},
        }) + "\n"
    )
    jsonl.write_text(entry)

    from harness_claw.observability.store import AgentObservabilityStore
    obs_store = AgentObservabilityStore()
    obs_store.init_session("sess-1")

    async def noop(sid, cost, inp, out): pass

    poller = CostPoller("sess-1", "/tmp/myproject", noop, claude_home=tmp_path, obs_store=obs_store)
    await poller._poll()
    await poller._poll()  # second poll — same data

    status = obs_store.get_status("sess-1")
    assert len(status.recent_activity) == 1


async def test_provider_session_id_captured_from_jsonl_filename(tmp_path):
    """provider_session_id is the stem of the most-recent JSONL file."""
    project_dir = tmp_path / "projects" / "tmp-test-project"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / "my-claude-session-id.jsonl"
    jsonl.write_text("")

    async def on_cost(sid, cost, inp, out):
        pass

    poller = CostPoller(
        session_id="s1",
        working_dir="/tmp/test-project",
        on_cost_update=on_cost,
        claude_home=tmp_path,
    )
    assert poller.provider_session_id is None

    await poller._poll()
    assert poller.provider_session_id == "my-claude-session-id"
