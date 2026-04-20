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


def test_event_id_and_timestamp_preserved(tmp_path: Path):
    import json as _json
    inner = AuditLogger(tmp_path / "a.jsonl")
    wrapper = RedactingAuditLogger(inner, _redactor())
    event = AuditEvent(
        subject="alice", operation="memory.set", resource="ns/k",
        outcome="allowed", details={"value": "ok"},
    )
    wrapper.log(event)
    line = (tmp_path / "a.jsonl").read_text().strip()
    written = _json.loads(line)
    assert written["event_id"] == event.event_id
    assert written["timestamp"] == event.timestamp
