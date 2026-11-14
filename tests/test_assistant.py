from pathlib import Path

import pytest

from harnessclaw.assistant.agent import Assistant
from harnessclaw.assistant.conversations import ConversationStore
from harnessclaw.assistant.messages import Completion, Message, ToolCall
from harnessclaw.assistant.models import ModelError
from harnessclaw.config import Config, ModelSpec
from harnessclaw.memory.service import MemoryService
from harnessclaw.memory.store import MemoryStore


class ScriptedModel:
    """Returns queued completions and records every request."""

    def __init__(self, spec: ModelSpec, script: list) -> None:
        self.spec = spec
        self.script = script
        self.requests: list[tuple[str, list[Message]]] = []

    async def respond(self, system, messages, tools):
        self.requests.append((system, [Message.from_dict(m.to_dict()) for m in messages]))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def reply(text="", calls=(), stop=None):
    calls = list(calls)
    return Completion(Message("assistant", text=text, tool_calls=calls), stop or ("tool_use" if calls else "end_turn"))


@pytest.fixture
def setup(tmp_path: Path):
    config = Config(home=tmp_path, embeddings_base_url="", embeddings_model="", models=[
        ModelSpec("local", "openai", "qwen3:14b", base_url="http://x/v1", local=True),
        ModelSpec("claude", "anthropic", "claude-opus-5-5"),
    ])
    memory = MemoryService(MemoryStore(tmp_path))
    models: dict[str, ScriptedModel] = {}

    def factory(spec):
        models.setdefault(spec.name, ScriptedModel(spec, []))
        return models[spec.name]

    for name in ("local", "claude"):
        factory(config.model(name))
    assistant = Assistant(lambda: config, memory, ConversationStore(tmp_path / "conv"), model_factory=factory)
    return assistant, memory, models, config


async def test_reply_includes_memory_and_time(setup):
    assistant, memory, models, _ = setup
    await memory.remember("Partner Mei is vegetarian", by="user")
    models["local"].script = [reply("How about a lentil curry?")]
    assert await assistant.handle("t-1", "dinner ideas?", channel="test") == "How about a lentil curry?"
    system, messages = models["local"].requests[0]
    assert "personal assistant" in system
    assert "Partner Mei is vegetarian" in messages[0].text and "Now: " in messages[0].text
    assert messages[0].text.endswith("dinner ideas?")


async def test_tool_calls_write_memory_with_provenance(setup):
    assistant, memory, models, _ = setup
    models["local"].script = [
        reply("Noted!", [ToolCall("c1", "remember", {"content": "Learning the cello", "category": "hobby"})]),
        reply("I'll remember that."),
    ]
    out = await assistant.handle("t-1", "I started learning the cello", channel="discord")
    assert out == "Noted!\n\nI'll remember that."
    fact = memory.store.facts()[0]
    assert (fact.content, fact.by, fact.model) == ("Learning the cello", "assistant/discord", "qwen3:14b")
    _, messages = models["local"].requests[1]
    assert messages[-1].tool_results[0].content.startswith("Remembered [m_")


async def test_invented_replaces_id_still_saves(setup):
    assistant, memory, models, _ = setup
    models["local"].script = [
        reply(calls=[ToolCall("c1", "remember", {"content": "Learning the cello", "replaces": "m_123456"})]),
        reply("Saved."),
    ]
    await assistant.handle("t-1", "remember I'm learning cello", channel="test")
    assert [f.content for f in memory.store.facts()] == ["Learning the cello"]
    assert "saved as a new one" in models["local"].requests[1][1][-1].tool_results[0].content


async def test_cloud_models_never_see_private_facts(setup):
    assistant, memory, models, _ = setup
    await memory.remember("Allergic to penicillin", private=True, by="user")
    await assistant.handle("t-1", "/model claude", channel="test")
    models["claude"].script = [
        reply(calls=[ToolCall("c1", "recall", {"query": "allergies"})]),
        reply("Nothing on file."),
    ]
    await assistant.handle("t-1", "any allergies I should mention?", channel="test")
    for _, messages in models["claude"].requests:
        assert all("penicillin" not in (m.text + "".join(r.content for r in m.tool_results)) for m in messages)


async def test_switching_to_cloud_after_private_context_starts_fresh(setup):
    assistant, memory, models, _ = setup
    await memory.remember("Allergic to penicillin", private=True, by="user")
    models["local"].script = [reply("Noted, you're allergic to penicillin.")]
    await assistant.handle("t-1", "what am I allergic to?", channel="test")
    out = await assistant.handle("t-1", "/model claude", channel="test")
    assert "started a fresh one" in out
    models["claude"].script = [reply("Hi!")]
    await assistant.handle("t-1", "hello", channel="test")
    _, messages = models["claude"].requests[0]
    assert len(messages) == 1 and "penicillin" not in messages[0].text


async def test_model_failure_leaves_the_conversation_unchanged(setup):
    assistant, _, models, _ = setup
    models["local"].script = [reply("one"), ModelError("connection refused")]
    await assistant.handle("t-1", "first", channel="test")
    out = await assistant.handle("t-1", "second", channel="test")
    assert "couldn't reach local" in out and "/model" in out
    assert len(assistant.store.load("t-1").messages) == 2


async def test_refusal_is_reported_and_not_kept(setup):
    assistant, _, models, _ = setup
    models["local"].script = [Completion(Message("assistant", text=""), "refusal")]
    assert "declined" in await assistant.handle("t-1", "hmm", channel="test")
    assert [m.role for m in assistant.store.load("t-1").messages] == ["user"]


async def test_truncated_tool_calls_are_not_run(setup):
    assistant, memory, models, _ = setup
    models["local"].script = [reply("Saving", [ToolCall("c1", "remember", {"content": "Half a fa"})], stop="max_tokens")]
    out = await assistant.handle("t-1", "x", channel="test")
    assert "cut off" in out and memory.store.facts() == []
    last = assistant.store.load("t-1").messages[-1]
    assert last.tool_results[0].is_error


async def test_commands(setup):
    assistant, _, models, _ = setup
    assert "/model" in await assistant.handle("t-1", "/help", channel="test")
    listing = await assistant.handle("t-1", "/model", channel="test")
    assert "→ local: qwen3:14b (local)" in listing and "claude: claude-opus-5-5 (cloud)" in listing
    assert "No model named" in await assistant.handle("t-1", "/model gpt", channel="test")
    assert (await assistant.handle("t-1", "/model@MyBot claude", channel="test")).startswith("Now using claude")
    models["claude"].script = [reply("a")]
    await assistant.handle("t-1", "hi", channel="test")
    assert "fresh" in await assistant.handle("t-1", "/new", channel="test")
    assert assistant.store.load("t-1").messages == []
    assert assistant.store.load("t-1").model == "claude"


async def test_idle_conversations_start_fresh(setup):
    assistant, _, models, config = setup
    models["local"].script = [reply("a"), reply("b")]
    await assistant.handle("t-1", "first", channel="test")
    conv = assistant.store.load("t-1")
    conv.updated_at = "2020-01-01T00:00:00+00:00"
    (assistant.store.root / "t-1.json").write_text(__import__("json").dumps(conv.to_dict()))
    await assistant.handle("t-1", "second", channel="test")
    assert len(models["local"].requests[1][1]) == 1
