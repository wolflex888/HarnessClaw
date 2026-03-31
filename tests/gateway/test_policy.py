from __future__ import annotations
import pytest
from harness_claw.gateway.policy import LocalPolicyEngine
from harness_claw.model import PolicyDecision


def test_allowed_when_scope_present():
    engine = LocalPolicyEngine()
    decision = engine.check(
        subject="s1",
        scopes=["agent:list", "agent:delegate"],
        operation="agent:list",
    )
    assert decision.allowed is True


def test_denied_when_scope_missing():
    engine = LocalPolicyEngine()
    decision = engine.check(
        subject="s1",
        scopes=["agent:list"],
        operation="agent:delegate",
    )
    assert decision.allowed is False
    assert "agent:delegate" in (decision.reason or "")


def test_denied_with_empty_scopes():
    engine = LocalPolicyEngine()
    decision = engine.check(subject="s1", scopes=[], operation="memory:write")
    assert decision.allowed is False


def test_allowed_with_wildcard_scope():
    engine = LocalPolicyEngine()
    decision = engine.check(subject="s1", scopes=["*"], operation="agent:spawn")
    assert decision.allowed is True


def test_returns_policy_decision_type(registry=None):
    engine = LocalPolicyEngine()
    result = engine.check("s1", ["agent:list"], "agent:list")
    assert isinstance(result, PolicyDecision)
