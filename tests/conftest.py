from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from harnessclaw.config import Config
from harnessclaw.memory.facts import Fact
from harnessclaw.memory.store import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(tmp_path)


def make_fact(content: str, *, id: str = "", by: str = "claude-code", at: str = "2026-11-01T00:00:00Z",
              category: str = "general") -> Fact:
    import uuid

    return Fact(id=id or "m_" + uuid.uuid4().hex[:8], content=content, by=by, at=at, category=category)


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config(home=tmp_path, port=_free_port(), embeddings_base_url="", embeddings_model="")
    cfg.add_client("user", private=True)
    cfg.add_client("cloud", private=False)
    cfg.save()
    return cfg


@pytest.fixture
def server(config: Config) -> Iterator[Config]:
    """A real memory server on a free port, for the length of one test."""
    from harnessclaw.server import create_app

    srv = uvicorn.Server(uvicorn.Config(create_app(config), host="127.0.0.1", port=config.port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        if time.monotonic() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    yield config
    srv.should_exit = True
    thread.join(timeout=10)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
