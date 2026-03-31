from __future__ import annotations
import pytest
from harness_claw.gateway.auth import TokenStore, AuthError


def test_issue_and_validate_token():
    store = TokenStore()
    token = store.issue("session-1", ["agent:list", "agent:delegate"])
    subject, scopes = store.validate(token)
    assert subject == "session-1"
    assert "agent:list" in scopes
    assert "agent:delegate" in scopes


def test_validate_unknown_token_raises():
    store = TokenStore()
    with pytest.raises(AuthError, match="invalid"):
        store.validate("not-a-real-token")


def test_revoke_makes_token_invalid():
    store = TokenStore()
    token = store.issue("session-1", ["agent:list"])
    store.revoke(token)
    with pytest.raises(AuthError, match="invalid"):
        store.validate(token)


def test_has_scope_returns_true_when_scope_present():
    store = TokenStore()
    token = store.issue("s1", ["agent:list", "memory:read"])
    _, scopes = store.validate(token)
    assert "agent:list" in scopes
    assert "memory:write" not in scopes


def test_issue_returns_unique_tokens():
    store = TokenStore()
    t1 = store.issue("s1", [])
    t2 = store.issue("s2", [])
    assert t1 != t2


def test_revoke_by_subject_revokes_all_tokens():
    store = TokenStore()
    t1 = store.issue("s1", ["agent:list"])
    t2 = store.issue("s1", ["memory:read"])
    store.revoke_by_subject("s1")
    with pytest.raises(AuthError):
        store.validate(t1)
    with pytest.raises(AuthError):
        store.validate(t2)
