"""`~/.harnessclaw/config.toml` (or `$HARNESSCLAW_HOME/config.toml`)."""

from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORT = 8765


def default_home() -> Path:
    return Path(os.environ.get("HARNESSCLAW_HOME") or Path.home() / ".harnessclaw").expanduser()


def new_token() -> str:
    return "hc_" + secrets.token_urlsafe(24)


@dataclass
class Client:
    """Something allowed to use the memory server, identified by its token."""

    name: str
    token: str
    # May read private memories. Give this only to clients that use local models.
    private: bool = False


@dataclass
class ModelSpec:
    """A model the assistant can use; pick one in chat with /model <name>."""

    name: str
    provider: str  # "openai" (any OpenAI-compatible endpoint) or "anthropic"
    model: str
    base_url: str = ""
    api_key: str = ""
    # Runs on hardware you control. Only local models see private memories.
    local: bool = False
    effort: str = ""  # Claude only: low | medium | high | xhigh | max


def default_models() -> list[ModelSpec]:
    return [
        ModelSpec(name="local", provider="openai", model="qwen3:14b",
                  base_url="http://127.0.0.1:11434/v1", local=True),
        ModelSpec(name="claude", provider="anthropic", model="claude-opus-5-5"),
    ]


@dataclass
class Config:
    home: Path
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    recall_limit: int = 20
    embeddings_base_url: str = "http://127.0.0.1:11434/v1"
    embeddings_model: str = "qwen3-embedding:0.6b"
    embeddings_api_key: str = ""
    embeddings_query_instruction: str = "auto"
    tidy_provider: str = "off"
    tidy_base_url: str = "http://127.0.0.1:11434/v1"
    tidy_model: str = ""
    tidy_api_key: str = ""
    clients: list[Client] = field(default_factory=list)
    assistant_name: str = "Claw"
    assistant_model: str = "local"
    assistant_instructions: str = ""
    new_conversation_after_hours: float = 6.0
    models: list[ModelSpec] = field(default_factory=default_models)
    discord_bot_token: str = ""
    discord_allowed_users: list[int] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return self.home / "config.toml"

    @property
    def url(self) -> str:
        host = "127.0.0.1" if self.host in ("0.0.0.0", "::", "") else self.host
        return f"http://{host}:{self.port}"

    def client(self, name: str) -> Client | None:
        return next((c for c in self.clients if c.name == name), None)

    def model(self, name: str) -> ModelSpec | None:
        return next((m for m in self.models if m.name == name), None)

    @property
    def discord_token(self) -> str:
        return self.discord_bot_token or os.environ.get("DISCORD_BOT_TOKEN", "")

    def by_token(self, token: str) -> Client | None:
        return next((c for c in self.clients if secrets.compare_digest(c.token, token)), None)

    def add_client(self, name: str, private: bool = False) -> Client:
        if self.client(name) is not None:
            raise ValueError(f"a client named {name!r} already exists")
        client = Client(name=name, token=new_token(), private=private)
        self.clients.append(client)
        return client

    @classmethod
    def load(cls, home: Path | None = None) -> Config:
        home = home or default_home()
        path = home / "config.toml"
        if not path.exists():
            raise FileNotFoundError(f"no config at {path}; run `harnessclaw init` first")
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        server = data.get("server", {})
        recall = data.get("recall", {})
        emb = data.get("embeddings", {})
        tidy = data.get("tidy", {})
        assistant = data.get("assistant", {})
        discord = data.get("discord", {})
        models = [
            ModelSpec(
                name=m["name"], provider=m["provider"], model=m["model"],
                base_url=m.get("base_url", ""), api_key=m.get("api_key", ""),
                local=bool(m.get("local", False)), effort=m.get("effort", ""),
            )
            for m in data.get("models", [])
        ] if "models" in data else default_models()
        return cls(
            home=home,
            host=server.get("host", "127.0.0.1"),
            port=int(server.get("port", DEFAULT_PORT)),
            recall_limit=int(recall.get("limit", 20)),
            embeddings_base_url=emb.get("base_url", ""),
            embeddings_model=emb.get("model", ""),
            embeddings_api_key=emb.get("api_key", ""),
            embeddings_query_instruction=emb.get("query_instruction", "auto"),
            tidy_provider=tidy.get("provider", "off"),
            tidy_base_url=tidy.get("base_url", ""),
            tidy_model=tidy.get("model", ""),
            tidy_api_key=tidy.get("api_key", ""),
            clients=[
                Client(name=c["name"], token=c["token"], private=bool(c.get("private", False)))
                for c in data.get("clients", [])
            ],
            assistant_name=assistant.get("name", "Claw"),
            assistant_model=assistant.get("model", "local"),
            assistant_instructions=assistant.get("instructions", ""),
            new_conversation_after_hours=float(assistant.get("new_conversation_after_hours", 6)),
            models=models,
            discord_bot_token=discord.get("bot_token", ""),
            discord_allowed_users=[int(u) for u in discord.get("allowed_users", [])],
        )

    def save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        q = _quote
        lines = [
            "# HarnessClaw memory server",
            "",
            "[server]",
            "# 127.0.0.1 keeps it on this machine. To reach it from other devices, use",
            "# your Tailscale/WireGuard address rather than 0.0.0.0 on an open network.",
            f"host = {q(self.host)}",
            f"port = {self.port}",
            "",
            "[recall]",
            "# Most facts added to a prompt before each message.",
            f"limit = {self.recall_limit}",
            "",
            "[embeddings]",
            "# Any OpenAI-compatible /embeddings endpoint (Ollama, vLLM, LM Studio, OpenAI).",
            '# Set base_url = "" to use keyword recall only.',
            f"base_url = {q(self.embeddings_base_url)}",
            f"model = {q(self.embeddings_model)}",
            f"api_key = {q(self.embeddings_api_key)}",
            '# Text telling an instruction-tuned embedding model what a query is for.',
            '# "auto" uses the right format for models known to need it; "" sends none.',
            f"query_instruction = {q(self.embeddings_query_instruction)}",
            "",
            "[tidy]",
            '# provider: "openai" (any OpenAI-compatible endpoint, e.g. Ollama), "anthropic", or "off".',
            "# The model proposes merges and drops; the server checks each one before applying it.",
            f"provider = {q(self.tidy_provider)}",
            f"base_url = {q(self.tidy_base_url)}",
            f"model = {q(self.tidy_model)}",
            f"api_key = {q(self.tidy_api_key)}",
            "",
            "[assistant]",
            f"name = {q(self.assistant_name)}",
            "# Default model: one of the [[models]] below. Switch in chat with /model <name>.",
            f"model = {q(self.assistant_model)}",
            "# Anything you'd like it to know about how to talk to you.",
            f"instructions = {q(self.assistant_instructions)}",
            "# A chat starts a fresh conversation after this long without messages.",
            "# Memory carries over; only the back-and-forth is cleared.",
            f"new_conversation_after_hours = {self.new_conversation_after_hours:g}",
            "",
            "[discord]",
            "# From the Discord Developer Portal (Bot → Reset Token). Or leave empty and set DISCORD_BOT_TOKEN.",
            f"bot_token = {q(self.discord_bot_token)}",
            "# Discord user ids allowed to talk to the assistant (`harnessclaw discord allow <id>`).",
            f"allowed_users = [{', '.join(str(u) for u in self.discord_allowed_users)}]",
        ]
        for m in self.models:
            lines += [
                "",
                "[[models]]",
                f"name = {q(m.name)}",
                f'provider = {q(m.provider)}  # "openai" = any OpenAI-compatible endpoint, or "anthropic"',
                f"model = {q(m.model)}",
                f"base_url = {q(m.base_url)}",
                f"api_key = {q(m.api_key)}",
                f"local = {'true' if m.local else 'false'}  # true: runs on your hardware and may see private memories",
            ]
            if m.effort:
                lines.append(f"effort = {q(m.effort)}")
        for c in self.clients:
            lines += [
                "",
                "[[clients]]",
                f"name = {q(c.name)}",
                f"token = {q(c.token)}",
                f"private = {'true' if c.private else 'false'}",
            ]
        tmp = self.path.with_suffix(".toml.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        os.replace(tmp, self.path)


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class ConfigWatcher:
    """The config, re-read whenever config.toml changes on disk (new clients, allowed users)."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._mtime = self._stat()

    def _stat(self) -> int:
        try:
            return self._config.path.stat().st_mtime_ns
        except OSError:
            return 0

    @property
    def current(self) -> Config:
        mtime = self._stat()
        if mtime and mtime != self._mtime:
            try:
                self._config = Config.load(self._config.home)
                self._mtime = mtime
            except (OSError, ValueError, KeyError):
                pass  # keep the last good config while the file is being edited
        return self._config

