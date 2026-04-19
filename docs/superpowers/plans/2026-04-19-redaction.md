# Redaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scrub secrets (and optionally PII) from `audit.jsonl` and the memory SQLite database before they are written, so those artifacts can be reviewed, shared, backed up, or shipped off-box without leaking.

**Architecture:** Add a new module `harness_claw/gateway/redaction.py` that exposes a `Redactor` Protocol and a regex-based default implementation. Two decorator classes wrap `AuditLogger` and `SqliteMemoryStore` on their write paths; reads pass through unchanged. Config lives in `redaction.yaml` at the project root. A backfill CLI (`harnessclaw redact-existing`) retroactively scrubs existing `audit.jsonl` and `memory.db` with dry-run by default.

**Tech Stack:** Python 3.12, `re` (stdlib), `pyyaml` (already a dep), `pytest`, `pytest-asyncio`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-19-redaction-design.md`.

---

## File Structure

**New files:**
- `harness_claw/gateway/redaction.py` — `Redactor` protocol, `CustomRule`, `RedactionConfig`, `RegexRedactor`, `RedactingAuditLogger`, `RedactingMemoryStore`.
- `harness_claw/redact_backfill.py` — backfill logic used by the CLI. (Sibling of `cli.py`, not a package, to match existing layout.)
- `redaction.yaml` — committed example config at project root, documenting the schema.
- `tests/gateway/test_redaction_config.py`
- `tests/gateway/test_redaction_redactor.py`
- `tests/gateway/test_redacting_audit_logger.py`
- `tests/gateway/test_redacting_memory_store.py`
- `tests/test_redact_backfill.py`

**Modified files:**
- `harness_claw/server.py` — wire the decorators at startup.
- `harness_claw/cli.py` — register `redact-existing` subcommand.

---

## Task 1: `RedactionConfig` + `CustomRule` (config loader)

**Files:**
- Create: `harness_claw/gateway/redaction.py`
- Test: `tests/gateway/test_redaction_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/test_redaction_config.py`:

```python
from __future__ import annotations

import re as _re
from pathlib import Path

import pytest

from harness_claw.gateway.redaction import RedactionConfig, CustomRule


def test_missing_file_returns_defaults():
    cfg = RedactionConfig.load(Path("/nonexistent/redaction.yaml"))
    assert cfg.enabled is True
    assert cfg.categories["anthropic_key"] is True
    assert cfg.categories["email"] is False
    assert cfg.custom_rules == []


def test_partial_file_merges_with_defaults(tmp_path: Path):
    path = tmp_path / "redaction.yaml"
    path.write_text("enabled: true\ncategories:\n  email: on\n")
    cfg = RedactionConfig.load(path)
    assert cfg.categories["email"] is True
    assert cfg.categories["anthropic_key"] is True  # default preserved


def test_unknown_category_logs_warning_but_loads(tmp_path: Path, caplog):
    path = tmp_path / "redaction.yaml"
    path.write_text("categories:\n  quantum_key: on\n")
    cfg = RedactionConfig.load(path)
    assert "quantum_key" not in cfg.categories
    assert any("quantum_key" in rec.message for rec in caplog.records)


def test_malformed_custom_rule_regex_raises_at_load(tmp_path: Path):
    path = tmp_path / "redaction.yaml"
    path.write_text(
        "custom_rules:\n"
        "  - name: bad\n"
        "    pattern: '[unclosed'\n"
        "    replacement: 'x'\n"
    )
    with pytest.raises(_re.error):
        RedactionConfig.load(path)


def test_custom_rule_default_replacement_uses_name(tmp_path: Path):
    path = tmp_path / "redaction.yaml"
    path.write_text(
        "custom_rules:\n"
        "  - name: acme_token\n"
        "    pattern: 'ACME-[A-Z0-9]{5}'\n"
    )
    cfg = RedactionConfig.load(path)
    assert cfg.custom_rules[0].replacement == "[REDACTED:acme_token]"


def test_enabled_false_is_structurally_valid(tmp_path: Path):
    path = tmp_path / "redaction.yaml"
    path.write_text("enabled: false\n")
    cfg = RedactionConfig.load(path)
    assert cfg.enabled is False
    # categories still populated with defaults for inspection
    assert "anthropic_key" in cfg.categories
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gateway/test_redaction_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness_claw.gateway.redaction'`

