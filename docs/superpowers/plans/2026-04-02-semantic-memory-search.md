# Semantic Memory Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add hybrid FTS5 + vector embedding search to `SqliteMemoryStore` so agents can find semantically relevant memories even without keyword overlap.

**Architecture:** Embed memory values using `all-mpnet-base-v2` (sentence-transformers) on write, store 768-dim vectors in a new `memory_vectors` SQLite table, and merge FTS5 keyword scores with cosine similarity scores on search. Model is lazy-loaded on first use.

**Tech Stack:** Python, SQLite, sentence-transformers, numpy

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `harness_claw/gateway/memory.py` | Modify | Add `Embedder` class, `memory_vectors` table, hybrid search logic |
| `tests/gateway/test_memory.py` | Modify | Add tests for embedding storage, hybrid search, semantic matching |
| `pyproject.toml` | Modify | Add `sentence-transformers` dependency |

---

### Task 1: Add `sentence-transformers` dependency

**Files:**
- Modify: `pyproject.toml:6-13`

- [ ] **Step 1: Add dependency to pyproject.toml**

In `pyproject.toml`, add `sentence-transformers` to the dependencies list:

```toml
dependencies = [
    "fastapi>=0.111.0",
    "uvicorn[standard]>=0.29.0",
    "pydantic>=2.7.0",
    "pyyaml>=6.0.1",
    "anthropic>=0.30.0",
    "ptyprocess>=0.7.0",
    "mcp>=1.3.0",
    "sentence-transformers>=3.0.0",
]
```

- [ ] **Step 2: Install the dependency**

Run: `uv sync`
Expected: installs sentence-transformers and its dependencies (torch, transformers, numpy)

- [ ] **Step 3: Verify import works**

Run: `python -c "from sentence_transformers import SentenceTransformer; print('ok')"`
Expected: prints `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add sentence-transformers dependency for semantic memory search"
```

---

### Task 2: Add `Embedder` helper class with lazy model loading

**Files:**
- Modify: `harness_claw/gateway/memory.py`
- Test: `tests/gateway/test_memory.py`

- [ ] **Step 1: Write the failing test for Embedder**

Add to `tests/gateway/test_memory.py`:

```python
from harness_claw.gateway.memory import Embedder


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
    # cosine similarity: similar texts should score higher
    import numpy as np
    def cosine(a, b):
        return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))
    sim_related = cosine(v1, v2)
    sim_unrelated = cosine(v1, v3)
    assert sim_related > sim_unrelated
    assert sim_related > 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gateway/test_memory.py::test_embedder_produces_768_dim_vector -v`
Expected: FAIL with `ImportError: cannot import name 'Embedder'`

- [ ] **Step 3: Implement Embedder class**

Add to `harness_claw/gateway/memory.py`, after the imports and before `MemoryEntry`:

```python
import struct

import numpy as np


class Embedder:
    """Lazy-loading sentence-transformers embedder."""

    MODEL_NAME = "all-mpnet-base-v2"
    DIMS = 768

    def __init__(self) -> None:
        self._model = None

    def _load(self) -> None:
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.MODEL_NAME)

    def embed(self, text: str) -> np.ndarray:
        """Return a normalized embedding vector for the given text."""
        self._load()
        vec = self._model.encode(text, normalize_embeddings=True)
        return vec

    @staticmethod
    def to_blob(vec: np.ndarray) -> bytes:
        """Serialize a float32 vector to bytes for SQLite BLOB storage."""
        return vec.astype(np.float32).tobytes()

    @staticmethod
    def from_blob(blob: bytes) -> np.ndarray:
        """Deserialize bytes back to a float32 numpy array."""
        return np.frombuffer(blob, dtype=np.float32)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gateway/test_memory.py::test_embedder_produces_768_dim_vector tests/gateway/test_memory.py::test_embedder_lazy_loads_model tests/gateway/test_memory.py::test_embedder_similar_texts_have_high_similarity -v`
Expected: all 3 PASS

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/memory.py tests/gateway/test_memory.py
git commit -m "feat: add Embedder class with lazy model loading"
```

---

### Task 3: Add `memory_vectors` table and store embeddings on `set()`

**Files:**
- Modify: `harness_claw/gateway/memory.py`
- Test: `tests/gateway/test_memory.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/gateway/test_memory.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gateway/test_memory.py::test_set_stores_embedding_vector -v`
Expected: FAIL with `no such table: memory_vectors`

- [ ] **Step 3: Add memory_vectors table to schema**

In `harness_claw/gateway/memory.py`, in `_init_schema()`, add after the existing `CREATE TRIGGER` statements but before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS memory_vectors (
    namespace TEXT NOT NULL,
    key       TEXT NOT NULL,
    embedding BLOB NOT NULL,
    PRIMARY KEY (namespace, key),
    FOREIGN KEY (namespace, key) REFERENCES memory(namespace, key) ON DELETE CASCADE
);
```

- [ ] **Step 4: Add embedder to SqliteMemoryStore and update set()**

In `harness_claw/gateway/memory.py`, modify `SqliteMemoryStore.__init__` to create an embedder:

