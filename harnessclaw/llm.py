"""Chat models the memory server can ask for help (the tidy-up).

`openai` covers anything with an OpenAI-compatible `/chat/completions`
endpoint: Ollama, vLLM, LM Studio, llama.cpp, OpenRouter, OpenAI.
`anthropic` uses the Anthropic SDK (install the `anthropic` extra).
"""

from __future__ import annotations

import re
from typing import Protocol

import httpx

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class ChatModel(Protocol):
    name: str

    async def complete(self, prompt: str) -> str: ...


class LLMError(Exception):
    pass


class OpenAICompatibleChat:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.name = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            timeout=timeout, headers=headers, transport=transport, trust_env=False
        )

    async def complete(self, prompt: str) -> str:
        try:
            resp = await self._client.post(
                f"{self.base_url}/chat/completions",
                json={"model": self.name, "messages": [{"role": "user", "content": prompt}]},
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"].get("content") or ""
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"{self.name} at {self.base_url}: {exc}") from exc
        return _THINK_RE.sub("", text).strip()


class AnthropicChat:
    def __init__(self, model: str = "claude-opus-5-5") -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on install
            raise LLMError("install the anthropic extra: uv tool install 'harnessclaw[anthropic]'") from exc
        self._anthropic = anthropic
        self.name = model
        # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an `ant auth login` profile.
        self._client = anthropic.AsyncAnthropic()

    async def complete(self, prompt: str) -> str:
        anthropic = self._anthropic
        try:
            response = await self._client.beta.messages.create(
                model=self.name,
                max_tokens=16000,
                output_config={"effort": "low"},
                # On a policy decline, retry on Anthropic's recommended fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                messages=[{"role": "user", "content": prompt}],
            )
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Anthropic API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"could not reach the Anthropic API: {exc}") from exc
        if response.stop_reason == "refusal":
            raise LLMError("the model declined the request")
        return "".join(b.text for b in response.content if b.type == "text").strip()


def make_chat(provider: str, model: str, base_url: str = "", api_key: str = "") -> ChatModel | None:
    if provider in ("", "off"):
        return None
    if provider == "openai":
        if not base_url or not model:
            raise ValueError("an openai-compatible model needs base_url and model")
        return OpenAICompatibleChat(base_url, model, api_key)
    if provider == "anthropic":
        return AnthropicChat(model or "claude-opus-5-5")
    raise ValueError(f"unknown provider {provider!r} (use openai, anthropic or off)")