- [ ] **Step 3: Implement `RedactionConfig` + `CustomRule`**

Create `harness_claw/gateway/redaction.py`:

```python
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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
    replacement: str | None = None

    def __post_init__(self) -> None:
        # Compile eagerly to fail fast on bad regex.
        self._compiled = re.compile(self.pattern)
        if self.replacement is None:
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
                log.warning("redaction: unknown category %r in %s — ignoring", name, path)
                continue
            cats[name] = bool(on)

        rules: list[CustomRule] = []
        for entry in data.get("custom_rules") or []:
            rules.append(CustomRule(
                name=entry["name"],
                pattern=entry["pattern"],
                replacement=entry.get("replacement"),
            ))

        return cls(
            enabled=bool(data.get("enabled", True)),
            categories=cats,
            custom_rules=rules,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gateway/test_redaction_config.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/redaction.py tests/gateway/test_redaction_config.py
git commit -m "feat(redaction): add RedactionConfig + CustomRule loader"
```

---

## Task 2: `Redactor` Protocol + `RegexRedactor` default

**Files:**
- Modify: `harness_claw/gateway/redaction.py`
- Test: `tests/gateway/test_redaction_redactor.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/test_redaction_redactor.py`:

```python
from __future__ import annotations

import pytest

from harness_claw.gateway.redaction import (
    CustomRule,
    RedactionConfig,
    RegexRedactor,
)


def make_redactor(**cat_overrides) -> RegexRedactor:
    cfg = RedactionConfig()
    cfg.categories.update(cat_overrides)
    return RegexRedactor(cfg)


def test_anthropic_key_is_redacted():
    r = make_redactor()
    out = r.redact("token: sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-")
    assert "sk-ant-" not in out
    assert "[REDACTED:anthropic_key]" in out


def test_openai_key_is_redacted():
    r = make_redactor()
    out = r.redact("key: sk-proj-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
    assert "[REDACTED:openai_key]" in out


def test_anthropic_matched_before_openai():
    r = make_redactor()
    # An sk-ant- key must be labeled as anthropic_key, not openai_key.
    out = r.redact("sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
    assert "[REDACTED:anthropic_key]" in out
    assert "openai_key" not in out


def test_github_token_redacted():
    r = make_redactor()
    out = r.redact("gh: ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
    assert "[REDACTED:github_token]" in out


def test_aws_access_key_redacted():
    r = make_redactor()
    out = r.redact("AKIAIOSFODNN7EXAMPLE in config")
    assert "[REDACTED:aws_access_key]" in out


def test_slack_webhook_redacted():
    r = make_redactor()
    out = r.redact("notify: https://hooks.slack.com/services/T00/B00/XXXXXXXXX")
    assert "[REDACTED:slack_webhook]" in out


def test_jwt_redacted():
    r = make_redactor()
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjMifQ.sig_value_here_longer"
    out = r.redact(f"bearer {jwt}")
    assert "[REDACTED:jwt]" in out


def test_ssh_private_key_redacted():
    r = make_redactor()
    key = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAA=\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    out = r.redact(key)
    assert "[REDACTED:ssh_private_key]" in out
    assert "PRIVATE KEY" not in out


def test_email_off_by_default():
    r = make_redactor()
    out = r.redact("contact me at alice@example.com")
    assert "alice@example.com" in out  # default is off


def test_email_on_when_enabled():
    r = make_redactor(email=True)
    out = r.redact("contact me at alice@example.com")
    assert "[REDACTED:email]" in out


def test_passes_through_ordinary_strings():
    r = make_redactor()
    out = r.redact("nothing sensitive here, just a normal log line")
    assert out == "nothing sensitive here, just a normal log line"


def test_recurses_into_dicts_and_lists():
    r = make_redactor()
    payload = {
        "outer": "ok",
        "inner": {"token": "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-"},
        "items": ["harmless", "gh: ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"],
    }
    out = r.redact(payload)
    assert out["outer"] == "ok"
    assert "[REDACTED:anthropic_key]" in out["inner"]["token"]
    assert "[REDACTED:github_token]" in out["items"][1]


def test_non_string_leaves_untouched():
    r = make_redactor()
    assert r.redact(42) == 42
    assert r.redact(3.14) == 3.14
    assert r.redact(True) is True
    assert r.redact(None) is None


def test_custom_rule_fires():
    cfg = RedactionConfig()
    cfg.custom_rules.append(CustomRule(name="acme", pattern=r"ACME-[A-Z0-9]{5}"))
    r = RegexRedactor(cfg)
    out = r.redact("key: ACME-AB123 value")
    assert "[REDACTED:acme]" in out


def test_idempotent():
    r = make_redactor()
    payload = {"k": "gh: ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"}
    once = r.redact(payload)
    twice = r.redact(once)
    assert once == twice
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gateway/test_redaction_redactor.py -v`
Expected: FAIL — `ImportError: cannot import name 'RegexRedactor'`