```python
def __init__(self, path: Path) -> None:
    self._conn = sqlite3.connect(str(path), check_same_thread=False)
    self._conn.execute("PRAGMA foreign_keys = ON")
    self._conn.row_factory = sqlite3.Row
    self._embedder = Embedder()
    self._init_schema()
```

Then modify the `set()` method to also store the embedding. Add after `self._conn.commit()`:

```python
async def set(self, namespace: str, key: str, value: str, summary: str | None, tags: list[str]) -> None:
    now = self._now()
    existing = self._conn.execute(
        "SELECT created_at FROM memory WHERE namespace=? AND key=?", (namespace, key)
    ).fetchone()
    created_at = existing["created_at"] if existing else now
    self._conn.execute(
        """INSERT OR REPLACE INTO memory (namespace, key, value, summary, tags, size_bytes, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (namespace, key, value, summary, json.dumps(tags), len(value.encode()), created_at, now),
    )
    # Embed and store vector
    embed_text = f"{value} {summary}" if summary else value
    vec = self._embedder.embed(embed_text)
    self._conn.execute(
        """INSERT OR REPLACE INTO memory_vectors (namespace, key, embedding)
           VALUES (?, ?, ?)""",
        (namespace, key, self._embedder.to_blob(vec)),
    )
    self._conn.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/gateway/test_memory.py::test_set_stores_embedding_vector tests/gateway/test_memory.py::test_set_updates_embedding_on_overwrite -v`
Expected: both PASS

- [ ] **Step 6: Run all existing tests to verify no regressions**

Run: `pytest tests/gateway/test_memory.py -v`
Expected: all tests PASS

- [ ] **Step 7: Commit**

```bash
git add harness_claw/gateway/memory.py tests/gateway/test_memory.py
git commit -m "feat: store embedding vectors on memory.set()"
```

---

### Task 4: Delete vectors on `memory.delete()`

**Files:**
- Modify: `harness_claw/gateway/memory.py`
- Test: `tests/gateway/test_memory.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/gateway/test_memory.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails (or passes via CASCADE)**

Run: `pytest tests/gateway/test_memory.py::test_delete_removes_embedding_vector -v`

This may already pass if `PRAGMA foreign_keys = ON` and `ON DELETE CASCADE` are working. If it passes, great — the CASCADE handles it. If it fails, add an explicit delete.

- [ ] **Step 3: If test failed, add explicit delete to delete()**

Only if the CASCADE didn't work — update `delete()` in `harness_claw/gateway/memory.py`:

```python
async def delete(self, namespace: str, key: str) -> None:
    self._conn.execute("DELETE FROM memory_vectors WHERE namespace=? AND key=?", (namespace, key))
    self._conn.execute("DELETE FROM memory WHERE namespace=? AND key=?", (namespace, key))
    self._conn.commit()
```

- [ ] **Step 4: Run all tests**

Run: `pytest tests/gateway/test_memory.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/memory.py tests/gateway/test_memory.py
git commit -m "feat: ensure embedding vectors are deleted with memory entries"
```

---

### Task 5: Implement hybrid search

**Files:**
- Modify: `harness_claw/gateway/memory.py`
- Test: `tests/gateway/test_memory.py`

- [ ] **Step 1: Write the failing test for semantic search**

Add to `tests/gateway/test_memory.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify the semantic test fails**

Run: `pytest tests/gateway/test_memory.py::test_search_finds_semantically_similar_entries -v`
Expected: FAIL — current FTS5-only search can't find "auth-bug" when querying "authentication bug" (no keyword overlap with "JWT token validation skips the expiry check")

- [ ] **Step 3: Implement hybrid search**

Replace the `search()` method in `SqliteMemoryStore` in `harness_claw/gateway/memory.py`:

```python
async def search(self, namespace: str, query: str, top_k: int = 10,
                 fts_weight: float = 0.4, vec_weight: float = 0.6) -> list[MemoryEntry]:
    """Hybrid search: merge FTS5 keyword results with vector cosine similarity."""
    scores: dict[str, float] = {}

    # --- FTS5 keyword search ---
    fts_rows = self._conn.execute(
        """SELECT m.*, rank FROM memory m
           JOIN memory_fts f ON m.rowid = f.rowid
           WHERE f.memory_fts MATCH ? AND m.namespace = ?
           ORDER BY rank""",
        (query, namespace),
    ).fetchall()
    if fts_rows:
        # FTS5 rank is negative (more negative = better match), normalize to 0–1
        ranks = [abs(r["rank"]) for r in fts_rows]
        max_rank = max(ranks) if ranks else 1.0
        for row, rank in zip(fts_rows, ranks):
            normalized = rank / max_rank if max_rank > 0 else 0.0
            scores[row["key"]] = fts_weight * normalized

    # --- Vector cosine similarity search ---
    vec_rows = self._conn.execute(
        """SELECT mv.key, mv.embedding FROM memory_vectors mv
           WHERE mv.namespace = ?""",
        (namespace,),
    ).fetchall()
    if vec_rows:
        query_vec = self._embedder.embed(query)
        for row in vec_rows:
            stored_vec = Embedder.from_blob(row["embedding"])
            similarity = float(np.dot(query_vec, stored_vec))
            # Vectors are normalized, so dot product = cosine similarity (range -1 to 1)
            # Clamp to 0–1 for scoring
            similarity = max(0.0, similarity)
            key = row["key"]
            scores[key] = scores.get(key, 0.0) + vec_weight * similarity

    # --- Merge and rank ---
    ranked_keys = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)[:top_k]
    if not ranked_keys:
        return []

    # Fetch full entries for the top-k keys
    placeholders = ",".join("?" for _ in ranked_keys)
    entry_rows = self._conn.execute(
        f"SELECT * FROM memory WHERE namespace = ? AND key IN ({placeholders})",
        (namespace, *ranked_keys),
    ).fetchall()
    entries_by_key = {r["key"]: self._row_to_entry(r) for r in entry_rows}
    return [entries_by_key[k] for k in ranked_keys if k in entries_by_key]
```

