from harness_claw.session import Session


def test_session_defaults() -> None:
    s = Session(role_id="general-purpose", working_dir="~/src", model="claude-sonnet-4-6")
    assert s.status == "idle"
    assert s.name == ""
    assert s.claude_session_id is None
    assert s.input_tokens == 0
    assert s.output_tokens == 0
    assert s.messages == []


def test_session_cost_usd() -> None:
    s = Session(role_id="general-purpose", working_dir="~/src", model="claude-sonnet-4-6")
    s.input_tokens = 1_000_000
    s.output_tokens = 1_000_000
    assert abs(s.cost_usd - 18.0) < 0.001  # 3.00 + 15.00


def test_session_unknown_model_cost_is_zero() -> None:
    s = Session(role_id="x", working_dir="~/src", model="unknown-model")
    s.input_tokens = 1000
    assert s.cost_usd == 0.0


def test_session_has_unique_id() -> None:
    a = Session(role_id="x", working_dir="~/src", model="claude-sonnet-4-6")
    b = Session(role_id="x", working_dir="~/src", model="claude-sonnet-4-6")
    assert a.session_id != b.session_id


def test_session_add_messages() -> None:
    s = Session(role_id="x", working_dir="~/src", model="claude-sonnet-4-6")
    s.add_user_message("hello")
    s.add_assistant_message("hi")
    assert s.messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]


def test_session_to_dict() -> None:
    s = Session(role_id="code-writer", working_dir="~/src/foo", model="claude-sonnet-4-6")
    s.name = "Fix the bug"
    d = s.to_dict()
    assert d["role_id"] == "code-writer"
    assert d["working_dir"] == "~/src/foo"
    assert d["name"] == "Fix the bug"
    assert d["status"] == "idle"
    assert "session_id" in d
    assert "claude_session_id" in d


def test_session_from_dict() -> None:
    s = Session(role_id="x", working_dir="~/src", model="claude-sonnet-4-6")
    s.name = "Test"
    d = s.to_dict()
    restored = Session.from_dict(d)
    assert restored.session_id == s.session_id
    assert restored.name == "Test"
    assert restored.role_id == "x"
