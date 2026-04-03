from __future__ import annotations

import numpy as np
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
    def cosine(a, b):
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
    sim_related = cosine(v1, v2)
    sim_unrelated = cosine(v1, v3)
    assert sim_related > sim_unrelated
    assert sim_related > 0.5


async def test_set_stores_embedding_vector(store):
    await store.set("ns", "k", "JWT token validation", summary="auth note", tags=[])
    row = store._conn.execute(
        "SELECT embedding FROM memory_vectors WHERE namespace=? AND key=?", ("ns", "k")
    ).fetchone()
    assert row is not None
    vec = Embedder.from_blob(row["embedding"])
    assert len(vec) == 768


async def test_set_updates_embedding_on_overwrite(store):
    await store.set("ns", "k", "original text", summary=None, tags=[])
    row1 = store._conn.execute(
        "SELECT embedding FROM memory_vectors WHERE namespace=? AND key=?", ("ns", "k")
    ).fetchone()
    await store.set("ns", "k", "completely different text", summary=None, tags=[])
    row2 = store._conn.execute(
        "SELECT embedding FROM memory_vectors WHERE namespace=? AND key=?", ("ns", "k")
    ).fetchone()
    assert row1["embedding"] != row2["embedding"]


async def test_delete_removes_embedding_vector(store):
    await store.set("ns", "k", "some value", summary=None, tags=[])
    row = store._conn.execute(
        "SELECT embedding FROM memory_vectors WHERE namespace=? AND key=?", ("ns", "k")
    ).fetchone()
    assert row is not None
    await store.delete("ns", "k")
    row = store._conn.execute(
        "SELECT embedding FROM memory_vectors WHERE namespace=? AND key=?", ("ns", "k")
    ).fetchone()
    assert row is None


async def test_search_finds_semantically_similar_entries(store):
    """The key test: FTS5 alone would miss this because there's no keyword overlap."""
    await store.set("ns", "auth-bug", "JWT token validation skips the expiry check",
                    summary="token expiry bug", tags=["auth"])
    await store.set("ns", "db-schema", "database uses PostgreSQL with UUID primary keys",
                    summary="db design", tags=["database"])
    await store.set("ns", "cake", "how to bake a chocolate cake recipe",
                    summary="baking", tags=["food"])
    results = await store.search("ns", "authentication bug")
    keys = [e.key for e in results]
    # "auth-bug" should be found even though "authentication bug" doesn't appear in its text
    assert "auth-bug" in keys
    # "cake" should not be in results (or ranked very low)
    if "cake" in keys:
        assert keys.index("auth-bug") < keys.index("cake")


async def test_search_hybrid_returns_fts_and_vector_matches(store):
    """FTS5 matches (exact keyword) and vector matches (semantic) both appear."""
    await store.set("ns", "exact-match", "the authentication module handles login",
                    summary=None, tags=[])
    await store.set("ns", "semantic-match", "JWT token validation and session management",
                    summary=None, tags=[])
    await store.set("ns", "unrelated", "chocolate cake baking instructions",
                    summary=None, tags=[])
    results = await store.search("ns", "authentication")
    keys = [e.key for e in results]
    assert "exact-match" in keys      # FTS5 hit (keyword "authentication")
    assert "semantic-match" in keys   # vector hit (semantically related)


async def test_search_empty_namespace_returns_empty(store):
    results = await store.search("empty-ns", "anything")
    assert results == []


async def test_search_handles_fts_syntax_error_gracefully(store):
    """Queries with special chars that break FTS5 should still return vector results."""
    await store.set("ns", "note", "authentication token validation", summary=None, tags=[])
    # Parentheses break FTS5 syntax
    results = await store.search("ns", "auth (token)")
    # Should not raise, should return vector-only results
    assert isinstance(results, list)