- [ ] **Step 3: Implement `Redactor` + `RegexRedactor`**

Append to `harness_claw/gateway/redaction.py`:

```python
from typing import Protocol


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gateway/test_redaction_redactor.py -v`
Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/redaction.py tests/gateway/test_redaction_redactor.py
git commit -m "feat(redaction): add Redactor protocol + RegexRedactor with built-in rules"
```

---

## Task 3: `RedactingAuditLogger` decorator

**Files:**
- Modify: `harness_claw/gateway/redaction.py`
- Test: `tests/gateway/test_redacting_audit_logger.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/test_redacting_audit_logger.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from harness_claw.gateway.audit import AuditEvent, AuditLogger
from harness_claw.gateway.redaction import (
    RedactionConfig,
    RegexRedactor,
    RedactingAuditLogger,
)


def _redactor() -> RegexRedactor:
    return RegexRedactor(RedactionConfig())


def test_log_scrubs_details_and_delegates(tmp_path: Path):
    inner = AuditLogger(tmp_path / "a.jsonl")
    wrapper = RedactingAuditLogger(inner, _redactor())
    wrapper.log(AuditEvent(
        subject="alice", operation="memory.set", resource="ns/k",
        outcome="allowed",
        details={"value": "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-"},
    ))
    written = (tmp_path / "a.jsonl").read_text()
    assert "sk-ant-" not in written
    assert "[REDACTED:anthropic_key]" in written


def test_structural_fields_preserved(tmp_path: Path):
    inner = AuditLogger(tmp_path / "a.jsonl")
    wrapper = RedactingAuditLogger(inner, _redactor())
    wrapper.log(AuditEvent(
        subject="alice@example.com",          # would match email rule (but off by default)
        operation="sk-ant-ignored",           # would match anthropic rule if redacted
        resource="ghp_IgnoredResourceString1234567890",
        outcome="allowed",
        details={},
    ))
    written = (tmp_path / "a.jsonl").read_text()
    # Structural fields are NEVER redacted even when rules would match.
    assert "alice@example.com" in written
    assert "sk-ant-ignored" in written
    assert "ghp_IgnoredResourceString1234567890" in written


def test_delegates_exactly_once(tmp_path: Path):
    inner = MagicMock(spec=AuditLogger)
    wrapper = RedactingAuditLogger(inner, _redactor())
    wrapper.log(AuditEvent(
        subject="s", operation="o", resource="r", outcome="allowed", details={},
    ))
    assert inner.log.call_count == 1


def test_redactor_failure_falls_open_with_error_entry(tmp_path: Path):
    inner = AuditLogger(tmp_path / "a.jsonl")

    class ExplodingRedactor:
        def redact(self, value):
            raise RuntimeError("boom")

    wrapper = RedactingAuditLogger(inner, ExplodingRedactor())
    wrapper.log(AuditEvent(
        subject="alice", operation="memory.set", resource="ns/k",
        outcome="allowed",
        details={"value": "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-"},
    ))
    written = (tmp_path / "a.jsonl").read_text()
    # Original payload was written unredacted (fail-open), plus an ERROR entry.
    assert "sk-ant-api03" in written
    assert '"outcome": "error"' in written
    assert "redaction_failed" in written
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gateway/test_redacting_audit_logger.py -v`
Expected: FAIL — `ImportError: cannot import name 'RedactingAuditLogger'`

- [ ] **Step 3: Implement `RedactingAuditLogger`**

Append to `harness_claw/gateway/redaction.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gateway/test_redacting_audit_logger.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/redaction.py tests/gateway/test_redacting_audit_logger.py
git commit -m "feat(redaction): add RedactingAuditLogger decorator with fail-open"
```

---

## Task 4: `RedactingMemoryStore` decorator

**Files:**
- Modify: `harness_claw/gateway/redaction.py`
- Test: `tests/gateway/test_redacting_memory_store.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/gateway/test_redacting_memory_store.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from harness_claw.gateway.memory import SqliteMemoryStore
from harness_claw.gateway.redaction import (
    RedactionConfig,
    RegexRedactor,
    RedactingMemoryStore,
)


