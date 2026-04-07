from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_claw.runtime.run_store import Run, SqliteRunStore


def make_run(**kwargs) -> Run:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    defaults = dict(
        run_id="run-1",
        agent_id="session-1",
        trigger_type="manual",
        caller="user",
        goal="Do the thing",
        input_payload={"key": "val"},
        budget_usd=1.0,
        timeout_secs=300,
        permissions_snapshot={"scopes": ["memory:read"], "role": {"id": "assistant"}},
        status="running",
        task_ids=[],
        checkpoint=None,
        created_at=now,
        updated_at=now,
    )
    defaults.update(kwargs)
    return Run(**defaults)


def test_run_to_dict_roundtrip():
    run = make_run()
    d = run.to_dict()
    assert d["run_id"] == "run-1"
    assert d["agent_id"] == "session-1"
    assert d["trigger_type"] == "manual"
    assert d["input_payload"] == {"key": "val"}
    assert d["task_ids"] == []
    assert d["checkpoint"] is None


def test_run_from_dict_roundtrip():
    run = make_run(task_ids=["t1", "t2"], checkpoint={"last_task_id": "t2"})
    restored = Run.from_dict(run.to_dict())
    assert restored.run_id == run.run_id
    assert restored.task_ids == ["t1", "t2"]
    assert restored.checkpoint == {"last_task_id": "t2"}


def test_sqlite_run_store_save_and_get(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    run = make_run()
    store.save(run)
    fetched = store.get("run-1")
    assert fetched is not None
    assert fetched.run_id == "run-1"
    assert fetched.input_payload == {"key": "val"}


def test_sqlite_run_store_upsert(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    run = make_run()
    store.save(run)
    run.status = "completed"
    run.task_ids = ["t1"]
    store.save(run)
    fetched = store.get("run-1")
    assert fetched.status == "completed"
    assert fetched.task_ids == ["t1"]


def test_sqlite_run_store_get_by_agent(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    store.save(make_run(run_id="r1", agent_id="agent-A"))
    store.save(make_run(run_id="r2", agent_id="agent-B"))
    store.save(make_run(run_id="r3", agent_id="agent-A"))
    runs = store.get_by_agent("agent-A")
    assert len(runs) == 2
    assert {r.run_id for r in runs} == {"r1", "r3"}


def test_sqlite_run_store_get_by_status(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    store.save(make_run(run_id="r1", status="running"))
    store.save(make_run(run_id="r2", status="completed"))
    store.save(make_run(run_id="r3", status="running"))
    runs = store.get_by_status("running")
    assert len(runs) == 2

def test_sqlite_run_store_all(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    store.save(make_run(run_id="r1"))
    store.save(make_run(run_id="r2"))
    assert len(store.all()) == 2


def test_sqlite_run_store_null_checkpoint(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    run = make_run(checkpoint=None)
    store.save(run)
    fetched = store.get("run-1")
    assert fetched.checkpoint is None


def test_sqlite_run_store_with_checkpoint(tmp_path):
    store = SqliteRunStore(tmp_path / "runs.db")
    cp = {"provider_session_id": "abc", "last_task_id": "t1", "progress_pct": 42, "pause_reason": None}
    run = make_run(checkpoint=cp)
    store.save(run)
    fetched = store.get("run-1")
    assert fetched.checkpoint == cp
