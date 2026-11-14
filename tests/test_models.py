import json

import anthropic
import httpx

from harnessclaw.assistant.messages import Message, ToolCall, ToolResult, ToolSpec
from harnessclaw.assistant.models import AnthropicModel, OpenAICompatibleModel, from_anthropic, to_anthropic
from harnessclaw.config import ModelSpec

TOOLS = [ToolSpec("remember", "save", {"type": "object", "properties": {}})]
HISTORY = [
    Message("user", text="hi"),
    Message("assistant", text="Saving", tool_calls=[ToolCall("c1", "remember", {"content": "x"})]),
    Message("user", tool_results=[ToolResult("c1", "Remembered [m_1].")]),
]


async def test_openai_request_and_tool_call_parsing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "content": "<think>hmm</think>On it.",
            "tool_calls": [{"id": "x1", "type": "function",
                            "function": {"name": "recall", "arguments": '{"query": "food"}'}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3}})

    spec = ModelSpec("local", "openai", "qwen3:14b", base_url="http://ollama/v1")
    out = await OpenAICompatibleModel(spec, transport=httpx.MockTransport(handler)).respond("SYS", HISTORY, TOOLS)
    assert out.stop_reason == "tool_use" and out.message.text == "On it."
    assert out.message.tool_calls == [ToolCall("x1", "recall", {"query": "food"})]
    assert seen["messages"][0] == {"role": "system", "content": "SYS"}
    assert seen["messages"][2]["tool_calls"][0]["function"] == {"name": "remember", "arguments": '{"content": "x"}'}
    assert seen["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": "Remembered [m_1]."}
    assert seen["tools"][0]["function"]["name"] == "remember"


async def test_openai_bad_arguments_are_flagged_not_crashing():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "tool_calls", "message": {
            "content": "", "tool_calls": [{"id": "x", "function": {"name": "recall", "arguments": "{oops"}}]}}]})

    spec = ModelSpec("l", "openai", "m", base_url="http://o/v1")
    out = await OpenAICompatibleModel(spec, transport=httpx.MockTransport(handler)).respond("s", HISTORY[:1], TOOLS)
    assert out.message.tool_calls[0].input == {"_invalid_json": "{oops"}


def test_anthropic_params_and_replay():
    model = AnthropicModel(ModelSpec("claude", "anthropic", "claude-opus-5-5"), client=object())
    native = Message("assistant", text="ok", native={"provider": "anthropic", "content": [
        {"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": "ok"}]})
    params = model.build_params("SYS", [*HISTORY, native, Message("user", text="next")], TOOLS)
    assert params["fallbacks"] == "default" and params["betas"] == ["server-side-fallback-2026-07-01"]
    assert params["cache_control"] == {"type": "ephemeral"} and "thinking" not in params
    msgs = params["messages"]
    assert msgs[1]["content"][1] == {"type": "tool_use", "id": "c1", "name": "remember", "input": {"content": "x"}}
    assert msgs[2]["content"] == [{"type": "tool_result", "tool_use_id": "c1", "content": "Remembered [m_1]."}]
    assert msgs[3]["content"][0]["signature"] == "sig"  # replayed exactly


def test_haiku_gets_no_fallbacks():
    model = AnthropicModel(ModelSpec("h", "anthropic", "claude-haiku-5-5"), client=object())
    assert "fallbacks" not in model.build_params("s", HISTORY[:1], [])


def test_from_anthropic_keeps_native_blocks():
    resp = anthropic.types.beta.BetaMessage.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "stop_reason": "tool_use", "stop_sequence": None,
        "usage": {"input_tokens": 5, "output_tokens": 7},
        "content": [
            {"type": "thinking", "thinking": "", "signature": "abc"},
            {"type": "text", "text": "Let me save that."},
            {"type": "tool_use", "id": "toolu_1", "name": "remember", "input": {"content": "x"}},
        ],
    })
    out = from_anthropic(resp)
    assert out.stop_reason == "tool_use" and out.message.text == "Let me save that."
    assert out.message.tool_calls == [ToolCall("toolu_1", "remember", {"content": "x"})]
    assert [b["type"] for b in out.message.native["content"]] == ["thinking", "text", "tool_use"]
    assert to_anthropic([out.message])[0]["content"] == out.message.native["content"]