def _redactor() -> RegexRedactor:
    return RegexRedactor(RedactionConfig())


@pytest.mark.asyncio
async def test_set_scrubs_value_and_summary(tmp_path: Path):
    inner = SqliteMemoryStore(tmp_path / "m.db")
    wrapper = RedactingMemoryStore(inner, _redactor())
    await wrapper.set(
        namespace="ns", key="k",
        value="token: sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-",
        summary="summary ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
        tags=["alpha"],
    )
    entry = await inner.get("ns", "k")
    assert entry is not None
    assert "sk-ant-" not in entry.value
    assert "[REDACTED:anthropic_key]" in entry.value
    assert "[REDACTED:github_token]" in entry.summary


@pytest.mark.asyncio
async def test_set_preserves_namespace_key_and_tags(tmp_path: Path):
    inner = SqliteMemoryStore(tmp_path / "m.db")
    wrapper = RedactingMemoryStore(inner, _redactor())
    # Tag that happens to look like a secret is preserved verbatim.
    await wrapper.set(
        namespace="ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
        key="sk-ant-key-name",
        value="value",
        summary=None,
        tags=["sk-ant-tag"],
    )
    entry = await inner.get(
        "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
        "sk-ant-key-name",
    )
    assert entry is not None
    assert entry.namespace == "ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    assert entry.key == "sk-ant-key-name"
    assert entry.tags == ["sk-ant-tag"]


@pytest.mark.asyncio
async def test_reads_are_straight_delegates():
    inner = MagicMock()
    inner.get = AsyncMock(return_value="get_sentinel")
    inner.list = AsyncMock(return_value="list_sentinel")
    inner.search = AsyncMock(return_value="search_sentinel")
    inner.delete = AsyncMock(return_value=None)
    inner.namespaces = AsyncMock(return_value=["a", "b"])

    wrapper = RedactingMemoryStore(inner, _redactor())
    assert await wrapper.get("n", "k") == "get_sentinel"
    assert await wrapper.list("n") == "list_sentinel"
    assert await wrapper.search("n", "q", top_k=7, fts_weight=0.3, vec_weight=0.7) == "search_sentinel"
    await wrapper.delete("n", "k")
    assert await wrapper.namespaces() == ["a", "b"]

    inner.search.assert_awaited_once_with("n", "q", top_k=7, fts_weight=0.3, vec_weight=0.7)


@pytest.mark.asyncio
async def test_end_to_end_against_real_store(tmp_path: Path):
    inner = SqliteMemoryStore(tmp_path / "m.db")
    wrapper = RedactingMemoryStore(inner, _redactor())
    await wrapper.set(
        namespace="ns", key="k",
        value="aws AKIAIOSFODNN7EXAMPLE",
        summary=None, tags=[],
    )
    roundtrip = await wrapper.get("ns", "k")
    assert roundtrip is not None
    assert "AKIA" not in roundtrip.value
    assert "[REDACTED:aws_access_key]" in roundtrip.value
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gateway/test_redacting_memory_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'RedactingMemoryStore'`

- [ ] **Step 3: Implement `RedactingMemoryStore`**

Append to `harness_claw/gateway/redaction.py`:

```python
from harness_claw.gateway.memory import MemoryEntry, MemoryStore


