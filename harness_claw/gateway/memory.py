from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class Embedder:
    """Lazy-loading sentence-transformers embedder."""

    MODEL_NAME = "all-mpnet-base-v2"
    DIMS = 768

    def __init__(self) -> None:
        self._model: SentenceTransformer | None = None

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


@dataclass
class MemoryEntry:
    namespace: str
    key: str
    value: str
    summary: str | None
    tags: list[str]
    size_bytes: int
    created_at: str
    updated_at: str


class MemoryStore(Protocol):
    async def set(self, namespace: str, key: str, value: str, summary: str | None, tags: list[str]) -> None: ...
    async def get(self, namespace: str, key: str) -> MemoryEntry | None: ...
    async def list(self, namespace: str) -> list[MemoryEntry]: ...
    async def search(self, namespace: str, query: str) -> list[MemoryEntry]: ...
    async def delete(self, namespace: str, key: str) -> None: ...
    async def namespaces(self) -> list[str]: ...


class SqliteMemoryStore:
    """SQLite-backed memory store with FTS5 full-text search."""

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.row_factory = sqlite3.Row
        self._embedder = Embedder()
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS memory (
                namespace TEXT NOT NULL,
                key       TEXT NOT NULL,
                value     TEXT NOT NULL,
                summary   TEXT,
                tags      TEXT NOT NULL DEFAULT '[]',
                size_bytes INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (namespace, key)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                namespace UNINDEXED,
                key UNINDEXED,
                value,
                summary,
                content='memory',
                content_rowid='rowid'
            );
            CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
                INSERT INTO memory_fts(rowid, namespace, key, value, summary)
                VALUES (new.rowid, new.namespace, new.key, new.value, new.summary);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, namespace, key, value, summary)
                VALUES ('delete', old.rowid, old.namespace, old.key, old.value, old.summary);
            END;
            CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE ON memory BEGIN
                INSERT INTO memory_fts(memory_fts, rowid, namespace, key, value, summary)
                VALUES ('delete', old.rowid, old.namespace, old.key, old.value, old.summary);
                INSERT INTO memory_fts(rowid, namespace, key, value, summary)
                VALUES (new.rowid, new.namespace, new.key, new.value, new.summary);
            END;
            CREATE TABLE IF NOT EXISTS memory_vectors (
                namespace TEXT NOT NULL,
                key       TEXT NOT NULL,
                embedding BLOB NOT NULL,
                PRIMARY KEY (namespace, key),
                FOREIGN KEY (namespace, key) REFERENCES memory(namespace, key) ON DELETE CASCADE
            );
        """)
        self._conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _row_to_entry(self, row: sqlite3.Row) -> MemoryEntry:
        return MemoryEntry(
            namespace=row["namespace"],
            key=row["key"],
            value=row["value"],
            summary=row["summary"],
            tags=json.loads(row["tags"]),
            size_bytes=row["size_bytes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

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

    async def get(self, namespace: str, key: str) -> MemoryEntry | None:
        row = self._conn.execute(
            "SELECT * FROM memory WHERE namespace=? AND key=?", (namespace, key)
        ).fetchone()
        return self._row_to_entry(row) if row else None

    async def list(self, namespace: str) -> list[MemoryEntry]:
        rows = self._conn.execute(
            "SELECT * FROM memory WHERE namespace=? ORDER BY updated_at DESC", (namespace,)
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def search(self, namespace: str, query: str) -> list[MemoryEntry]:
        rows = self._conn.execute(
            """SELECT m.* FROM memory m
               JOIN memory_fts f ON m.rowid = f.rowid
               WHERE f.memory_fts MATCH ? AND m.namespace = ?
               ORDER BY rank""",
            (query, namespace),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    async def delete(self, namespace: str, key: str) -> None:
        self._conn.execute("DELETE FROM memory WHERE namespace=? AND key=?", (namespace, key))
        self._conn.commit()

    async def namespaces(self) -> list[str]:
        rows = self._conn.execute("SELECT DISTINCT namespace FROM memory ORDER BY namespace").fetchall()
        return [r["namespace"] for r in rows]
