from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

log = logging.getLogger(__name__)


def _default_categories() -> dict[str, bool]:
    return {
        "anthropic_key": True,
        "openai_key": True,
        "github_token": True,
        "aws_access_key": True,
        "aws_secret_key": True,
        "gcp_key": True,
        "slack_webhook": True,
        "jwt": True,
        "ssh_private_key": True,
        "email": False,
        "phone_us": False,
        "credit_card": False,
        "ssn_us": False,
        "user_path": False,
    }


@dataclass
class CustomRule:
    name: str
    pattern: str
    replacement: str = ""

    def __post_init__(self) -> None:
        # Compile eagerly to fail fast on bad regex.
        self._compiled = re.compile(self.pattern)
        if not self.replacement:
            self.replacement = f"[REDACTED:{self.name}]"

    @property
    def compiled(self) -> re.Pattern[str]:
        return self._compiled


@dataclass
class RedactionConfig:
    enabled: bool = True
    categories: dict[str, bool] = field(default_factory=_default_categories)
    custom_rules: list[CustomRule] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> "RedactionConfig":
        path = path or Path("redaction.yaml")
        if not path.exists():
            return cls()
        data = yaml.safe_load(path.read_text()) or {}

        known = _default_categories()
        cats: dict[str, bool] = dict(known)
        for name, on in (data.get("categories") or {}).items():
            if name not in known:
                log.warning("redaction: unknown category %r in %s - ignoring", name, path)
                continue
            cats[name] = bool(on)

        rules: list[CustomRule] = []
        for i, entry in enumerate(data.get("custom_rules") or []):
            if not isinstance(entry, dict):
                raise ValueError(f"redaction: custom_rules[{i}] must be a mapping, got {type(entry).__name__}")
            if "name" not in entry:
                raise ValueError(f"redaction: custom_rules[{i}] missing required key 'name'")
            if "pattern" not in entry:
                raise ValueError(f"redaction: custom_rules[{i}] missing required key 'pattern'")
            rules.append(CustomRule(
                name=entry["name"],
                pattern=entry["pattern"],
                replacement=entry.get("replacement", ""),
            ))

        return cls(
            enabled=bool(data.get("enabled", True)),
            categories=cats,
            custom_rules=rules,
        )


class Redactor(Protocol):
    def redact(self, value: Any) -> Any: ...


# Built-in rule order matters: more-specific patterns come first so a match
# gets labeled with the right category (e.g. `sk-ant-` is anthropic_key, not
# openai_key). Config only toggles which rules run, not the order.
_BUILTIN_RULES: list[tuple[str, str]] = [
    ("anthropic_key",   r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    ("openai_key",      r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    ("github_token",    r"gh[pousr]_[A-Za-z0-9]{30,}"),
    ("aws_access_key",  r"AKIA[0-9A-Z]{16}"),
    ("aws_secret_key",  r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}['\"]?"),
    ("gcp_key",         r'"type"\s*:\s*"service_account"'),
    ("slack_webhook",   r"https://hooks\.slack\.com/services/[A-Z0-9/]+"),
    ("jwt",             r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
    ("ssh_private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
    ("email",           r"[\w.+\-]+@[\w\-]+\.[\w.\-]+"),
    ("phone_us",        r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)"),
    ("credit_card",     r"(?<!\d)\d{13,19}(?!\d)"),
    ("ssn_us",          r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"),
    ("user_path",       r"/(?:Users|home)/[\w.\-]+(?=/)"),
]


@dataclass
class _CompiledRule:
    name: str
    regex: "re.Pattern[str]"
    replacement: str


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


class RegexRedactor:
    """Default `Redactor` — regex-based, recursive, destructive."""

    def __init__(self, config: RedactionConfig) -> None:
        self._rules: list[_CompiledRule] = []
        for name, pattern in _BUILTIN_RULES:
            if config.categories.get(name, False):
                self._rules.append(_CompiledRule(
                    name=name,
                    regex=re.compile(pattern),
                    replacement=f"[REDACTED:{name}]",
                ))
        for custom in config.custom_rules:
            self._rules.append(_CompiledRule(
                name=custom.name,
                regex=custom.compiled,
                # __post_init__ always fills in replacement
                replacement=custom.replacement or f"[REDACTED:{custom.name}]",
            ))

    def redact(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._redact_str(value)
        if isinstance(value, dict):
            return {k: self.redact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        return value

    def _redact_str(self, s: str) -> str:
        for rule in self._rules:
            if rule.name == "credit_card":
                s = rule.regex.sub(
                    lambda m: rule.replacement if _luhn_ok(m.group(0)) else m.group(0),
                    s,
                )
            else:
                s = rule.regex.sub(rule.replacement, s)
        return s


from harness_claw.gateway.audit import AuditEvent, AuditLogger


class RedactingAuditLogger:
    """Drop-in replacement for `AuditLogger` that scrubs `details` before write.

    Structural fields (`subject`, `operation`, `resource`, `outcome`) are
    preserved verbatim so the audit trail stays queryable.
    """

    def __init__(self, inner: AuditLogger, redactor: Redactor) -> None:
        self._inner = inner
        self._redactor = redactor

    def log(self, event: AuditEvent) -> None:
        try:
            scrubbed_details = self._redactor.redact(event.details)
        except Exception as e:
            # Fail open: write the original event, then emit an ERROR entry
            # that bypasses the redactor (prevents infinite recursion if the
            # redactor itself is the bug).
            self._inner.log(event)
            self._inner.log(AuditEvent(
                subject=event.subject,
                operation=event.operation,
                resource=event.resource,
                outcome="error",
                details={"reason": "redaction_failed", "error": str(e)},
            ))
            log.warning("redaction failed for event %s: %s", event.event_id, e)
            return

        self._inner.log(AuditEvent(
            subject=event.subject,
            operation=event.operation,
            resource=event.resource,
            outcome=event.outcome,
            details=scrubbed_details,
        ))
