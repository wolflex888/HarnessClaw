"""Chat models with tool use: Claude, and anything OpenAI-compatible (Ollama, vLLM, LM Studio…)."""

from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, Protocol

import httpx

from harnessclaw.assistant.messages import Completion, Message, StopReason, ToolCall, ToolSpec
from harnessclaw.config import ModelSpec

_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)

# Claude models that accept server-side refusal fallback (`fallbacks: "default"`).
_FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ModelError(Exception):
    pass


class ChatModel(Protocol):
    spec: ModelSpec

    async def respond(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> Completion: ...


def create_model(spec: ModelSpec) -> ChatModel:
    if spec.provider == "openai":
        return OpenAICompatibleModel(spec)
    if spec.provider == "anthropic":
        return AnthropicModel(spec)
    raise ValueError(f"model {spec.name!r}: unknown provider {spec.provider!r} (use openai or anthropic)")


# ---------------------------------------------------------------------- OpenAI-compatible


class OpenAICompatibleModel:
    def __init__(self, spec: ModelSpec, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not spec.base_url:
            raise ValueError(f"model {spec.name!r} needs a base_url")
        self.spec = spec
        headers = {"Authorization": f"Bearer {spec.api_key}"} if spec.api_key else {}
        self._client = httpx.AsyncClient(timeout=600.0, headers=headers, transport=transport, trust_env=False)

    async def respond(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> Completion:
        body: dict[str, Any] = {
            "model": self.spec.model,
            "messages": [{"role": "system", "content": system}, *to_openai(messages)],
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": {
                    "name": t.name, "description": t.description, "parameters": t.input_schema}}
                for t in tools
            ]
        url = self.spec.base_url.rstrip("/") + "/chat/completions"
        try:
            resp = await self._client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise ModelError(f"could not reach {self.spec.base_url} ({type(exc).__name__})") from exc
        if resp.status_code >= 400:
            raise ModelError(f"{self.spec.model} at {self.spec.base_url}: HTTP {resp.status_code} {resp.text[:300]}")
        try:
            data = resp.json()
            choice = data["choices"][0]
            msg = choice["message"]
        except (ValueError, KeyError, IndexError) as exc:
            raise ModelError(f"unexpected answer from {self.spec.base_url}") from exc

        calls = []
        for raw in msg.get("tool_calls") or []:
            fn = raw.get("function", {})
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    args = {"_invalid_json": args}
            if not isinstance(args, dict):
                args = {"_invalid_json": json.dumps(args)}
            calls.append(ToolCall(id=raw.get("id") or "call_" + uuid.uuid4().hex[:12],
                                  name=fn.get("name", ""), input=args))
        text = _THINK_RE.sub("", msg.get("content") or "").strip()
        finish = choice.get("finish_reason")
        stop: StopReason = "tool_use" if calls else ("max_tokens" if finish == "length" else "end_turn")
        usage = data.get("usage") or {}
        return Completion(
            Message("assistant", text=text, tool_calls=calls), stop,
            input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0),
        )


def to_openai(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.text or None}
            if m.tool_calls:
                entry["tool_calls"] = [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.input, ensure_ascii=False)}}
                    for c in m.tool_calls
                ]
            out.append(entry)
            continue
        for r in m.tool_results:
            content = f"Error: {r.content}" if r.is_error else r.content
            out.append({"role": "tool", "tool_call_id": r.call_id, "content": content})
        if m.text:
            out.append({"role": "user", "content": m.text})
    return out


# ---------------------------------------------------------------------- Anthropic


class AnthropicModel:
    def __init__(self, spec: ModelSpec, client: Any = None) -> None:
        self.spec = spec
        try:
            import anthropic
        except ImportError as exc:
            raise ModelError("Claude needs the anthropic extra: uv tool install 'harnessclaw[anthropic]'") from exc
        self._anthropic = anthropic
        self._client = client
        # A proxy behind a custom base URL may not accept newer request fields.
        self._first_party = not (spec.base_url or os.environ.get("ANTHROPIC_BASE_URL"))

    @property
    def client(self) -> Any:
        if self._client is None:
            kwargs: dict[str, Any] = {}
            if self.spec.api_key:
                kwargs["api_key"] = self.spec.api_key
            if self.spec.base_url:
                kwargs["base_url"] = self.spec.base_url
            # Without an api_key this resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN,
            # or an `ant auth login` profile.
            self._client = self._anthropic.AsyncAnthropic(**kwargs)
        return self._client

    def build_params(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.spec.model,
            "max_tokens": 16000,
            "system": system,
            "messages": to_anthropic(messages),
            # Cache the conversation prefix up to the newest block on every turn.
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools
            ]
        if self.spec.effort:
            params["output_config"] = {"effort": self.spec.effort}
        if self._first_party and self.spec.model in _FALLBACK_MODELS:
            # On a safety decline, retry on Anthropic's recommended fallback model.
            params["betas"] = [_FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    async def respond(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> Completion:
        a = self._anthropic
        params = self.build_params(system, messages, tools)
        try:
            response = await self.client.beta.messages.create(**params)
        except a.AuthenticationError as exc:
            raise ModelError("Anthropic authentication failed; set ANTHROPIC_API_KEY or api_key in config.toml") from exc
        except a.NotFoundError as exc:
            raise ModelError(f"unknown Claude model {self.spec.model!r}") from exc
        except a.RateLimitError as exc:
            raise ModelError("Claude is rate limited; try again shortly") from exc
        except a.APIStatusError as exc:
            raise ModelError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except a.APIConnectionError as exc:
            raise ModelError("could not reach the Anthropic API") from exc
        except TypeError as exc:
            # The SDK raises this when no API key, token or profile is configured.
            if "authentication" not in str(exc).lower():
                raise
            raise ModelError("no Anthropic credentials; set ANTHROPIC_API_KEY or api_key in config.toml") from exc
        return from_anthropic(response)


def to_anthropic(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant":
            if m.native and m.native.get("provider") == "anthropic":
                content = m.native["content"]  # exactly as returned: thinking, fallback blocks…
            else:
                content = ([{"type": "text", "text": m.text}] if m.text else []) + [
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in m.tool_calls
                ]
            out.append({"role": "assistant", "content": content})
            continue
        blocks: list[dict[str, Any]] = [
            {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content,
             **({"is_error": True} if r.is_error else {})}
            for r in m.tool_results
        ]
        if m.text:
            blocks.append({"type": "text", "text": m.text})
        out.append({"role": "user", "content": blocks})
    return out


_ANTHROPIC_STOPS: dict[str, StopReason] = {
    "end_turn": "end_turn", "stop_sequence": "end_turn", "tool_use": "tool_use",
    "max_tokens": "max_tokens", "model_context_window_exceeded": "max_tokens", "refusal": "refusal",
}


def from_anthropic(response: Any) -> Completion:
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    calls = [ToolCall(id=b.id, name=b.name, input=b.input if isinstance(b.input, dict) else {})
             for b in response.content if b.type == "tool_use"]
    native = {
        "provider": "anthropic",
        "content": [
            b.model_dump(exclude_unset=True, mode="json", by_alias=True,
                         exclude=getattr(b, "__api_exclude__", None))
            for b in response.content
        ],
    }
    usage = response.usage
    return Completion(
        Message("assistant", text=text, tool_calls=calls, native=native),
        _ANTHROPIC_STOPS.get(response.stop_reason or "", "other"),
        input_tokens=(usage.input_tokens or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0),
        output_tokens=usage.output_tokens or 0,
    )
