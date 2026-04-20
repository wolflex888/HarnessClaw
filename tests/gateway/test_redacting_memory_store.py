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
