from __future__ import annotations
import json
import pytest
from harness_claw.gateway.audit import AuditLogger, AuditEvent


def test_log_writes_jsonl(tmp_path):
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(path)
    logger.log(AuditEvent(
        subject="s1",
        operation="agent.delegate",
        resource="task-123",
        outcome="allowed",
        details={"caps": ["python"]},
    ))
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["subject"] == "s1"
    assert event["operation"] == "agent.delegate"
    assert event["outcome"] == "allowed"
    assert "event_id" in event
    assert "timestamp" in event


def test_log_appends_multiple_events(tmp_path):
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(path)
    logger.log(AuditEvent(subject="s1", operation="op1", resource="r1", outcome="allowed", details={}))
    logger.log(AuditEvent(subject="s2", operation="op2", resource="r2", outcome="denied", details={}))
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 2


def test_log_creates_file_if_missing(tmp_path):
    path = tmp_path / "subdir" / "audit.jsonl"
    logger = AuditLogger(path)
    logger.log(AuditEvent(subject="s1", operation="op", resource="r", outcome="allowed", details={}))
    assert path.exists()
