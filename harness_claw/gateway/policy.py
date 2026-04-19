from __future__ import annotations

from typing import Protocol

from harness_claw.model import PolicyDecision


class PolicyEngine(Protocol):
    def check(self, subject: str, scopes: list[str], operation: str) -> PolicyDecision:
        ...


class LocalPolicyEngine:
    """Scope-based policy enforcement. Phase 1 default."""

    def __init__(self, free_mode: bool = False) -> None:
        self._free_mode = free_mode

    def check(self, subject: str, scopes: list[str], operation: str) -> PolicyDecision:
        if self._free_mode:
            return PolicyDecision(allowed=True, reason="free mode: all permission checks bypassed")
        if "*" in scopes or operation in scopes:
            return PolicyDecision(allowed=True)
        return PolicyDecision(
            allowed=False,
            reason=f"scope '{operation}' required but not granted to subject '{subject}'",
        )
