from pathlib import Path
from harness_claw.session import Session
from harness_claw.runtime.session_store import SessionStore


def make_session(role_id: str = "general-purpose") -> Session:
    return Session(role_id=role_id, working_dir="~/src", model="claude-sonnet-4-6")


def test_save_and_load(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.json")
    s = make_session()
    s.name = "Hello world"
    store.save(s)

    store2 = SessionStore(tmp_path / "sessions.json")
    loaded = store2.get(s.session_id)
    assert loaded is not None
    assert loaded.name == "Hello world"
    assert loaded.session_id == s.session_id


def test_all_sessions(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.json")
    a = make_session("code-writer")
    a.working_dir = "~/src/proj-a"
    b = make_session("reviewer")
    b.working_dir = "~/src/proj-b"
    store.save(a)
    store.save(b)

    all_sessions = store.all()
    assert len(all_sessions) == 2


def test_delete_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.json")
    s = make_session()
    store.save(s)
    store.delete(s.session_id)
    assert store.get(s.session_id) is None


def test_grouped_by_working_dir(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.json")
    a = make_session()
    a.working_dir = "~/src/alpha"
    b = make_session()
    b.working_dir = "~/src/alpha"
    c = make_session()
    c.working_dir = "~/src/beta"
    for s in [a, b, c]:
        store.save(s)

    grouped = store.grouped_by_dir()
    assert len(grouped["~/src/alpha"]) == 2
    assert len(grouped["~/src/beta"]) == 1


def test_empty_store(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions.json")
    assert store.all() == []
    assert store.get("nonexistent") is None
