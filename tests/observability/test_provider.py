from __future__ import annotations

import json

from harness_claw.observability.provider import ClaudeCodeProvider, NullProvider, ObservabilityProvider


def test_null_provider_configure_does_nothing(tmp_path):
    p = NullProvider()
    env: dict = {}
    p.configure_session("s1", str(tmp_path), env)
    assert env == {}


def test_claude_provider_creates_settings_file(tmp_path):
    provider = ClaudeCodeProvider(base_url="http://localhost:8000")
    env: dict = {}
    provider.configure_session("s1", str(tmp_path), env)

    settings_path = tmp_path / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "hooks" in data
    assert "PreToolUse" in data["hooks"]
    assert "PostToolUse" in data["hooks"]


def test_claude_provider_merges_existing_settings(tmp_path):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    settings_path = claude_dir / "settings.json"
    settings_path.write_text(json.dumps({"someOtherSetting": True}))

    provider = ClaudeCodeProvider(base_url="http://localhost:8000")
    provider.configure_session("s1", str(tmp_path), {})

    data = json.loads(settings_path.read_text())
    assert data["someOtherSetting"] is True
    assert "hooks" in data


def test_claude_provider_injects_env_vars(tmp_path):
    provider = ClaudeCodeProvider(base_url="http://localhost:8000")
    env: dict = {}
    provider.configure_session("s1", str(tmp_path), env)
    assert env["HARNESS_SESSION_ID"] == "s1"
    assert env["HARNESS_BASE_URL"] == "http://localhost:8000"


def test_claude_provider_unconfigure_removes_hooks(tmp_path):
    provider = ClaudeCodeProvider(base_url="http://localhost:8000")
    provider.configure_session("s1", str(tmp_path), {})
    provider.unconfigure_session("s1", str(tmp_path))

    settings_path = tmp_path / ".claude" / "settings.json"
    if settings_path.exists():
        data = json.loads(settings_path.read_text())
        hooks = data.get("hooks", {})
        # harnessclaw hooks removed
        for event_hooks in hooks.values():
            for hook_group in event_hooks:
                assert hook_group.get("_harnessclaw") is not True


def test_claude_provider_unconfigure_removes_file_if_empty(tmp_path):
    provider = ClaudeCodeProvider(base_url="http://localhost:8000")
    provider.configure_session("s1", str(tmp_path), {})
    provider.unconfigure_session("s1", str(tmp_path))
    settings_path = tmp_path / ".claude" / "settings.json"
    assert not settings_path.exists()


def test_null_provider_satisfies_protocol():
    assert isinstance(NullProvider(), ObservabilityProvider)


def test_claude_provider_satisfies_protocol():
    assert isinstance(ClaudeCodeProvider(), ObservabilityProvider)
