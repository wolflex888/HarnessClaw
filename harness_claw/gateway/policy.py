from __future__ import annotations

from typing import Protocol

from harness_claw.model import PolicyDecision


class PolicyEngine(Protocol):
    def check(self, subject: str, scopes: list[str], operation: str) -> PolicyDecision:
        ...


class LocalPolicyEngine:
    """Scope-based policy enforcement. Phase 1 default."""

    def check(self, subject: str, scopes: list[str], operation: str) -> PolicyDecision:
        if "*" in scopes or operation in scopes:
            return PolicyDecision(allowed=True)
        return PolicyDecision(
            allowed=False,
            reason=f"scope '{operation}' required but not granted to subject '{subject}'",
        )
