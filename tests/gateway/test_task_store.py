from __future__ import annotations

import sqlite3

from harness_claw.gateway.task_store import SqliteTaskStore, Task


def make_task(**kwargs) -> Task:
    defaults = dict(
        task_id="t1",
        delegated_by="agent-a",
        delegated_to="agent-b",
        instructions="do the thing",
        caps_requested=["python"],
    )
    defaults.update(kwargs)
    return Task(**defaults)


def test_save_and_get(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    store.save(make_task())
    result = store.get("t1")
    assert result is not None
    assert result.task_id == "t1"
    assert result.delegated_by == "agent-a"
    assert result.caps_requested == ["python"]


def test_save_updates_existing(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    task = make_task()
    store.save(task)
    task.status = "completed"
    task.progress_pct = 100
    store.save(task)
    result = store.get("t1")
    assert result.status == "completed"
    assert result.progress_pct == 100


def test_get_missing_returns_none(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    assert store.get("nonexistent") is None


def test_all_returns_all_tasks(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    store.save(make_task(task_id="t1"))
    store.save(make_task(task_id="t2"))
    tasks = store.all()
    assert len(tasks) == 2
    assert {t.task_id for t in tasks} == {"t1", "t2"}


def test_mark_stale_as_failed(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    store.save(make_task(task_id="q", status="queued"))
    store.save(make_task(task_id="r", status="running"))
    store.save(make_task(task_id="d", status="completed"))
    count = store.mark_stale_as_failed()
    assert count == 2
    assert store.get("q").status == "failed"
    assert store.get("r").status == "failed"
    assert store.get("q").result == "server_restart"
    assert store.get("d").status == "completed"  # unchanged


def test_expire_removes_old_tasks(tmp_path):
    from datetime import datetime, timedelta, timezone
    store = SqliteTaskStore(tmp_path / "tasks.db")
    store.save(make_task(task_id="old", status="completed"))
    old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        conn.execute("UPDATE tasks SET updated_at = ? WHERE task_id = 'old'", (old_time,))
    store.save(make_task(task_id="new", status="completed"))
    count = store.expire(days=7)
    assert count == 1
    assert store.get("old") is None
    assert store.get("new") is not None


def test_roundtrip_context_and_result(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    task = make_task(
        context={"key": "value", "num": 42},
        result={"output": "done"},
        callback=True,
    )
    store.save(task)
    loaded = store.get(task.task_id)
    assert loaded.context == {"key": "value", "num": 42}
    assert loaded.result == {"output": "done"}
    assert loaded.callback is True


def test_string_result_roundtrip(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    task = make_task(result="server_restart")
    store.save(task)
    loaded = store.get(task.task_id)
    assert loaded.result == "server_restart"


def test_persists_across_instances(tmp_path):
    db_path = tmp_path / "tasks.db"
    store1 = SqliteTaskStore(db_path)
    store1.save(make_task(task_id="persistent"))
    store2 = SqliteTaskStore(db_path)
    assert store2.get("persistent") is not None


def test_task_priority_default():
    task = make_task()
    assert task.priority == 2
    assert task.resume is False


def test_task_priority_in_to_dict():
    task = make_task(priority=1, resume=True)
    d = task.to_dict()
    assert d["priority"] == 1
    assert d["resume"] is True


def test_sqlite_roundtrip_priority_and_resume(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    task = make_task(task_id="p1", priority=1, resume=True)
    store.save(task)
    loaded = store.get("p1")
    assert loaded.priority == 1
    assert loaded.resume is True


def test_get_interrupted_returns_queued_and_running(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    store.save(make_task(task_id="q1", status="queued"))
    store.save(make_task(task_id="r1", status="running"))
    store.save(make_task(task_id="c1", status="completed"))
    store.save(make_task(task_id="f1", status="failed"))
    results = store.get_interrupted()
    ids = {t.task_id for t in results}
    assert ids == {"q1", "r1"}


def test_mark_interrupted_as_queued(tmp_path):
    store = SqliteTaskStore(tmp_path / "tasks.db")
    store.save(make_task(task_id="q1", status="queued"))
    store.save(make_task(task_id="r1", status="running"))
    store.save(make_task(task_id="c1", status="completed"))
    count = store.mark_interrupted_as_queued()
    assert count == 2
    assert store.get("q1").status == "queued"
    assert store.get("r1").status == "queued"
    assert store.get("c1").status == "completed"  # unchanged
