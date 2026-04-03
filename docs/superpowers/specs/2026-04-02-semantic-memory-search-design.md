# Semantic Memory Search — Design Spec

**Date:** 2026-04-02
**Status:** Approved

## Problem

Agents have memory tools (`memory.set`, `memory.search`, etc.) and system prompts instructing them to use memory proactively. However, the current FTS5 keyword search fails when there's no keyword overlap between query and stored memory (e.g., searching "auth bug" won't find "JWT token validation skips expiry check").

## Solution

Add a local embedding layer to `SqliteMemoryStore` using `sentence-transformers` (`all-mpnet-base-v2`). Hybrid search merges FTS5 keyword results with cosine vector similarity results, giving both exact-match precision and semantic recall.

## Architecture

All changes are contained within `harness_claw/gateway/memory.py`. No new files, no new services, no interface changes.

### Write path (`memory.set`)

1. Store text in SQLite as today (FTS5 index maintained via existing triggers)
2. Embed the `value` (concatenated with `summary` if present) using `all-mpnet-base-v2`
3. Store the 768-dim vector as a BLOB in a new `memory_vectors` table

### Search path (`memory.search`)

1. Run FTS5 keyword search → scored results (normalized to 0–1)
2. Embed the query → cosine similarity against stored vectors → scored results (already 0–1)
3. Merge both result sets with weighted ranking (default: 0.4 FTS5 + 0.6 vector)
4. Return top-k deduplicated results

### Delete path (`memory.delete`)

- Delete from both `memory` and `memory_vectors` tables (CASCADE foreign key handles this)

### Model loading

- Lazy-load on first `set()` or `search()` call, not at server startup
- Single `SentenceTransformer` instance cached for process lifetime
- First load: ~2–3 seconds; subsequent embeds: ~5ms each
- If memory is never used, model is never loaded (no startup penalty)

## Data Model

New table alongside existing schema:

```sql
CREATE TABLE IF NOT EXISTS memory_vectors (
    namespace TEXT NOT NULL,
    key       TEXT NOT NULL,
    embedding BLOB NOT NULL,   -- 768 floats stored as bytes (3072 bytes per entry)
    PRIMARY KEY (namespace, key),
    FOREIGN KEY (namespace, key) REFERENCES memory(namespace, key) ON DELETE CASCADE
);
```

Separate table rationale:
- Keeps FTS5 triggers untouched
- `memory` table stays clean for REST API / dashboard queries
- Vectors can be dropped/rebuilt independently (e.g., model swap)

## Hybrid Search Merge

```python
def hybrid_search(namespace, query, top_k=10, fts_weight=0.4, vec_weight=0.6):
    # 1. FTS5 results → normalize scores to 0–1
    # 2. Vector results → cosine similarity (already 0–1)
    # 3. Union by key, weighted sum of scores
    # 4. Return top_k sorted by combined score
```

Weights are configurable. Default 0.4/0.6 favors semantic since the whole point is catching what FTS5 misses.

## Dependencies

- `sentence-transformers` added to `pyproject.toml` (pulls in `torch`, `transformers`, `numpy`)

## Files Changed

| File | Change |
|---|---|
| `harness_claw/gateway/memory.py` | `memory_vectors` table creation, embedding on set/delete, hybrid search |
| `pyproject.toml` | Add `sentence-transformers` dependency |
| `tests/gateway/test_memory.py` | Tests for vector storage, hybrid search, merge ranking |

## Files NOT Changed

- `mcp_server.py` — `memory.search()` signature unchanged
- `server.py` — no new wiring
- `agents.yaml` — system prompts already updated (separate change)
- Frontend — `MemoryTab` search benefits automatically

## Scale

Designed for hundreds to low thousands of entries. At this scale:
- All vectors fit in memory for brute-force cosine similarity
- No approximate nearest neighbor index needed
- SQLite handles the load without issues
