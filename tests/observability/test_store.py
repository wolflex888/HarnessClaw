from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from harness_claw.observability.store import AgentObservabilityStore


def make_store() -> AgentObservabilityStore:
    s = AgentObservabilityStore()
    s.init_session("s1")
    return s


def test_tool_start_sets_current_tool():
    store = make_store()
    t = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    store.update_tool_start("s1", "Bash", t)
    status = store.get_status("s1")
    assert status is not None
    assert status.current_tool == "Bash"
    assert status.current_tool_started_at == t


def test_tool_end_clears_current_tool_and_appends_event():
    store = make_store()
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 1, 1, 12, 0, 2, tzinfo=timezone.utc)
    store.update_tool_start("s1", "Bash", t0)
    store.update_tool_end("s1", "Bash", t1, 2000)
    status = store.get_status("s1")
    assert status.current_tool is None
    assert len(status.recent_activity) == 1
    ev = status.recent_activity[0]
    assert ev.tool_name == "Bash"
    assert ev.duration_ms == 2000


def test_recent_activity_capped_at_50():
    store = make_store()
    for i in range(55):
        t0 = datetime(2026, 1, 1, 12, 0, i, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 12, 0, i, 500000, tzinfo=timezone.utc)
        store.update_tool_start("s1", f"Tool{i}", t0)
        store.update_tool_end("s1", f"Tool{i}", t1, 500)
    status = store.get_status("s1")
    assert len(status.recent_activity) == 50


def test_stuck_tool_timeout():
    store = make_store()
    old = datetime.now(timezone.utc) - timedelta(minutes=6)
    store.update_tool_start("s1", "Bash", old)
    status = store.get_status("s1")
    assert status.is_stuck is True
    assert status.stuck_reason == "tool_timeout"


def test_stuck_tool_loop():
    store = make_store()
    for i in range(10):
        t0 = datetime(2026, 1, 1, 12, 0, i, tzinfo=timezone.utc)
        t1 = datetime(2026, 1, 1, 12, 0, i, 500000, tzinfo=timezone.utc)
        store.update_tool_start("s1", "Bash", t0)
        store.update_tool_end("s1", "Bash", t1, 500)
    status = store.get_status("s1")
    assert status.is_stuck is True
    assert status.stuck_reason == "tool_loop"


def test_stuck_output_silence():
    store = make_store()
    old = datetime.now(timezone.utc) - timedelta(minutes=6)
    store.update_output("s1", old)
    status = store.get_status("s1")
    assert status.is_stuck is True
    assert status.stuck_reason == "output_silence"


def test_not_stuck_after_recent_output():
    store = make_store()
    old = datetime.now(timezone.utc) - timedelta(minutes=6)
    store.update_output("s1", old)
    store.update_output("s1", datetime.now(timezone.utc))
    status = store.get_status("s1")
    assert status.is_stuck is False


def test_remove_session():
    store = make_store()
    store.remove_session("s1")
    assert store.get_status("s1") is None
