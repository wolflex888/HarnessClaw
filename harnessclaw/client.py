"""A small client for the memory server's JSON API (used by the CLI and hooks)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from harnessclaw.config import Config, default_home


class ServerError(Exception):
    pass


class MemoryClient:
    def __init__(self, url: str, token: str, timeout: float = 30.0) -> None:
        self.url = url.rstrip("/")
        self._http = httpx.Client(
            timeout=timeout, headers={"Authorization": f"Bearer {token}"}, trust_env=False
        )

    @classmethod
    def from_env(cls, client_name: str = "user", home: Path | None = None, timeout: float = 30.0) -> MemoryClient:
        """`HARNESSCLAW_URL` + `HARNESSCLAW_TOKEN` when set (a server on another machine),
        otherwise the local config and the named client's token."""
        url = os.environ.get("HARNESSCLAW_URL")
        token = os.environ.get("HARNESSCLAW_TOKEN")
        if not (url and token):
            config = Config.load(home or default_home())
            client = config.client(client_name)
            if client is None:
                raise ServerError(f"no client named {client_name!r} in {config.path}")
            url, token = url or config.url, token or client.token
        return cls(url, token, timeout=timeout)

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = self._http.request(method, self.url + path, **kwargs)
        except httpx.HTTPError as exc:
            raise ServerError(
                f"could not reach the memory server at {self.url} ({exc}); is `harnessclaw serve` running?"
            ) from exc
        try:
            data = resp.json()
        except ValueError:
            data = {"error": resp.text}
        if resp.status_code >= 400:
            raise ServerError(data.get("error") or f"HTTP {resp.status_code}")
        return data

    def status(self) -> dict[str, Any]:
        return self._call("GET", "/v1/status")

    def recall(self, query: str, project: str | None = None, limit: int | None = None) -> dict[str, Any]:
        return self._call("POST", "/v1/recall", json={"query": query, "project": project, "limit": limit})

    def remember(self, content: str, **fields: Any) -> dict[str, Any]:
        return self._call("POST", "/v1/remember", json={"content": content, **fields})

    def forget(self, fact_id: str) -> dict[str, Any]:
        return self._call("POST", "/v1/forget", json={"id": fact_id})

    def facts(self, project: str | None = None, all_projects: bool = False) -> list[dict[str, Any]]:
        params = {"all": "1"} if all_projects else ({"project": project} if project else {})
        return self._call("GET", "/v1/facts", params=params)["facts"]

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._call("GET", "/v1/history", params={"limit": limit})["changes"]

    def undo(self, change_id: str) -> dict[str, Any]:
        return self._call("POST", "/v1/undo", json={"id": change_id})

    def tidy(self, dry_run: bool = False) -> dict[str, Any]:
        return self._call("POST", "/v1/tidy", json={"dry_run": dry_run})

    def chat(self, text: str) -> str:
        return self._call("POST", "/v1/chat", json={"text": text})["reply"]