class RedactingMemoryStore:
    """Drop-in replacement for `MemoryStore` that scrubs `value` and `summary`
    before write. `namespace`, `key`, and `tags` are preserved verbatim —
    redacting them would break lookups and tag semantics.
    Read methods delegate unchanged.
    """

    def __init__(self, inner: MemoryStore, redactor: Redactor) -> None:
        self._inner = inner
        self._redactor = redactor

    async def set(
        self,
        namespace: str,
        key: str,
        value: str,
        summary: str | None,
        tags: list[str],
    ) -> None:
        scrubbed_value = self._redactor.redact(value)
        scrubbed_summary = self._redactor.redact(summary) if summary else summary
        await self._inner.set(
            namespace, key, scrubbed_value,
            summary=scrubbed_summary, tags=tags,
        )

    async def get(self, namespace: str, key: str) -> MemoryEntry | None:
        return await self._inner.get(namespace, key)

    async def list(self, namespace: str) -> list[MemoryEntry]:
        return await self._inner.list(namespace)

    async def search(
        self,
        namespace: str,
        query: str,
        top_k: int = 10,
        fts_weight: float = 0.4,
        vec_weight: float = 0.6,
    ) -> list[MemoryEntry]:
        return await self._inner.search(
            namespace, query,
            top_k=top_k, fts_weight=fts_weight, vec_weight=vec_weight,
        )

    async def delete(self, namespace: str, key: str) -> None:
        await self._inner.delete(namespace, key)

    async def namespaces(self) -> list[str]:
        return await self._inner.namespaces()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gateway/test_redacting_memory_store.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/redaction.py tests/gateway/test_redacting_memory_store.py
