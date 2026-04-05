from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol, runtime_checkable


_PRE_HOOK_CMD = (
    "[ -z \"$HARNESS_SESSION_ID\" ] && exit 0; "
    "jq --arg sid \"$HARNESS_SESSION_ID\" "
    "'{harness_session_id: $sid, event: \"tool_start\", tool_name: .tool_name}' "
    "| curl -s -X POST \"$HARNESS_BASE_URL/agent/tool-event\" "
    "-H 'Content-Type: application/json' -d @-"
)

_POST_HOOK_CMD = (
    "[ -z \"$HARNESS_SESSION_ID\" ] && exit 0; "
    "jq --arg sid \"$HARNESS_SESSION_ID\" "
    "'{harness_session_id: $sid, event: \"tool_end\", tool_name: .tool_name}' "
    "| curl -s -X POST \"$HARNESS_BASE_URL/agent/tool-event\" "
    "-H 'Content-Type: application/json' -d @-"
)

_HOOK_MARKER = "_harnessclaw"


@runtime_checkable
class ObservabilityProvider(Protocol):
    def configure_session(
        self, session_id: str, working_dir: str, extra_env: dict[str, str],
        allowed_tools: list[str] | None = None,
    ) -> None: ...

    def unconfigure_session(self, session_id: str, working_dir: str) -> None: ...


class NullProvider:
    def configure_session(
        self, session_id: str, working_dir: str, extra_env: dict[str, str],
        allowed_tools: list[str] | None = None,
    ) -> None:
        pass

    def unconfigure_session(self, session_id: str, working_dir: str) -> None:
        pass


class ClaudeCodeProvider:
    def __init__(self, base_url: str = "http://localhost:8000") -> None:
        self._base_url = base_url

    def _settings_path(self, working_dir: str) -> Path:
        return Path(working_dir).expanduser() / ".claude" / "settings.json"

    def _read_settings(self, path: Path) -> dict:
        if path.exists():
            try:
                return json.loads(path.read_text())
            except json.JSONDecodeError:
                pass  # corrupted file — treat as empty, overwrite below
        return {}

    def _write_settings(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    def configure_session(
        self, session_id: str, working_dir: str, extra_env: dict[str, str],
        allowed_tools: list[str] | None = None,
    ) -> None:
        extra_env["HARNESS_SESSION_ID"] = session_id
        extra_env["HARNESS_BASE_URL"] = self._base_url

        path = self._settings_path(working_dir)
        data = self._read_settings(path)

        hooks = data.setdefault("hooks", {})

        pre_hooks = hooks.setdefault("PreToolUse", [])
        if not any(h.get(_HOOK_MARKER) for h in pre_hooks):
            pre_hooks.append({
                _HOOK_MARKER: True,
                "matcher": ".*",
                "hooks": [{"type": "command", "command": _PRE_HOOK_CMD}],
            })

        post_hooks = hooks.setdefault("PostToolUse", [])
        if not any(h.get(_HOOK_MARKER) for h in post_hooks):
            post_hooks.append({
                _HOOK_MARKER: True,
                "matcher": ".*",
                "hooks": [{"type": "command", "command": _POST_HOOK_CMD}],
            })

        if allowed_tools is not None:
            data["permissions"] = {
                "allow": list(allowed_tools),
                "deny": ["*"],
            }

        self._write_settings(path, data)

    def unconfigure_session(self, session_id: str, working_dir: str) -> None:
        path = self._settings_path(working_dir)
        if not path.exists():
            return

        data = self._read_settings(path)
        hooks = data.get("hooks", {})

        for event in ("PreToolUse", "PostToolUse"):
            hooks[event] = [h for h in hooks.get(event, []) if not h.get(_HOOK_MARKER)]
            if not hooks[event]:
                hooks.pop(event, None)

        if not hooks:
            data.pop("hooks", None)

        data.pop("permissions", None)

        if not data:
            path.unlink(missing_ok=True)
        else:
            self._write_settings(path, data)