Add `import numpy as np` to the top of the file if not already present (it was added in Task 2 with the Embedder class).

- [ ] **Step 4: Run the semantic search tests**

Run: `pytest tests/gateway/test_memory.py::test_search_finds_semantically_similar_entries tests/gateway/test_memory.py::test_search_hybrid_returns_fts_and_vector_matches tests/gateway/test_memory.py::test_search_empty_namespace_returns_empty -v`
Expected: all 3 PASS

- [ ] **Step 5: Run all tests to verify no regressions**

Run: `pytest tests/gateway/test_memory.py -v`
Expected: all tests PASS (including the original `test_search_finds_by_content`)

- [ ] **Step 6: Commit**

```bash
git add harness_claw/gateway/memory.py tests/gateway/test_memory.py
git commit -m "feat: implement hybrid FTS5 + vector cosine similarity search"
```

---

### Task 6: Handle FTS5 query syntax errors gracefully

**Files:**
- Modify: `harness_claw/gateway/memory.py`
- Test: `tests/gateway/test_memory.py`

- [ ] **Step 1: Write the failing test**

FTS5 has strict query syntax — characters like `*`, `(`, `)` can cause `OperationalError`. When FTS5 fails, search should fall back to vector-only.

Add to `tests/gateway/test_memory.py`:

```python
async def test_search_handles_fts_syntax_error_gracefully(store):
    """Queries with special chars that break FTS5 should still return vector results."""
    await store.set("ns", "note", "authentication token validation", summary=None, tags=[])
    # Parentheses break FTS5 syntax
    results = await store.search("ns", "auth (token)")
    # Should not raise, should return vector-only results
    assert isinstance(results, list)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/gateway/test_memory.py::test_search_handles_fts_syntax_error_gracefully -v`
Expected: FAIL with `sqlite3.OperationalError`

- [ ] **Step 3: Wrap FTS5 query in try/except**

In `harness_claw/gateway/memory.py`, in the `search()` method, wrap the FTS5 section:

```python
    # --- FTS5 keyword search ---
    try:
        fts_rows = self._conn.execute(
            """SELECT m.*, rank FROM memory m
               JOIN memory_fts f ON m.rowid = f.rowid
               WHERE f.memory_fts MATCH ? AND m.namespace = ?
               ORDER BY rank""",
            (query, namespace),
        ).fetchall()
    except sqlite3.OperationalError:
        fts_rows = []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/gateway/test_memory.py::test_search_handles_fts_syntax_error_gracefully -v`
Expected: PASS

- [ ] **Step 5: Run all tests**

Run: `pytest tests/gateway/test_memory.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add harness_claw/gateway/memory.py tests/gateway/test_memory.py
git commit -m "fix: gracefully handle FTS5 syntax errors in search queries"
```

---

### Task 7: Final integration verification

**Files:**
- Test: `tests/gateway/test_memory.py`

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 2: Run a quick manual smoke test**

Run:
```bash
python -c "
import asyncio
from pathlib import Path
from harness_claw.gateway.memory import SqliteMemoryStore

async def smoke():
    store = SqliteMemoryStore(Path('/tmp/test_semantic_memory.db'))
    await store.set('test', 'bug1', 'JWT token validation skips expiry check', summary='auth bug', tags=['auth'])
    await store.set('test', 'feature1', 'add dark mode toggle to settings page', summary='UI feature', tags=['frontend'])
    await store.set('test', 'perf1', 'database query N+1 problem in user listing', summary='performance', tags=['db'])

    results = await store.search('test', 'authentication security issue')
    print('Query: authentication security issue')
    for e in results:
        print(f'  → {e.key}: {e.summary}')

    results = await store.search('test', 'slow page load')
    print('Query: slow page load')
    for e in results:
        print(f'  → {e.key}: {e.summary}')

asyncio.run(smoke())
"
```

Expected: "authentication security issue" should rank `bug1` first. "slow page load" should rank `perf1` first.

- [ ] **Step 3: Clean up temp file**

Run: `rm /tmp/test_semantic_memory.db`

- [ ] **Step 4: Commit any final adjustments**

If any tweaks were needed, commit them. Otherwise, this task is done.
