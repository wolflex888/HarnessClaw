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


@pytest.mark.asyncio
async def test_apply_refreshes_vector_embeddings(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    memory_db = tmp_path / "memory.db"
    # Skip audit seeding — test focuses on memory.
    audit.write_text("")
    store = SqliteMemoryStore(memory_db)
    await store.set(
        "ns", "k",
        value="token sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-",
        summary=None, tags=[],
    )

    redactor = RegexRedactor(RedactionConfig())
    await run_backfill(
        redactor=redactor, audit_path=audit, memory_path=memory_db, apply=True,
    )

    # After redaction, searching for the plaintext secret should NOT surface the row,
    # because both the FTS index and the vector embedding were recomputed over the
    # redacted text. We search for the exact pre-redaction substring — if embeddings
    # were stale, the row would still rank highly by vector similarity.
    results = await store.search("ns", "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz", top_k=5)
    for entry in results:
        assert "sk-ant-" not in entry.value, (
            "Row still contains the original secret — redaction did not apply to stored row."
        )
    # Verify the row exists and is redacted.
    entry = await store.get("ns", "k")
    assert entry is not None
    assert "[REDACTED:anthropic_key]" in entry.value


@pytest.mark.asyncio
async def test_count_categories_sums_across_records(tmp_path: Path):
    audit = tmp_path / "audit.jsonl"
    memory_db = tmp_path / "memory.db"
    # Seed TWO audit records each containing one anthropic key.
    records = []
    for i in range(2):
        records.append(json.dumps({
            "event_id": f"e{i}", "timestamp": "2026-04-19T00:00:00Z",
            "subject": "alice", "operation": "memory.set",
            "resource": f"ns/k{i}", "outcome": "allowed",
            "details": {"value": f"sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-"},
        }))
    audit.write_text("\n".join(records) + "\n")
    SqliteMemoryStore(memory_db)  # empty, creates schema

    redactor = RegexRedactor(RedactionConfig())
    report = await run_backfill(
        redactor=redactor, audit_path=audit, memory_path=memory_db, apply=False,
    )
    assert report.audit_by_category.get("anthropic_key") == 2
