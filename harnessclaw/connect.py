"""Connecting harnesses: the MCP server for tools, a hook for automatic recall."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harnessclaw.client import MemoryClient
from harnessclaw.config import Client, Config

MCP_NAME = "harnessclaw-memory"
HOOK_TIMEOUT = 5  # seconds Claude Code waits for the hook
RECALL_TIMEOUT = 3.0  # seconds the hook waits for the server


def project_for(cwd: str | None) -> str | None:
    """The git repository's directory name, or None outside a repository."""
    if not cwd:
        return None
    path = Path(cwd).resolve()
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate.name
    return None


def claude_code_hook(stdin_text: str, client_name: str = "claude-code") -> str:
    """Handle Claude Code's UserPromptSubmit hook: the memory context for this prompt,
    as hook JSON. Returns "" (adds nothing) on any problem, so a prompt is never blocked."""
    try:
        event = json.loads(stdin_text or "{}")
        prompt = str(event.get("prompt") or "")
        if not prompt.strip():
            return ""
        client = MemoryClient.from_env(client_name, timeout=RECALL_TIMEOUT)
        context = client.recall(prompt, project=project_for(event.get("cwd")))["context"]
    except Exception:  # noqa: BLE001 - memory must never get in the way of a prompt
        return ""
    if not context:
        return ""
    return json.dumps({
        "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}
    })


def harnessclaw_command() -> str:
    found = shutil.which("harnessclaw")
    return found or f"{sys.executable} -m harnessclaw"


@dataclass
class Plan:
    client: Client
    created_client: bool
    mcp_command: list[str]
    settings_path: Path
    hook: dict[str, Any]
    notes: list[str] = field(default_factory=list)


def plan_claude_code(config: Config, client_name: str = "claude-code") -> Plan:
    client = config.client(client_name)
    created = client is None
    if client is None:
        # Claude Code uses cloud models, so it does not get private memories.
        client = config.add_client(client_name, private=False)
    mcp_command = [
        "claude", "mcp", "add", "--scope", "user", "--transport", "http", MCP_NAME,
        f"{config.url}/mcp", "--header", f"Authorization: Bearer {client.token}",
    ]
    command = f"{harnessclaw_command()} hook claude-code"
    if client_name != "claude-code":
        command += f" --client {shlex.quote(client_name)}"
    hook = {"type": "command", "command": command, "timeout": HOOK_TIMEOUT}
    return Plan(client, created, mcp_command, Path.home() / ".claude" / "settings.json", hook)


def apply_claude_code(config: Config, plan: Plan) -> list[str]:
    done: list[str] = []
    if plan.created_client:
        config.save()
        done.append(f"added client {plan.client.name!r} to {config.path}")

    if shutil.which("claude") is None:
        raise RuntimeError("the `claude` command was not found; install Claude Code first")
    subprocess.run(["claude", "mcp", "remove", "--scope", "user", MCP_NAME], capture_output=True)
    result = subprocess.run(plan.mcp_command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"`claude mcp add` failed: {result.stderr.strip() or result.stdout.strip()}")
    done.append(f"added MCP server {MCP_NAME!r} to Claude Code (user scope)")

    if add_hook(plan.settings_path, plan.hook):
        done.append(f"added the recall hook to {plan.settings_path}")
    else:
        done.append(f"the recall hook was already in {plan.settings_path}")
    return done


def add_hook(settings_path: Path, hook: dict[str, Any]) -> bool:
    """Add (or update) the UserPromptSubmit hook. False when it was already there as is."""
    settings: dict[str, Any] = {}
    if settings_path.exists():
        settings = json.loads(settings_path.read_text() or "{}")
    groups = settings.setdefault("hooks", {}).setdefault("UserPromptSubmit", [])
    for group in groups:
        for existing in group.get("hooks", []):
            if "hook claude-code" in str(existing.get("command", "")) and "harnessclaw" in str(
                existing.get("command", "")
            ):
                if existing == hook:
                    return False
                existing.clear()
                existing.update(hook)
                _write_json(settings_path, settings)
                return True
    groups.append({"hooks": [hook]})
    _write_json(settings_path, settings)
    return True


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)