git commit -m "feat(redaction): add RedactingMemoryStore decorator"
```

---

## Task 5: Wire decorators into `server.py` startup + ship `redaction.yaml`

**Files:**
- Modify: `harness_claw/server.py`
- Create: `redaction.yaml`

This task is wiring-only. No new tests; existing `tests/gateway/*.py` covers the `GatewayMCP` round trip and will silently exercise the decorators once wired.

- [ ] **Step 1: Add the import**

In `harness_claw/server.py`, add this import alongside the other `harness_claw.gateway.*` imports near the top of the file:

```python
from harness_claw.gateway.redaction import (
    RedactionConfig,
    RegexRedactor,
    RedactingAuditLogger,
    RedactingMemoryStore,
)
```

- [ ] **Step 2: Replace the memory and audit construction lines**

`harness_claw/server.py` currently has these two lines (at the time of writing, lines 76 and 80 — `grep -n "memory = \|audit = " harness_claw/server.py` confirms):

```python
memory = SqliteMemoryStore(_memory_db)
...
audit = AuditLogger(_audit_jsonl)
```

Nothing between those two lines (77–79) reads `memory` or `audit`, so it is safe to delete both and replace them with a single block. Edit the file so:

1. Line 76 (`memory = SqliteMemoryStore(_memory_db)`) is **deleted**.
2. Line 80 (`audit = AuditLogger(_audit_jsonl)`) is **replaced** with:

```python
_redaction_config = RedactionConfig.load()
_redactor = RegexRedactor(_redaction_config)

_audit_inner = AuditLogger(_audit_jsonl)
_memory_inner = SqliteMemoryStore(_memory_db)

if _redaction_config.enabled:
    audit = RedactingAuditLogger(_audit_inner, _redactor)
    memory = RedactingMemoryStore(_memory_inner, _redactor)
else:
    audit = _audit_inner
    memory = _memory_inner
```

After the edit, `memory` and `audit` are defined at the same point (where `audit` used to be). All downstream consumers still see `memory` and `audit` as expected.

- [ ] **Step 3: Create the example `redaction.yaml`**

Create `redaction.yaml` at project root:

```yaml
# HarnessClaw redaction configuration.
# Missing file => defaults (secrets on, PII off). Set enabled: false to disable
# redaction entirely without editing wiring.

enabled: true

categories:
  anthropic_key: on
  openai_key: on
  github_token: on
  aws_access_key: on
  aws_secret_key: on
  gcp_key: on
  slack_webhook: on
  jwt: on
  ssh_private_key: on
  email: off
  phone_us: off
  credit_card: off
  ssn_us: off
  user_path: off

# custom_rules:
#   - name: internal_api_token
#     pattern: 'ACME-[A-Z0-9]{32}'
#     replacement: '[REDACTED:internal_api_token]'
```

- [ ] **Step 4: Smoke-check wiring and run the existing gateway tests**

Run:
```bash
python -c "from harness_claw import server; print('import ok')"
pytest tests/gateway -v
```

Expected: `import ok`, and all existing gateway tests pass. No test changed, so the expected count matches the current count.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/server.py redaction.yaml
git commit -m "feat(redaction): wire RedactingAuditLogger + RedactingMemoryStore at startup"
```

---

## Task 6: Backfill CLI — `harnessclaw redact-existing`

**Files:**
- Create: `harness_claw/redact_backfill.py`
- Modify: `harness_claw/cli.py`
- Test: `tests/test_redact_backfill.py`

The core redaction logic is already in `RegexRedactor`. This task adds a standalone entrypoint that applies it to existing on-disk data.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_redact_backfill.py`:

```python
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from harness_claw.gateway.memory import SqliteMemoryStore
from harness_claw.gateway.redaction import RedactionConfig, RegexRedactor
from harness_claw.redact_backfill import (
    BackfillReport,
    run_backfill,
)


def _seed_audit(path: Path) -> None:
    path.write_text(
        json.dumps({
            "event_id": "1", "timestamp": "2026-04-19T00:00:00Z",
            "subject": "alice", "operation": "memory.set", "resource": "ns/k",
            "outcome": "allowed",
            "details": {"value": "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-"},
        }) + "\n" +
        json.dumps({
            "event_id": "2", "timestamp": "2026-04-19T00:00:01Z",
            "subject": "alice", "operation": "memory.list", "resource": "ns",
            "outcome": "allowed", "details": {"count": 3},
        }) + "\n"
    )


async def _seed_memory(path: Path) -> None:
    store = SqliteMemoryStore(path)
    await store.set("ns", "secret", "token: ghp_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
                    summary=None, tags=[])
    await store.set("ns", "clean", "nothing here", summary=None, tags=[])


@pytest.mark.asyncio
async def test_dry_run_reports_counts_and_mutates_nothing(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    memory = tmp_path / "memory.db"
    _seed_audit(audit)
    await _seed_memory(memory)

    audit_before = audit.read_bytes()
    memory_before = memory.read_bytes()

    redactor = RegexRedactor(RedactionConfig())
    report: BackfillReport = await run_backfill(
        redactor=redactor,
        audit_path=audit,
        memory_path=memory,
        apply=False,
    )

    assert report.audit_records_scanned == 2
    assert report.audit_records_changed == 1
    assert report.memory_entries_scanned == 2
    assert report.memory_entries_changed == 1

    # Nothing touched on disk.
    assert audit.read_bytes() == audit_before
    assert memory.read_bytes() == memory_before
    assert not (tmp_path / "audit.jsonl.bak").exists()


@pytest.mark.asyncio
async def test_apply_rewrites_atomically_with_bak(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    memory = tmp_path / "memory.db"
    _seed_audit(audit)
    await _seed_memory(memory)

    redactor = RegexRedactor(RedactionConfig())
    await run_backfill(
        redactor=redactor, audit_path=audit, memory_path=memory, apply=True,
    )

    # audit.jsonl rewritten; .bak holds original.
    new_audit = audit.read_text().splitlines()
    assert "[REDACTED:anthropic_key]" in new_audit[0]
    assert "sk-ant-" not in new_audit[0]
    bak = (tmp_path / "audit.jsonl.bak").read_text()
    assert "sk-ant-" in bak

    # Memory DB: previously-secret row now redacted.
    store = SqliteMemoryStore(memory)
    entry = await store.get("ns", "secret")
    assert entry is not None
    assert "ghp_" not in entry.value
    assert "[REDACTED:github_token]" in entry.value


@pytest.mark.asyncio
async def test_second_apply_is_noop(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    memory = tmp_path / "memory.db"
    _seed_audit(audit)
    await _seed_memory(memory)

    redactor = RegexRedactor(RedactionConfig())
    await run_backfill(
        redactor=redactor, audit_path=audit, memory_path=memory, apply=True,
    )
    second = await run_backfill(
        redactor=redactor, audit_path=audit, memory_path=memory, apply=True,
    )
    assert second.audit_records_changed == 0
    assert second.memory_entries_changed == 0


@pytest.mark.asyncio
async def test_missing_audit_file_is_not_an_error(tmp_path: Path):
    memory = tmp_path / "memory.db"
    await _seed_memory(memory)
    redactor = RegexRedactor(RedactionConfig())
    report = await run_backfill(
        redactor=redactor,
        audit_path=tmp_path / "does_not_exist.jsonl",
        memory_path=memory,
        apply=False,
    )
    assert report.audit_records_scanned == 0
    assert report.memory_entries_scanned == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_redact_backfill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'harness_claw.redact_backfill'`

- [ ] **Step 3: Implement the backfill module**

Create `harness_claw/redact_backfill.py`:

```python
from __future__ import annotations

import json
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from harness_claw.gateway.redaction import Redactor


@dataclass
class BackfillReport:
    audit_records_scanned: int = 0
    audit_records_changed: int = 0
    memory_entries_scanned: int = 0
    memory_entries_changed: int = 0
    audit_by_category: dict[str, int] = field(default_factory=dict)
    memory_by_category: dict[str, int] = field(default_factory=dict)


def _count_categories(before: str, after: str, into: dict[str, int]) -> None:
    # Rough category accounting: count every [REDACTED:<name>] token that
    # appears in `after` but not in `before`.
    import re
    tag = re.compile(r"\[REDACTED:([a-z_]+)\]")
    for m in tag.finditer(after):
        name = m.group(1)
        # Only count tokens that weren't already present.
        before_count = before.count(f"[REDACTED:{name}]")
        after_count = after.count(f"[REDACTED:{name}]")
        delta = after_count - before_count
        if delta > 0 and into.get(name, 0) < after_count:
            into[name] = into.get(name, 0) + 1


async def _backfill_audit(
    redactor: Redactor, path: Path, apply: bool, report: BackfillReport,
) -> None:
    if not path.exists():
        return

    tmp = path.with_suffix(path.suffix + ".tmp")
    with path.open("r") as src, tmp.open("w") as dst:
        for line in src:
            if not line.strip():
                dst.write(line)
                continue
            report.audit_records_scanned += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                dst.write(line)
                continue

            original_details = record.get("details") or {}
            new_details = redactor.redact(original_details)
            if new_details != original_details:
                report.audit_records_changed += 1
                _count_categories(
                    json.dumps(original_details), json.dumps(new_details),
                    report.audit_by_category,
                )
                record["details"] = new_details
            dst.write(json.dumps(record) + "\n")

    if apply and report.audit_records_changed > 0:
        bak = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, bak)
        os.replace(tmp, path)
    else:
        tmp.unlink(missing_ok=True)


def _check_memory_lock(path: Path) -> None:
    """Raise RuntimeError if another process holds a write lock on memory.db."""
    if not path.exists():
        return
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
    except sqlite3.OperationalError as e:
        raise RuntimeError(
            f"memory.db is locked by another process — stop the server before "
            f"running redact-existing: {e}"
        )
    finally:
        conn.close()


async def _backfill_memory(
    redactor: Redactor, path: Path, apply: bool, report: BackfillReport,
) -> None:
    if not path.exists():
        return

    _check_memory_lock(path)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT namespace, key, value, summary FROM memory"
        ).fetchall()
        report.memory_entries_scanned = len(rows)

        updates: list[tuple[str, str | None, str, str]] = []
        for row in rows:
            new_value = redactor.redact(row["value"])
            new_summary = (
                redactor.redact(row["summary"]) if row["summary"] is not None else None
            )
            if new_value != row["value"] or new_summary != row["summary"]:
                report.memory_entries_changed += 1
                _count_categories(
                    f"{row['value']} {row['summary'] or ''}",
                    f"{new_value} {new_summary or ''}",
                    report.memory_by_category,
                )
                updates.append((new_value, new_summary, row["namespace"], row["key"]))

        if apply and updates:
            conn.executemany(
                "UPDATE memory SET value=?, summary=? WHERE namespace=? AND key=?",
                updates,
            )
            conn.commit()
    finally:
        conn.close()


async def run_backfill(
    *,
    redactor: Redactor,
    audit_path: Path,
    memory_path: Path,
    apply: bool,
) -> BackfillReport:
    report = BackfillReport()
    await _backfill_audit(redactor, audit_path, apply, report)
    await _backfill_memory(redactor, memory_path, apply, report)
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_redact_backfill.py -v`
Expected: 4 passed.

- [ ] **Step 5: Wire the CLI subcommand**

Edit `harness_claw/cli.py`. Update the usage banner and dispatcher.

Replace the `main()` function with:

```python
def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: harnessclaw <command>")
        print("Commands:")
        print("  run                              Start HarnessClaw server + UI")
        print("  attach --role <role> [--dir <d>] Hook this terminal into HarnessClaw")
        print("  redact-existing [--apply]        Backfill redaction over audit.jsonl + memory.db")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "run":
        _run()
    elif cmd == "attach":
        _attach(sys.argv[2:])
    elif cmd == "redact-existing":
        _redact_existing(sys.argv[2:])
    else:
        print(f"Unknown command: {cmd!r}")
        sys.exit(1)
```

Append to the same file:

```python
def _redact_existing(args: list[str]) -> None:
    import asyncio
    from harness_claw.gateway.redaction import RedactionConfig, RegexRedactor
    from harness_claw.redact_backfill import run_backfill

    config_path = Path("redaction.yaml")
    audit_path = Path("audit.jsonl")
    memory_path = Path("memory.db")
    apply = False
    emit_json = False
    force = False

    i = 0
    while i < len(args):
        if args[i] == "--config" and i + 1 < len(args):
            config_path = Path(args[i + 1]); i += 2
        elif args[i] == "--audit" and i + 1 < len(args):
            audit_path = Path(args[i + 1]); i += 2
        elif args[i] == "--memory" and i + 1 < len(args):
            memory_path = Path(args[i + 1]); i += 2
        elif args[i] == "--apply":
            apply = True; i += 1
        elif args[i] == "--json":
            emit_json = True; i += 1
        elif args[i] == "--force":
            force = True; i += 1
        else:
            print(f"Unknown flag: {args[i]!r}")
            sys.exit(1)

    cfg = RedactionConfig.load(config_path)
    if not cfg.enabled and not force:
        print("redaction.yaml has enabled: false — nothing to redact. Use --force to run anyway.")
        sys.exit(1)
    redactor = RegexRedactor(cfg)

    try:
        report = asyncio.run(run_backfill(
            redactor=redactor,
            audit_path=audit_path,
            memory_path=memory_path,
            apply=apply,
        ))
    except RuntimeError as e:
        print(f"error: {e}")
        sys.exit(2)

    if emit_json:
        from dataclasses import asdict
        print(json.dumps(asdict(report), indent=2))
        return

    print(f"{audit_path}: scanned {report.audit_records_scanned} records")
    print(f"  - would redact {report.audit_records_changed} records" if not apply
          else f"  - redacted {report.audit_records_changed} records")
    for cat, count in sorted(report.audit_by_category.items()):
        print(f"    {cat}: {count}")

    print()
    print(f"{memory_path}: scanned {report.memory_entries_scanned} entries")
    print(f"  - would redact {report.memory_entries_changed} entries" if not apply
          else f"  - redacted {report.memory_entries_changed} entries")
    for cat, count in sorted(report.memory_by_category.items()):
        print(f"    {cat}: {count}")

    if not apply:
        print()
        print("Dry run. Re-run with --apply to commit.")
```

- [ ] **Step 6: Smoke-test the CLI**

Run:
```bash
python -c "from harness_claw.cli import main" || true  # import works
harnessclaw redact-existing --audit /tmp/nope.jsonl --memory /tmp/nope.db
```

Expected: usage-style "scanned 0" output, dry run, exit 0.

- [ ] **Step 7: Commit**

```bash
git add harness_claw/redact_backfill.py harness_claw/cli.py tests/test_redact_backfill.py
git commit -m "feat(redaction): add harnessclaw redact-existing backfill CLI"
```

---

## Final verification

- [ ] **Run the full redaction test suite**

```bash
pytest tests/gateway/test_redaction_config.py \
       tests/gateway/test_redaction_redactor.py \
       tests/gateway/test_redacting_audit_logger.py \
       tests/gateway/test_redacting_memory_store.py \
       tests/test_redact_backfill.py -v
```

Expected: 6 + 15 + 4 + 4 + 4 = 33 passed.

- [ ] **Run the existing gateway suite to catch regressions**

```bash
pytest tests/gateway -v
```

Expected: everything still passes. No test was modified.

---

## Out of scope (deferred)

- In-flight redaction of MCP tool arguments (semantic-breaking; needs per-tool opt-in).
- Reversible/vault redaction.
- Per-namespace redaction policy.
- PTY byte-stream scrubbing.
- Metrics/alerts for redaction events (counters, Grafana panels, etc.).
