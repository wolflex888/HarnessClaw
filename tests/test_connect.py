import json
from pathlib import Path

from harnessclaw.config import Config
from harnessclaw.connect import add_hook, claude_code_hook, plan_claude_code, project_for


def test_project_is_the_git_root_name(tmp_path: Path):
    (tmp_path / "web" / ".git").mkdir(parents=True)
    (tmp_path / "web" / "src").mkdir()
    assert project_for(str(tmp_path / "web" / "src")) == "web"
    assert project_for(str(tmp_path)) is None


def test_hook_adds_nothing_when_the_server_is_down(config: Config, monkeypatch):
    monkeypatch.setenv("HARNESSCLAW_HOME", str(config.home))
    assert claude_code_hook(json.dumps({"prompt": "hi", "cwd": "/"}), "cloud") == ""
    assert claude_code_hook("not json", "cloud") == ""


def test_hook_returns_context(server: Config, monkeypatch):
    from harnessclaw.client import MemoryClient

    monkeypatch.setenv("HARNESSCLAW_HOME", str(server.home))
    MemoryClient(server.url, server.client("user").token).remember("Partner Mei is vegetarian")
    out = json.loads(claude_code_hook(json.dumps({"prompt": "dinner ideas?", "cwd": "/"}), "cloud"))
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "Partner Mei is vegetarian" in out["hookSpecificOutput"]["additionalContext"]


def test_plan_creates_a_non_private_client(config: Config):
    plan = plan_claude_code(config)
    assert plan.created_client and not plan.client.private
    assert plan.mcp_command[-1] == f"Authorization: Bearer {plan.client.token}"
    assert plan.mcp_command[-3] == f"{config.url}/mcp"


def test_add_hook_is_idempotent_and_keeps_other_settings(tmp_path: Path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"model": "opus", "hooks": {"UserPromptSubmit": [{"hooks": [
        {"type": "command", "command": "other"}]}]}}))
    hook = {"type": "command", "command": "/bin/harnessclaw hook claude-code", "timeout": 5}
    assert add_hook(path, hook)
    assert not add_hook(path, hook)
    assert add_hook(path, {**hook, "timeout": 7})
    data = json.loads(path.read_text())
    assert data["model"] == "opus"
    commands = [h for g in data["hooks"]["UserPromptSubmit"] for h in g["hooks"]]
    assert commands == [{"type": "command", "command": "other"}, {**hook, "timeout": 7}]


def test_config_round_trip(config: Config):
    config.tidy_model = 'qwen3:14b "quoted"'
    config.save()
    again = Config.load(config.home)
    assert again.tidy_model == config.tidy_model
    assert [(c.name, c.private) for c in again.clients] == [("user", True), ("cloud", False)]
    assert (config.path.stat().st_mode & 0o777) == 0o600
