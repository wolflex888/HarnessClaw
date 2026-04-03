from __future__ import annotations
import pytest
from harness_claw.gateway.memory import SqliteMemoryStore, MemoryEntry, Embedder


@pytest.fixture
def store(tmp_path):
    return SqliteMemoryStore(tmp_path / "memory.db")


async def test_set_and_get(store):
    await store.set("project:test", "key1", "value1", summary="a note", tags=["tag1"])
    entry = await store.get("project:test", "key1")
    assert entry is not None
    assert entry.value == "value1"
    assert entry.summary == "a note"
    assert "tag1" in entry.tags


async def test_get_missing_returns_none(store):
    result = await store.get("project:test", "missing")
    assert result is None


async def test_delete_removes_entry(store):
    await store.set("project:test", "k", "v", summary=None, tags=[])
    await store.delete("project:test", "k")
    assert await store.get("project:test", "k") is None


async def test_list_returns_entries_in_namespace(store):
    await store.set("ns1", "a", "va", summary=None, tags=[])
    await store.set("ns1", "b", "vb", summary=None, tags=[])
    await store.set("ns2", "c", "vc", summary=None, tags=[])
    entries = await store.list("ns1")
    keys = [e.key for e in entries]
    assert "a" in keys
    assert "b" in keys
    assert "c" not in keys


async def test_search_finds_by_content(store):
    await store.set("project:x", "notes", "authentication token design", summary=None, tags=[])
    await store.set("project:x", "other", "unrelated content", summary=None, tags=[])
    results = await store.search("project:x", "authentication")
    assert any(e.key == "notes" for e in results)


async def test_namespaces_lists_used_namespaces(store):
    await store.set("ns-a", "k", "v", summary=None, tags=[])
    await store.set("ns-b", "k", "v", summary=None, tags=[])
    ns = await store.namespaces()
    assert "ns-a" in ns
    assert "ns-b" in ns


async def test_set_updates_existing_entry(store):
    await store.set("ns", "k", "original", summary=None, tags=[])
    await store.set("ns", "k", "updated", summary="new summary", tags=[])
    entry = await store.get("ns", "k")
    assert entry.value == "updated"
    assert entry.summary == "new summary"


def test_embedder_produces_768_dim_vector():
    embedder = Embedder()
    vec = embedder.embed("hello world")
    assert len(vec) == 768


def test_embedder_lazy_loads_model():
    embedder = Embedder()
    assert embedder._model is None
    embedder.embed("trigger load")
    assert embedder._model is not None


def test_embedder_similar_texts_have_high_similarity():
    embedder = Embedder()
    v1 = embedder.embed("JWT token validation skips expiry check")
    v2 = embedder.embed("authentication bug in token expiry")
    v3 = embedder.embed("how to bake chocolate cake")
    import numpy as np
    def cosine(a, b):
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
    sim_related = cosine(v1, v2)
    sim_unrelated = cosine(v1, v3)
    assert sim_related > sim_unrelated
    assert sim_related > 0.5
