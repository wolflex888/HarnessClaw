import pytest
from harness_claw.session import Session


def test_session_initial_cost_is_zero():
    s = Session(agent_id="a1", model="claude-sonnet-4-6")
    assert s.cost_usd == 0.0


def test_session_cost_updates_with_tokens():
    s = Session(agent_id="a1", model="claude-sonnet-4-6")
    s.input_tokens = 1_000_000
    s.output_tokens = 1_000_000
    assert s.cost_usd == pytest.approx(18.00)


def test_session_add_user_message():
    s = Session(agent_id="a1", model="claude-sonnet-4-6")
    s.add_user_message("Hello")
    assert s.messages == [{"role": "user", "content": "Hello"}]


def test_session_add_assistant_message():
    s = Session(agent_id="a1", model="claude-sonnet-4-6")
    s.add_assistant_message("Hi there")
    assert s.messages == [{"role": "assistant", "content": "Hi there"}]


def test_session_preserves_message_order():
    s = Session(agent_id="a1", model="claude-sonnet-4-6")
    s.add_user_message("Q1")
    s.add_assistant_message("A1")
    s.add_user_message("Q2")
    assert [m["role"] for m in s.messages] == ["user", "assistant", "user"]


def test_session_has_unique_id():
    s1 = Session(agent_id="a1", model="claude-sonnet-4-6")
    s2 = Session(agent_id="a1", model="claude-sonnet-4-6")
    assert s1.session_id != s2.session_id


def test_session_unknown_model_cost_is_zero():
    s = Session(agent_id="a1", model="gpt-99")
    s.input_tokens = 1_000_000
    assert s.cost_usd == 0.0
