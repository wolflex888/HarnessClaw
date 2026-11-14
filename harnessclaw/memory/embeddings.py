"""Meaning-based recall through any OpenAI-compatible `/embeddings` endpoint.

Ollama, vLLM, LM Studio and llama.cpp's server all provide one, so embeddings
can stay on your machine. Vectors are cached in `index.db`, keyed by fact id
and content hash, and can always be rebuilt from the Markdown files.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from array import array
from collections.abc import Sequence
from pathlib import Path

import httpx

from harnessclaw.memory.facts import Fact

log = logging.getLogger(__name__)

BATCH = 64
RETRY_AFTER = 60.0  # seconds to wait after the endpoint fails before trying again


# Instruction-tuned embedding models rank much better when the query says what it's for.
_INSTRUCTION = "Given a message from a user, retrieve facts about the user that help answer it"
_INSTRUCTED_MODELS = ("qwen3-embedding", "gte-qwen", "e5-mistral", "multilingual-e5-large-instruct")


def query_prefix(model: str, instruction: str = "auto") -> str:
    """The text put before a query: `instruction` itself, or for "auto" the format
    known instruction-tuned models were trained with (nothing for other models)."""
    if instruction == "auto":
        if not any(name in model.lower() for name in _INSTRUCTED_MODELS):
            return ""
        instruction = _INSTRUCTION
    return f"Instruct: {instruction}\nQuery: " if instruction else ""


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class Embedder:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        query_instruction: str = "auto",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.query_prefix = query_prefix(model, query_instruction)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            timeout=timeout, headers=headers, transport=transport, trust_env=False
        )
        self._failed_at = 0.0
        self.last_error = ""

    @property
    def available(self) -> bool:
        return time.monotonic() - self._failed_at >= RETRY_AFTER

    async def embed(self, texts: Sequence[str]) -> list[list[float]] | None:
        """Vectors for `texts`, or None when the endpoint is unavailable."""
        if not texts:
            return []
        if not self.available:
            return None
        out: list[list[float]] = []
        try:
            for i in range(0, len(texts), BATCH):
                batch = list(texts[i : i + BATCH])
                resp = await self._client.post(
                    f"{self.base_url}/embeddings", json={"model": self.model, "input": batch}
                )
                resp.raise_for_status()
                data = sorted(resp.json()["data"], key=lambda d: d["index"])
                if len(data) != len(batch):
                    raise ValueError("endpoint returned a different number of vectors")
                out.extend(d["embedding"] for d in data)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            self._failed_at = time.monotonic()
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("embeddings unavailable (%s); using keyword recall", self.last_error)
            return None
        self.last_error = ""
        return out

    async def close(self) -> None:
        await self._client.aclose()


class VectorIndex:
    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS vectors ("
            " id TEXT NOT NULL, model TEXT NOT NULL, hash TEXT NOT NULL, vec BLOB NOT NULL,"
            " PRIMARY KEY (id, model))"
        )
        self._conn.commit()

    def load(self, model: str) -> dict[str, tuple[str, list[float]]]:
        rows = self._conn.execute("SELECT id, hash, vec FROM vectors WHERE model = ?", (model,))
        return {fid: (h, array("f", blob).tolist()) for fid, h, blob in rows}

    def put(self, model: str, entries: dict[str, tuple[str, list[float]]]) -> None:
        self._conn.executemany(
            "INSERT OR REPLACE INTO vectors (id, model, hash, vec) VALUES (?, ?, ?, ?)",
            [(fid, model, h, array("f", vec).tobytes()) for fid, (h, vec) in entries.items()],
        )
        self._conn.commit()

    def prune(self, model: str, keep: set[str]) -> None:
        ids = [row[0] for row in self._conn.execute("SELECT id FROM vectors WHERE model = ?", (model,))]
        stale = [(fid, model) for fid in ids if fid not in keep]
        if stale:
            self._conn.executemany("DELETE FROM vectors WHERE id = ? AND model = ?", stale)
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class SemanticIndex:
    """Keeps a vector for every fact and answers `vectors_for(facts)`."""

    def __init__(self, embedder: Embedder, index: VectorIndex) -> None:
        self.embedder = embedder
        self._index = index
        self._cache = index.load(embedder.model)

    async def ensure(self, facts: Sequence[Fact]) -> bool:
        """Embed facts that are new or changed. False when the endpoint is down."""
        missing = [f for f in facts if self._cache.get(f.id, ("",))[0] != content_hash(f.content)]
        if missing:
            vecs = await self.embedder.embed([f.content for f in missing])
            if vecs is None:
                return False
            entries = {f.id: (content_hash(f.content), v) for f, v in zip(missing, vecs)}
            self._index.put(self.embedder.model, entries)
            self._cache.update(entries)
        return True

    def prune(self, keep: set[str]) -> None:
        self._index.prune(self.embedder.model, keep)
        self._cache = {k: v for k, v in self._cache.items() if k in keep}

    def vectors_for(self, facts: Sequence[Fact]) -> dict[str, list[float]]:
        out = {}
        for f in facts:
            entry = self._cache.get(f.id)
            if entry and entry[0] == content_hash(f.content):
                out[f.id] = entry[1]
        return out

    async def query_vector(self, text: str) -> list[float] | None:
        vecs = await self.embedder.embed([self.embedder.query_prefix + text])
        return vecs[0] if vecs else None

    def status(self) -> dict[str, object]:
        return {
            "model": self.embedder.model,
            "base_url": self.embedder.base_url,
            "vectors": len(self._cache),
            "available": self.embedder.available,
            "last_error": self.embedder.last_error,
        }
