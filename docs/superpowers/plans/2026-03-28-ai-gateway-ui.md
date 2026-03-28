# AI Gateway UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a locally-run multi-agent dashboard with a FastAPI backend and React/Vite frontend supporting real-time chat, long-running jobs, orchestrator agents, and live per-session cost tracking.

**Architecture:** Single FastAPI process with a WebSocket endpoint for streaming and REST endpoints for agent management. A `BaseProvider` abstraction (Anthropic first) powers agents. An asyncio `JobRunner` manages streaming tasks and nested orchestrator/sub-agent loops. React SPA communicates over WebSocket and REST, routing streamed tokens by `job_id`.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, anthropic SDK, PyYAML, pydantic, pytest, pytest-asyncio, httpx; React 18, TypeScript, Vite, Tailwind CSS.

---

## File Map

```
harness_claw/
├── pricing.py               CREATE  — PRICING dict, get_cost()
├── session.py               CREATE  — Session dataclass with cost property
├── agent_registry.py        CREATE  — AgentConfig model, AgentRegistry
├── job_runner.py            CREATE  — JobRunner (asyncio tasks, orchestrator loop)
├── server.py                CREATE  — FastAPI app, /ws WebSocket, /api/* REST
└── providers/
    ├── __init__.py          CREATE  — empty
    ├── base.py              CREATE  — BaseProvider (stream_chat, stream_with_tools)
    └── anthropic.py         CREATE  — AnthropicProvider implementation

agents.yaml                  CREATE  — sample agent config
requirements.txt             UPDATE  — add all deps

tests/
├── __init__.py              CREATE  — empty
├── test_pricing.py          CREATE  — unit tests
├── test_session.py          CREATE  — unit tests
└── test_job_runner.py       CREATE  — integration test with MockProvider

ui/
├── index.html               CREATE
├── package.json             CREATE
├── vite.config.ts           CREATE
├── tsconfig.json            CREATE
├── tailwind.config.js       CREATE
├── postcss.config.js        CREATE
└── src/
    ├── main.tsx             CREATE
    ├── index.css            CREATE
    ├── types.ts             CREATE
    ├── ws.ts                CREATE
    ├── App.tsx              CREATE
    └── components/
        ├── AgentSidebar.tsx    CREATE
        ├── ChatPanel.tsx       CREATE
        ├── JobsPanel.tsx       CREATE
        ├── SessionCostBar.tsx  CREATE
        └── AgentConfigPanel.tsx CREATE
```

---

## Task 1: Project Setup

**Files:**
- Update: `requirements.txt`
- Create: `tests/__init__.py`

- [ ] **Step 1: Write requirements.txt**

```
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
pydantic>=2.7.0
pyyaml>=6.0.1
anthropic>=0.30.0
pytest>=8.2.0
pytest-asyncio>=0.23.0
httpx>=0.27.0
```

- [ ] **Step 2: Create tests/__init__.py**

Create an empty file at `tests/__init__.py`.

- [ ] **Step 3: Install dependencies**

```bash
pip install -r requirements.txt
```

Expected: All packages install without errors.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt tests/__init__.py
git commit -m "chore: add backend dependencies and tests package"
```

---

## Task 2: pricing.py (TDD)

**Files:**
- Create: `harness_claw/pricing.py`
- Create: `tests/test_pricing.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_pricing.py`:

```python
import pytest
from harness_claw.pricing import get_cost, PRICING


def test_pricing_dict_has_required_models():
    assert "claude-sonnet-4-6" in PRICING
    assert "claude-haiku-4-5-20251001" in PRICING
    assert "claude-opus-4-6" in PRICING


def test_get_cost_sonnet():
    # claude-sonnet-4-6: $3/M input, $15/M output
    cost = get_cost("claude-sonnet-4-6", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(18.00)


def test_get_cost_zero_tokens():
    cost = get_cost("claude-sonnet-4-6", input_tokens=0, output_tokens=0)
    assert cost == 0.0


def test_get_cost_unknown_model():
    cost = get_cost("unknown-model", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 0.0


def test_get_cost_partial():
    # 500k input tokens at $3/M = $1.50
    cost = get_cost("claude-sonnet-4-6", input_tokens=500_000, output_tokens=0)
    assert cost == pytest.approx(1.50)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_pricing.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` — `pricing` not yet defined.

- [ ] **Step 3: Implement pricing.py**

Create `harness_claw/pricing.py`:

```python
from __future__ import annotations

# (input_price_per_million, output_price_per_million) in USD
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-6":          (15.00, 75.00),
    "claude-sonnet-4-6":        (3.00,  15.00),
    "claude-haiku-4-5-20251001": (0.80,   4.00),
}


def get_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    if model not in PRICING:
        return 0.0
    input_price, output_price = PRICING[model]
    return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_pricing.py -v
```

Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/pricing.py tests/test_pricing.py
git commit -m "feat: add pricing module with per-model cost calculation"
```

---

## Task 3: session.py (TDD)

**Files:**
- Create: `harness_claw/session.py`
- Create: `tests/test_session.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_session.py`:

```python
import pytest
from harness_claw.session import Session


def test_session_initial_cost_is_zero():
    s = Session(agent_id="a1", model="claude-sonnet-4-6")
    assert s.cost_usd == 0.0


def test_session_cost_updates_with_tokens():
    s = Session(agent_id="a1", model="claude-sonnet-4-6")
    s.input_tokens = 1_000_000
    s.output_tokens = 1_000_000
    assert s.cost_usd == pytest.approx(18.00)


def test_session_add_user_message():
    s = Session(agent_id="a1", model="claude-sonnet-4-6")
    s.add_user_message("Hello")
    assert s.messages == [{"role": "user", "content": "Hello"}]


def test_session_add_assistant_message():
    s = Session(agent_id="a1", model="claude-sonnet-4-6")
    s.add_assistant_message("Hi there")
    assert s.messages == [{"role": "assistant", "content": "Hi there"}]


def test_session_preserves_message_order():
    s = Session(agent_id="a1", model="claude-sonnet-4-6")
    s.add_user_message("Q1")
    s.add_assistant_message("A1")
    s.add_user_message("Q2")
    assert [m["role"] for m in s.messages] == ["user", "assistant", "user"]


def test_session_has_unique_id():
    s1 = Session(agent_id="a1", model="claude-sonnet-4-6")
    s2 = Session(agent_id="a1", model="claude-sonnet-4-6")
    assert s1.session_id != s2.session_id


def test_session_unknown_model_cost_is_zero():
    s = Session(agent_id="a1", model="gpt-99")
    s.input_tokens = 1_000_000
    assert s.cost_usd == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_session.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement session.py**

Create `harness_claw/session.py`:

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from harness_claw.pricing import get_cost


@dataclass
class Session:
    agent_id: str
    model: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    messages: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        return get_cost(self.model, self.input_tokens, self.output_tokens)

    def add_user_message(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_assistant_message(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_session.py -v
```

Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/session.py tests/test_session.py
git commit -m "feat: add Session dataclass with token tracking and cost property"
```

---

## Task 4: BaseProvider + providers package

**Files:**
- Create: `harness_claw/providers/__init__.py`
- Create: `harness_claw/providers/base.py`

- [ ] **Step 1: Create providers/__init__.py**

Create an empty file at `harness_claw/providers/__init__.py`.

- [ ] **Step 2: Create base.py**

Create `harness_claw/providers/base.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Callable, Awaitable


class BaseProvider:
    """
    Base class for AI providers.

    stream_chat yields dicts with type "token" (delta: str) or "usage"
    (input_tokens: int, output_tokens: int).

    stream_with_tools yields the same plus "tool_call"
    (tool_id: str, name: str, input: dict). The tool_executor callback
    receives (tool_name, tool_input) and returns the result string.
    """

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        max_tokens: int,
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError
        yield  # make this an async generator  # noqa: unreachable

    async def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_tokens: int,
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError
        yield  # noqa: unreachable
```

- [ ] **Step 3: Commit**

```bash
git add harness_claw/providers/__init__.py harness_claw/providers/base.py
git commit -m "feat: add BaseProvider interface for AI providers"
```

---

## Task 5: AnthropicProvider

**Files:**
- Create: `harness_claw/providers/anthropic.py`

- [ ] **Step 1: Create anthropic.py**

Create `harness_claw/providers/anthropic.py`:

```python
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Callable, Awaitable

import anthropic

from harness_claw.providers.base import BaseProvider


class AnthropicProvider(BaseProvider):
    def __init__(self) -> None:
        # Reads ANTHROPIC_API_KEY from environment automatically
        self._client = anthropic.AsyncAnthropic()

    async def stream_chat(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        max_tokens: int,
    ) -> AsyncIterator[dict[str, Any]]:
        async for event in self._stream_once(messages, system, model, [], max_tokens):
            if event["type"] in ("token", "usage"):
                yield event

    async def stream_with_tools(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        tools: list[dict[str, Any]],
        tool_executor: Callable[[str, dict[str, Any]], Awaitable[str]],
        max_tokens: int,
    ) -> AsyncIterator[dict[str, Any]]:
        current_messages = list(messages)

        while True:
            stop_reason: str | None = None
            content_blocks: list[dict[str, Any]] = []
            tool_use_blocks: list[dict[str, Any]] = []

            async for event in self._stream_once(current_messages, system, model, tools, max_tokens):
                if event["type"] == "token":
                    yield event
                elif event["type"] == "usage":
                    yield event
                elif event["type"] == "_stop":
                    stop_reason = event["stop_reason"]
                    content_blocks = event["content"]
                    tool_use_blocks = [b for b in content_blocks if b["type"] == "tool_use"]
                    for b in tool_use_blocks:
                        yield {
                            "type": "tool_call",
                            "tool_id": b["id"],
                            "name": b["name"],
                            "input": b["input"],
                        }

            if stop_reason != "tool_use":
                break

            tool_results = []
            for block in tool_use_blocks:
                result = await tool_executor(block["name"], block["input"])
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": result,
                })

            current_messages = current_messages + [
                {"role": "assistant", "content": content_blocks},
                {"role": "user", "content": tool_results},
            ]

    async def _stream_once(
        self,
        messages: list[dict[str, Any]],
        system: str,
        model: str,
        tools: list[dict[str, Any]],
        max_tokens: int,
    ) -> AsyncIterator[dict[str, Any]]:
        input_tokens = 0
        output_tokens = 0
        stop_reason: str | None = None
        content_blocks: list[dict[str, Any]] = []
        current_block: dict[str, Any] | None = None
        current_tool_input = ""

        kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        if tools:
            kwargs["tools"] = tools

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if isinstance(event, anthropic.types.RawMessageStartEvent):
                    input_tokens = event.message.usage.input_tokens

                elif isinstance(event, anthropic.types.RawContentBlockStartEvent):
                    cb = event.content_block
                    if cb.type == "text":
                        current_block = {"type": "text", "text": ""}
                    elif cb.type == "tool_use":
                        current_block = {
                            "type": "tool_use",
                            "id": cb.id,
                            "name": cb.name,
                            "input": {},
                        }
                        current_tool_input = ""

                elif isinstance(event, anthropic.types.RawContentBlockDeltaEvent):
                    if event.delta.type == "text_delta" and current_block:
                        current_block["text"] += event.delta.text
                        yield {"type": "token", "delta": event.delta.text}
                    elif event.delta.type == "input_json_delta":
                        current_tool_input += event.delta.partial_json

                elif isinstance(event, anthropic.types.RawContentBlockStopEvent):
                    if current_block:
                        if current_block["type"] == "tool_use":
                            current_block["input"] = (
                                json.loads(current_tool_input) if current_tool_input else {}
                            )
                        content_blocks.append(current_block)
                    current_block = None
                    current_tool_input = ""

                elif isinstance(event, anthropic.types.RawMessageDeltaEvent):
                    stop_reason = event.delta.stop_reason
                    output_tokens = event.usage.output_tokens

        yield {"type": "usage", "input_tokens": input_tokens, "output_tokens": output_tokens}
        yield {"type": "_stop", "stop_reason": stop_reason, "content": content_blocks}
```

- [ ] **Step 2: Verify import works**

```bash
python -c "from harness_claw.providers.anthropic import AnthropicProvider; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add harness_claw/providers/anthropic.py
git commit -m "feat: implement AnthropicProvider with streaming and tool-use loop"
```

---

## Task 6: AgentRegistry + agents.yaml

**Files:**
- Create: `harness_claw/agent_registry.py`
- Create: `agents.yaml`

- [ ] **Step 1: Create agents.yaml**

Create `agents.yaml` at the project root:

```yaml
agents:
  - id: assistant
    name: Assistant
    provider: anthropic
    model: claude-sonnet-4-6
    system_prompt: "You are a helpful assistant."
    max_tokens: 4096

  - id: code-writer
    name: Code Writer
    provider: anthropic
    model: claude-sonnet-4-6
    system_prompt: "You write clean, well-tested Python code. Return only code and brief explanations."
    max_tokens: 8192

  - id: reviewer
    name: Code Reviewer
    provider: anthropic
    model: claude-sonnet-4-6
    system_prompt: "You review code for correctness, clarity, and security. Be concise and specific."
    max_tokens: 4096

  - id: coordinator
    name: Coordinator
    provider: anthropic
    model: claude-sonnet-4-6
    orchestrates:
      - code-writer
      - reviewer
    system_prompt: |
      You are a coordinator agent. You break down tasks and delegate them to specialist sub-agents
      using the call_agent tool. First call the code-writer to produce code, then call the reviewer
      to review it. Synthesize the results into a final response.
    max_tokens: 4096
```

- [ ] **Step 2: Create agent_registry.py**

Create `harness_claw/agent_registry.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    id: str
    name: str
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    system_prompt: str = "You are a helpful assistant."
    max_tokens: int = 4096
    orchestrates: list[str] = Field(default_factory=list)


class AgentRegistry:
    def __init__(self, config_path: Path | str = "agents.yaml") -> None:
        self._config_path = Path(config_path)
        self._agents: dict[str, AgentConfig] = {}
        self._load_from_file()

    def _load_from_file(self) -> None:
        if not self._config_path.exists():
            return
        data = yaml.safe_load(self._config_path.read_text()) or {}
        for entry in data.get("agents", []):
            cfg = AgentConfig(**entry)
            self._agents[cfg.id] = cfg

    def get(self, agent_id: str) -> AgentConfig:
        if agent_id not in self._agents:
            raise KeyError(f"Agent '{agent_id}' not found")
        return self._agents[agent_id]

    def all(self) -> list[AgentConfig]:
        return list(self._agents.values())

    def add(self, config: AgentConfig) -> None:
        self._agents[config.id] = config

    def remove(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)

    def update(self, config: AgentConfig) -> None:
        if config.id not in self._agents:
            raise KeyError(f"Agent '{config.id}' not found")
        self._agents[config.id] = config
```

- [ ] **Step 3: Verify loading works**

```bash
python -c "
from harness_claw.agent_registry import AgentRegistry
r = AgentRegistry('agents.yaml')
for a in r.all():
    print(a.id, '-', a.name)
"
```

Expected output:
```
assistant - Assistant
code-writer - Code Writer
reviewer - Code Reviewer
coordinator - Coordinator
```

- [ ] **Step 4: Commit**

```bash
git add harness_claw/agent_registry.py agents.yaml
git commit -m "feat: add AgentRegistry with YAML config loading and runtime agent management"
```

---

## Task 7: JobRunner (TDD with MockProvider)

**Files:**
- Create: `harness_claw/job_runner.py`
- Create: `tests/test_job_runner.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_job_runner.py`:

```python
from __future__ import annotations

import pytest
from collections.abc import AsyncIterator
from typing import Any, Callable, Awaitable

from harness_claw.agent_registry import AgentConfig, AgentRegistry
from harness_claw.job_runner import JobRunner, PROVIDERS
from harness_claw.providers.base import BaseProvider


class MockProvider(BaseProvider):
    def __init__(
        self,
        tokens: list[str],
        input_tokens: int = 10,
        output_tokens: int = 5,
    ) -> None:
        self._tokens = tokens
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens

    async def stream_chat(self, messages, system, model, max_tokens) -> AsyncIterator[dict]:
        for token in self._tokens:
            yield {"type": "token", "delta": token}
        yield {"type": "usage", "input_tokens": self._input_tokens, "output_tokens": self._output_tokens}

    async def stream_with_tools(
        self, messages, system, model, tools, tool_executor, max_tokens
    ) -> AsyncIterator[dict]:
        for token in self._tokens:
            yield {"type": "token", "delta": token}
        yield {"type": "usage", "input_tokens": self._input_tokens, "output_tokens": self._output_tokens}


@pytest.fixture
def registry():
    r = AgentRegistry.__new__(AgentRegistry)
    r._agents = {
        "test-agent": AgentConfig(
            id="test-agent",
            name="Test Agent",
            provider="mock",
            model="claude-sonnet-4-6",
        ),
        "orchestrator": AgentConfig(
            id="orchestrator",
            name="Orchestrator",
            provider="mock",
            model="claude-sonnet-4-6",
            orchestrates=["test-agent"],
        ),
    }
    return r


@pytest.fixture(autouse=True)
def patch_providers(monkeypatch):
    monkeypatch.setitem(PROVIDERS, "mock", MockProvider(tokens=["Hello", ", ", "world", "!"]))


@pytest.mark.asyncio
async def test_run_job_streams_tokens(registry):
    runner = JobRunner(registry)
    received: list[dict] = []

    async def send(msg: dict) -> None:
        received.append(msg)

    await runner.run_job("test-agent", "Hi", send)

    token_events = [m for m in received if m["type"] == "token"]
    assert [e["delta"] for e in token_events] == ["Hello", ", ", "world", "!"]


@pytest.mark.asyncio
async def test_run_job_sends_running_then_completed(registry):
    runner = JobRunner(registry)
    received: list[dict] = []

    async def send(msg: dict) -> None:
        received.append(msg)

    await runner.run_job("test-agent", "Hi", send)

    updates = [m for m in received if m["type"] == "job_update"]
    statuses = [u["status"] for u in updates]
    assert statuses[0] == "running"
    assert statuses[-1] == "completed"


@pytest.mark.asyncio
async def test_run_job_tracks_usage(registry):
    runner = JobRunner(registry)
    received: list[dict] = []

    async def send(msg: dict) -> None:
        received.append(msg)

    await runner.run_job("test-agent", "Hi", send)

    usage = [m for m in received if m["type"] == "usage"]
    assert len(usage) == 1
    assert usage[0]["input_tokens"] == 10
    assert usage[0]["output_tokens"] == 5
    assert usage[0]["cost_usd"] > 0


@pytest.mark.asyncio
async def test_session_persists_messages(registry):
    runner = JobRunner(registry)

    async def send(msg: dict) -> None:
        pass

    await runner.run_job("test-agent", "Hello", send)

    session = runner.get_session("test-agent")
    assert session is not None
    assert session.messages[0] == {"role": "user", "content": "Hello"}
    assert session.messages[1]["role"] == "assistant"
    assert "Hello" in session.messages[1]["content"]


@pytest.mark.asyncio
async def test_second_message_appends_to_session(registry):
    runner = JobRunner(registry)

    async def send(msg: dict) -> None:
        pass

    await runner.run_job("test-agent", "First", send)
    await runner.run_job("test-agent", "Second", send)

    session = runner.get_session("test-agent")
    assert len(session.messages) == 4  # user, assistant, user, assistant


@pytest.mark.asyncio
async def test_orchestrator_uses_stream_with_tools(registry, monkeypatch):
    calls: list[str] = []
    original = MockProvider.stream_with_tools

    async def tracking_stream_with_tools(self, *args, **kwargs) -> AsyncIterator[dict]:
        calls.append("stream_with_tools")
        async for e in original(self, *args, **kwargs):
            yield e

    monkeypatch.setattr(MockProvider, "stream_with_tools", tracking_stream_with_tools)

    runner = JobRunner(registry)

    async def send(msg: dict) -> None:
        pass

    await runner.run_job("orchestrator", "Write code", send)
    assert "stream_with_tools" in calls
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_job_runner.py -v
```

Expected: `ModuleNotFoundError` — `job_runner` not yet defined.

- [ ] **Step 3: Create job_runner.py**

Create `harness_claw/job_runner.py`:

```python
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable, Awaitable

from harness_claw.agent_registry import AgentConfig, AgentRegistry
from harness_claw.providers.anthropic import AnthropicProvider
from harness_claw.providers.base import BaseProvider
from harness_claw.session import Session

PROVIDERS: dict[str, BaseProvider] = {
    "anthropic": AnthropicProvider(),
}


class JobRunner:
    def __init__(self, registry: AgentRegistry) -> None:
        self._registry = registry
        self._sessions: dict[str, Session] = {}  # keyed by agent_id

    def get_or_create_session(self, agent_id: str) -> Session:
        if agent_id not in self._sessions:
            agent = self._registry.get(agent_id)
            self._sessions[agent_id] = Session(agent_id=agent_id, model=agent.model)
        return self._sessions[agent_id]

    def get_session(self, agent_id: str) -> Session | None:
        return self._sessions.get(agent_id)

    async def run_job(
        self,
        agent_id: str,
        text: str,
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> str:
        job_id = str(uuid.uuid4())
        agent = self._registry.get(agent_id)
        session = self.get_or_create_session(agent_id)
        provider = PROVIDERS[agent.provider]

        session.add_user_message(text)
        await send({
            "type": "job_update",
            "job_id": job_id,
            "agent_id": agent_id,
            "title": text[:60],
            "status": "running",
            "progress": None,
        })

        assistant_text = ""

        try:
            if agent.orchestrates:
                tools = [self._make_call_agent_tool(agent.orchestrates)]

                async def tool_executor(tool_name: str, tool_input: dict[str, Any]) -> str:
                    if tool_name != "call_agent":
                        return f"Error: unknown tool '{tool_name}'"
                    sub_agent_id = tool_input["agent_id"]
                    prompt = tool_input["prompt"]
                    return await self._run_sub_agent(sub_agent_id, prompt, send)

                async for event in provider.stream_with_tools(
                    session.messages, agent.system_prompt, agent.model,
                    tools, tool_executor, agent.max_tokens,
                ):
                    if event["type"] == "token":
                        assistant_text += event["delta"]
                        await send({"type": "token", "job_id": job_id, "delta": event["delta"]})
                    elif event["type"] == "tool_call":
                        await send({"type": "tool_call", "job_id": job_id, **event})
                    elif event["type"] == "usage":
                        session.input_tokens += event["input_tokens"]
                        session.output_tokens += event["output_tokens"]
                        await send({
                            "type": "usage",
                            "job_id": job_id,
                            "input_tokens": session.input_tokens,
                            "output_tokens": session.output_tokens,
                            "cost_usd": session.cost_usd,
                        })
            else:
                async for event in provider.stream_chat(
                    session.messages, agent.system_prompt, agent.model, agent.max_tokens,
                ):
                    if event["type"] == "token":
                        assistant_text += event["delta"]
                        await send({"type": "token", "job_id": job_id, "delta": event["delta"]})
                    elif event["type"] == "usage":
                        session.input_tokens += event["input_tokens"]
                        session.output_tokens += event["output_tokens"]
                        await send({
                            "type": "usage",
                            "job_id": job_id,
                            "input_tokens": session.input_tokens,
                            "output_tokens": session.output_tokens,
                            "cost_usd": session.cost_usd,
                        })

            session.add_assistant_message(assistant_text)
            await send({
                "type": "job_update",
                "job_id": job_id,
                "agent_id": agent_id,
                "status": "completed",
                "progress": None,
            })

        except Exception as exc:
            await send({"type": "error", "job_id": job_id, "message": str(exc)})
            await send({
                "type": "job_update",
                "job_id": job_id,
                "agent_id": agent_id,
                "status": "failed",
                "progress": None,
            })

        return job_id

    async def _run_sub_agent(
        self,
        agent_id: str,
        prompt: str,
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> str:
        sub_job_id = str(uuid.uuid4())
        agent = self._registry.get(agent_id)
        provider = PROVIDERS[agent.provider]

        # Sub-agents run with a fresh session per invocation (no history)
        sub_session = Session(agent_id=agent_id, model=agent.model)
        sub_session.add_user_message(prompt)

        await send({
            "type": "job_update",
            "job_id": sub_job_id,
            "agent_id": agent_id,
            "title": prompt[:60],
            "status": "running",
            "progress": None,
        })

        result_text = ""
        async for event in provider.stream_chat(
            sub_session.messages, agent.system_prompt, agent.model, agent.max_tokens,
        ):
            if event["type"] == "token":
                result_text += event["delta"]
                await send({"type": "token", "job_id": sub_job_id, "delta": event["delta"]})
            elif event["type"] == "usage":
                await send({"type": "usage", "job_id": sub_job_id, **event, "cost_usd": 0.0})

        await send({
            "type": "job_update",
            "job_id": sub_job_id,
            "agent_id": agent_id,
            "status": "completed",
            "progress": None,
        })

        return result_text

    @staticmethod
    def _make_call_agent_tool(orchestrates: list[str]) -> dict[str, Any]:
        return {
            "name": "call_agent",
            "description": (
                "Call a sub-agent to perform a specific task. "
                "Returns the agent's full response as a string."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "The ID of the sub-agent to call.",
                        "enum": orchestrates,
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The task or question to send to the sub-agent.",
                    },
                },
                "required": ["agent_id", "prompt"],
            },
        }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_job_runner.py -v
```

Expected: All 6 tests PASS.

- [ ] **Step 5: Run all tests**

```bash
pytest tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
git add harness_claw/job_runner.py tests/test_job_runner.py
git commit -m "feat: add JobRunner with streaming, orchestrator tool-use loop, and sub-agent dispatch"
```

---

## Task 8: FastAPI Server

**Files:**
- Create: `harness_claw/server.py`

- [ ] **Step 1: Create server.py**

Create `harness_claw/server.py`:

```python
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from harness_claw.agent_registry import AgentConfig, AgentRegistry
from harness_claw.job_runner import JobRunner

app = FastAPI(title="HarnessClaw")

registry = AgentRegistry(Path(__file__).parent.parent / "agents.yaml")
runner = JobRunner(registry)


# ── REST endpoints ─────────────────────────────────────────────────────────────

@app.get("/api/agents")
async def list_agents() -> list[dict]:
    return [a.model_dump() for a in registry.all()]


class AgentCreateRequest(BaseModel):
    id: str
    name: str
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    system_prompt: str = "You are a helpful assistant."
    max_tokens: int = 4096
    orchestrates: list[str] = []


@app.post("/api/agents", status_code=201)
async def create_agent(req: AgentCreateRequest) -> dict:
    config = AgentConfig(**req.model_dump())
    registry.add(config)
    return config.model_dump()


@app.put("/api/agents/{agent_id}")
async def update_agent(agent_id: str, req: AgentCreateRequest) -> dict:
    data = req.model_dump()
    data["id"] = agent_id
    config = AgentConfig(**data)
    registry.update(config)
    return config.model_dump()


@app.delete("/api/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str) -> None:
    registry.remove(agent_id)


@app.get("/api/sessions/{agent_id}")
async def get_session(agent_id: str) -> dict:
    session = runner.get_session(agent_id)
    if session is None:
        return {
            "messages": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": 0.0,
            "model": "",
        }
    return {
        "messages": session.messages,
        "input_tokens": session.input_tokens,
        "output_tokens": session.output_tokens,
        "cost_usd": session.cost_usd,
        "model": session.model,
    }


# ── WebSocket ──────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    send_queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def send(msg: dict) -> None:
        await send_queue.put(msg)

    async def sender() -> None:
        while True:
            msg = await send_queue.get()
            if msg is None:
                break
            try:
                await websocket.send_json(msg)
            except Exception:
                return

    sender_task = asyncio.create_task(sender())

    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "chat":
                asyncio.create_task(
                    runner.run_job(data["agent_id"], data["text"], send)
                )
    except WebSocketDisconnect:
        pass
    finally:
        await send_queue.put(None)
        await sender_task


# ── Serve React build (production) ────────────────────────────────────────────

_UI_DIST = Path(__file__).parent.parent / "ui" / "dist"

if _UI_DIST.exists():
    app.mount("/assets", StaticFiles(directory=_UI_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> FileResponse:
        return FileResponse(_UI_DIST / "index.html")
```

- [ ] **Step 2: Verify server starts**

```bash
uvicorn harness_claw.server:app --reload --port 8000
```

Expected: Server starts, no import errors. Visit `http://localhost:8000/api/agents` — should return the agents from `agents.yaml`.

Stop the server (Ctrl+C).

- [ ] **Step 3: Commit**

```bash
git add harness_claw/server.py
git commit -m "feat: add FastAPI server with REST endpoints and WebSocket streaming"
```

---

## Task 9: Frontend Scaffold

**Files:**
- Create: `ui/package.json`, `ui/vite.config.ts`, `ui/tsconfig.json`
- Create: `ui/tailwind.config.js`, `ui/postcss.config.js`
- Create: `ui/index.html`, `ui/src/main.tsx`, `ui/src/index.css`

- [ ] **Step 1: Create ui/package.json**

```json
{
  "name": "harnessclaw-ui",
  "private": true,
  "version": "0.0.1",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.3",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "autoprefixer": "^10.4.19",
    "postcss": "^8.4.38",
    "tailwindcss": "^3.4.3",
    "typescript": "^5.4.5",
    "vite": "^5.2.11"
  }
}
```

- [ ] **Step 2: Create ui/vite.config.ts**

```typescript
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    },
  },
})
```

- [ ] **Step 3: Create ui/tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true
  },
  "include": ["src"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

- [ ] **Step 4: Create ui/tsconfig.node.json**

```json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 5: Create ui/tailwind.config.js**

```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {},
  },
  plugins: [],
}
```

- [ ] **Step 6: Create ui/postcss.config.js**

```javascript
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```

- [ ] **Step 7: Create ui/index.html**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>HarnessClaw</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 8: Create ui/src/index.css**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

body {
  margin: 0;
  background-color: #0d1117;
  color: #e6edf3;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
```

- [ ] **Step 9: Create ui/src/main.tsx**

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

- [ ] **Step 10: Install dependencies**

```bash
cd ui && npm install
```

Expected: `node_modules` created, no errors.

- [ ] **Step 11: Commit**

```bash
cd ..
git add ui/
git commit -m "chore: scaffold React/Vite/Tailwind frontend"
```

---

## Task 10: types.ts + ws.ts

**Files:**
- Create: `ui/src/types.ts`
- Create: `ui/src/ws.ts`

- [ ] **Step 1: Create ui/src/types.ts**

```typescript
export interface AgentConfig {
  id: string
  name: string
  provider: string
  model: string
  system_prompt: string
  max_tokens: number
  orchestrates: string[]
}

export type JobStatus = 'queued' | 'running' | 'completed' | 'failed'

export interface Job {
  job_id: string
  agent_id: string
  title: string
  status: JobStatus
  progress: number | null
}

export interface ToolCallEvent {
  tool_id: string
  name: string
  input: Record<string, unknown>
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
  tool_calls?: ToolCallEvent[]
}

export interface SessionState {
  messages: Message[]
  streamingMessages: Record<string, string>  // job_id → accumulated text
  jobs: Job[]
  input_tokens: number
  output_tokens: number
  cost_usd: number
  model: string
}

export type WSIncoming =
  | { type: 'token'; job_id: string; delta: string }
  | { type: 'job_update'; job_id: string; agent_id: string; title?: string; status: JobStatus; progress: number | null }
  | { type: 'tool_call'; job_id: string; tool_id: string; name: string; input: Record<string, unknown> }
  | { type: 'usage'; job_id: string; input_tokens: number; output_tokens: number; cost_usd: number }
  | { type: 'error'; job_id: string; message: string }
```

- [ ] **Step 2: Create ui/src/ws.ts**

```typescript
export type MessageHandler = (msg: unknown) => void

export class WsClient {
  private ws: WebSocket | null = null
  private handler: MessageHandler
  private reconnectDelay = 1000
  private destroyed = false

  constructor(handler: MessageHandler) {
    this.handler = handler
    this.connect()
  }

  private connect(): void {
    const url = `ws://${window.location.host}/ws`
    this.ws = new WebSocket(url)

    this.ws.onmessage = (event) => {
      try {
        this.handler(JSON.parse(event.data as string))
      } catch {
        // ignore malformed messages
      }
    }

    this.ws.onclose = () => {
      if (!this.destroyed) {
        setTimeout(() => this.connect(), this.reconnectDelay)
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30_000)
      }
    }

    this.ws.onopen = () => {
      this.reconnectDelay = 1000
    }
  }

  send(msg: object): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg))
    }
  }

  destroy(): void {
    this.destroyed = true
    this.ws?.close()
  }
}
```

- [ ] **Step 3: Commit**

```bash
git add ui/src/types.ts ui/src/ws.ts
git commit -m "feat: add TypeScript types and WebSocket client with auto-reconnect"
```

---

## Task 11: AgentSidebar + SessionCostBar

**Files:**
- Create: `ui/src/components/AgentSidebar.tsx`
- Create: `ui/src/components/SessionCostBar.tsx`

- [ ] **Step 1: Create ui/src/components/AgentSidebar.tsx**

```tsx
import type { AgentConfig } from '../types'

interface Props {
  agents: AgentConfig[]
  activeAgentId: string | null
  onSelect: (id: string) => void
  onNewAgent: () => void
}

export function AgentSidebar({ agents, activeAgentId, onSelect, onNewAgent }: Props) {
  // Build parent → children map for orchestrators
  const childIds = new Set(agents.flatMap((a) => a.orchestrates))
  const topLevel = agents.filter((a) => !childIds.has(a.id))

  function renderAgent(agent: AgentConfig, indent = false) {
    const isActive = agent.id === activeAgentId
    const isOrchestrator = agent.orchestrates.length > 0
    const children = agents.filter((a) => agent.orchestrates.includes(a.id))

    return (
      <div key={agent.id}>
        <button
          onClick={() => onSelect(agent.id)}
          className={`w-full text-left px-3 py-2 rounded text-sm flex items-center gap-2 transition-colors ${
            indent ? 'ml-4 w-[calc(100%-1rem)]' : ''
          } ${
            isActive
              ? 'bg-blue-900/40 text-blue-300 border-l-2 border-blue-500'
              : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'
          }`}
        >
          <span className="text-xs">{isOrchestrator ? '⬡' : '○'}</span>
          <span className="truncate">{agent.name}</span>
        </button>
        {children.map((child) => renderAgent(child, true))}
      </div>
    )
  }

  return (
    <div className="w-52 shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col h-full">
      <div className="px-3 py-3 border-b border-gray-800">
        <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
          Agents
        </span>
      </div>
      <div className="flex-1 overflow-y-auto py-2 px-2 flex flex-col gap-1">
        {topLevel.map((agent) => renderAgent(agent))}
      </div>
      <div className="p-2 border-t border-gray-800">
        <button
          onClick={onNewAgent}
          className="w-full py-2 rounded text-sm text-white bg-green-800 hover:bg-green-700 transition-colors"
        >
          + New Agent
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create ui/src/components/SessionCostBar.tsx**

```tsx
interface Props {
  model: string
  inputTokens: number
  outputTokens: number
  costUsd: number
}

export function SessionCostBar({ model, inputTokens, outputTokens, costUsd }: Props) {
  const totalTokens = inputTokens + outputTokens

  return (
    <div className="px-4 py-2 border-b border-gray-800 flex items-center justify-between text-xs text-gray-500">
      <span className="font-mono">{model || '—'}</span>
      <div className="flex items-center gap-4">
        <span>{totalTokens.toLocaleString()} tokens</span>
        <span className="text-green-500 font-medium">
          ${costUsd.toFixed(4)}
        </span>
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add ui/src/components/AgentSidebar.tsx ui/src/components/SessionCostBar.tsx
git commit -m "feat: add AgentSidebar and SessionCostBar components"
```

---

## Task 12: ChatPanel + JobsPanel

**Files:**
- Create: `ui/src/components/ChatPanel.tsx`
- Create: `ui/src/components/JobsPanel.tsx`

- [ ] **Step 1: Create ui/src/components/ChatPanel.tsx**

```tsx
import { useEffect, useRef } from 'react'
import type { Message, ToolCallEvent } from '../types'

interface Props {
  messages: Message[]
  streamingMessages: Record<string, string>
  onSend: (text: string) => void
  disabled?: boolean
}

function ToolCallCard({ toolCall }: { toolCall: ToolCallEvent }) {
  return (
    <div className="border border-gray-700 rounded-md p-3 bg-gray-900 text-xs my-1">
      <div className="text-yellow-400 font-medium mb-1">→ Calling: {toolCall.name}</div>
      <div className="text-gray-500 font-mono truncate">
        {JSON.stringify(toolCall.input).slice(0, 120)}
      </div>
    </div>
  )
}

export function ChatPanel({ messages, streamingMessages, onSend, disabled }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingMessages])

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      const text = inputRef.current?.value.trim()
      if (text) {
        onSend(text)
        inputRef.current!.value = ''
      }
    }
  }

  // Find active streaming message job_ids (those with tokens but no completed message yet)
  const streamingEntries = Object.entries(streamingMessages)

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Message list */}
      <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-3">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex flex-col gap-1 max-w-[80%] ${
              msg.role === 'user' ? 'self-end items-end' : 'self-start items-start'
            }`}
          >
            {msg.tool_calls?.map((tc) => <ToolCallCard key={tc.tool_id} toolCall={tc} />)}
            <div
              className={`rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                msg.role === 'user'
                  ? 'bg-blue-900/30 text-gray-200'
                  : 'bg-gray-800 text-gray-200'
              }`}
            >
              {msg.content}
            </div>
          </div>
        ))}

        {/* Active streaming messages */}
        {streamingEntries.map(([jobId, text]) => (
          <div key={jobId} className="self-start max-w-[80%]">
            <div className="rounded-lg px-3 py-2 text-sm bg-gray-800 text-gray-200 whitespace-pre-wrap">
              {text}
              <span className="inline-block w-1.5 h-3.5 ml-0.5 bg-blue-400 animate-pulse align-middle" />
            </div>
          </div>
        ))}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="border-t border-gray-800 p-3 flex gap-2">
        <textarea
          ref={inputRef}
          rows={1}
          disabled={disabled}
          onKeyDown={handleKeyDown}
          placeholder="Message… (Enter to send, Shift+Enter for newline)"
          className="flex-1 bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-200 placeholder-gray-600 resize-none focus:outline-none focus:border-blue-600"
        />
        <button
          onClick={() => {
            const text = inputRef.current?.value.trim()
            if (text) {
              onSend(text)
              inputRef.current!.value = ''
            }
          }}
          disabled={disabled}
          className="px-4 py-2 bg-blue-700 hover:bg-blue-600 disabled:opacity-50 text-white text-sm rounded-md transition-colors"
        >
          Send
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create ui/src/components/JobsPanel.tsx**

```tsx
import type { Job, JobStatus } from '../types'

interface Props {
  jobs: Job[]
}

const STATUS_STYLES: Record<JobStatus, string> = {
  queued:    'text-gray-400',
  running:   'text-blue-400',
  completed: 'text-green-400',
  failed:    'text-red-400',
}

const STATUS_LABELS: Record<JobStatus, string> = {
  queued:    '◌ Queued',
  running:   '● Running',
  completed: '✓ Done',
  failed:    '✗ Failed',
}

export function JobsPanel({ jobs }: Props) {
  if (jobs.length === 0) {
    return (
      <div className="w-36 shrink-0 border-l border-gray-800 p-3">
        <div className="text-xs font-semibold text-gray-600 uppercase tracking-wider mb-2">
          Jobs
        </div>
        <div className="text-xs text-gray-600">No jobs yet</div>
      </div>
    )
  }

  return (
    <div className="w-36 shrink-0 border-l border-gray-800 flex flex-col h-full">
      <div className="px-3 py-3 border-b border-gray-800">
        <span className="text-xs font-semibold text-gray-600 uppercase tracking-wider">
          Jobs
        </span>
      </div>
      <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-2">
        {[...jobs].reverse().map((job) => (
          <div
            key={job.job_id}
            className="bg-gray-900 border border-gray-800 rounded-md p-2"
            style={{ borderLeftColor: job.status === 'running' ? '#3b82f6' : undefined, borderLeftWidth: job.status === 'running' ? '2px' : undefined }}
          >
            <div className="text-xs text-gray-300 truncate mb-1">{job.title || job.agent_id}</div>
            <div className={`text-xs ${STATUS_STYLES[job.status]}`}>
              {STATUS_LABELS[job.status]}
            </div>
            {job.status === 'running' && (
              <div className="mt-1.5 h-1 bg-gray-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-600 rounded-full transition-all duration-300"
                  style={{ width: job.progress != null ? `${job.progress}%` : '40%', animation: job.progress == null ? 'pulse 1.5s ease-in-out infinite' : undefined }}
                />
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Commit**

```bash
git add ui/src/components/ChatPanel.tsx ui/src/components/JobsPanel.tsx
git commit -m "feat: add ChatPanel with streaming and JobsPanel with status tracking"
```

---

## Task 13: AgentConfigPanel

**Files:**
- Create: `ui/src/components/AgentConfigPanel.tsx`

- [ ] **Step 1: Create ui/src/components/AgentConfigPanel.tsx**

```tsx
import { useState } from 'react'
import type { AgentConfig } from '../types'

interface Props {
  agents: AgentConfig[]
  editingAgent?: AgentConfig | null
  onSave: (config: AgentConfig) => void
  onCancel: () => void
}

export function AgentConfigPanel({ agents, editingAgent, onSave, onCancel }: Props) {
  const [id, setId] = useState(editingAgent?.id ?? '')
  const [name, setName] = useState(editingAgent?.name ?? '')
  const [model, setModel] = useState(editingAgent?.model ?? 'claude-sonnet-4-6')
  const [systemPrompt, setSystemPrompt] = useState(editingAgent?.system_prompt ?? 'You are a helpful assistant.')
  const [maxTokens, setMaxTokens] = useState(editingAgent?.max_tokens ?? 4096)
  const [orchestrates, setOrchestrates] = useState<string[]>(editingAgent?.orchestrates ?? [])

  const models = [
    'claude-sonnet-4-6',
    'claude-opus-4-6',
    'claude-haiku-4-5-20251001',
  ]

  const potentialSubAgents = agents.filter((a) => a.id !== id)

  function toggleSubAgent(agentId: string) {
    setOrchestrates((prev) =>
      prev.includes(agentId) ? prev.filter((x) => x !== agentId) : [...prev, agentId],
    )
  }

  function handleSave() {
    if (!id.trim() || !name.trim()) return
    onSave({
      id: id.trim(),
      name: name.trim(),
      provider: 'anthropic',
      model,
      system_prompt: systemPrompt,
      max_tokens: maxTokens,
      orchestrates,
    })
  }

  return (
    <div className="flex-1 overflow-y-auto p-6">
      <div className="max-w-xl">
        <h2 className="text-lg font-semibold text-gray-200 mb-6">
          {editingAgent ? `Edit: ${editingAgent.name}` : 'New Agent'}
        </h2>

        <div className="flex flex-col gap-4">
          {/* ID */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              Agent ID
            </label>
            <input
              value={id}
              onChange={(e) => setId(e.target.value)}
              disabled={!!editingAgent}
              placeholder="my-agent"
              className="w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-600 disabled:opacity-50"
            />
            <p className="mt-1 text-xs text-gray-600">Lowercase, hyphen-separated. Cannot be changed after creation.</p>
          </div>

          {/* Name */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              Display Name
            </label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="My Agent"
              className="w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-200 placeholder-gray-600 focus:outline-none focus:border-blue-600"
            />
          </div>

          {/* Model */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              Model
            </label>
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-600"
            >
              {models.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>

          {/* System Prompt */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              System Prompt
            </label>
            <textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={6}
              className="w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-200 placeholder-gray-600 resize-y focus:outline-none focus:border-blue-600 font-mono"
            />
          </div>

          {/* Max Tokens */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
              Max Tokens
            </label>
            <input
              type="number"
              value={maxTokens}
              onChange={(e) => setMaxTokens(Number(e.target.value))}
              min={256}
              max={65536}
              className="w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-blue-600"
            />
          </div>

          {/* Sub-agents (orchestrates) */}
          {potentialSubAgents.length > 0 && (
            <div>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">
                Orchestrates (sub-agents)
              </label>
              <div className="flex flex-col gap-1">
                {potentialSubAgents.map((a) => (
                  <label key={a.id} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={orchestrates.includes(a.id)}
                      onChange={() => toggleSubAgent(a.id)}
                      className="accent-blue-500"
                    />
                    <span className="text-sm text-gray-300">{a.name}</span>
                    <span className="text-xs text-gray-600">({a.id})</span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3 pt-2">
            <button
              onClick={handleSave}
              className="px-4 py-2 bg-green-800 hover:bg-green-700 text-white text-sm rounded-md transition-colors"
            >
              {editingAgent ? 'Save Changes' : 'Create Agent'}
            </button>
            <button
              onClick={onCancel}
              className="px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-md transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add ui/src/components/AgentConfigPanel.tsx
git commit -m "feat: add AgentConfigPanel for creating and editing agents"
```

---

## Task 14: App.tsx

**Files:**
- Create: `ui/src/App.tsx`

- [ ] **Step 1: Create ui/src/App.tsx**

```tsx
import { useEffect, useRef, useState, useCallback } from 'react'
import { WsClient } from './ws'
import type { AgentConfig, Job, Message, SessionState, WSIncoming } from './types'
import { AgentSidebar } from './components/AgentSidebar'
import { ChatPanel } from './components/ChatPanel'
import { JobsPanel } from './components/JobsPanel'
import { SessionCostBar } from './components/SessionCostBar'
import { AgentConfigPanel } from './components/AgentConfigPanel'

function emptySession(model: string): SessionState {
  return {
    messages: [],
    streamingMessages: {},
    jobs: [],
    input_tokens: 0,
    output_tokens: 0,
    cost_usd: 0,
    model,
  }
}

let msgCounter = 0
function nextId() {
  return String(++msgCounter)
}

export default function App() {
  const [agents, setAgents] = useState<AgentConfig[]>([])
  const [activeAgentId, setActiveAgentId] = useState<string | null>(null)
  const [sessions, setSessions] = useState<Record<string, SessionState>>({})
  const [showConfig, setShowConfig] = useState(false)
  const [editingAgent, setEditingAgent] = useState<AgentConfig | null>(null)
  const wsRef = useRef<WsClient | null>(null)
  // Map job_id → agent_id for routing incoming WebSocket messages
  const jobAgentMap = useRef<Record<string, string>>({})

  // Load agents from REST API
  useEffect(() => {
    fetch('/api/agents')
      .then((r) => r.json())
      .then((data: AgentConfig[]) => {
        setAgents(data)
        if (data.length > 0) setActiveAgentId(data[0].id)
      })
      .catch(console.error)
  }, [])

  const handleWsMessage = useCallback((raw: unknown) => {
    const msg = raw as WSIncoming

    if (msg.type === 'job_update') {
      const agentId = msg.agent_id
      jobAgentMap.current[msg.job_id] = agentId

      setSessions((prev) => {
        const existing = prev[agentId] ?? emptySession('')
        const existingJob = existing.jobs.find((j) => j.job_id === msg.job_id)

        let updatedJobs: Job[]
        if (!existingJob) {
          updatedJobs = [
            ...existing.jobs,
            {
              job_id: msg.job_id,
              agent_id: agentId,
              title: msg.title ?? '',
              status: msg.status,
              progress: msg.progress,
            },
          ]
        } else {
          updatedJobs = existing.jobs.map((j) =>
            j.job_id === msg.job_id ? { ...j, status: msg.status, progress: msg.progress } : j,
          )
        }

        // When a job completes, move its streaming text to messages
        let updatedMessages = existing.messages
        let updatedStreaming = existing.streamingMessages
        if (msg.status === 'completed' && existing.streamingMessages[msg.job_id]) {
          const text = existing.streamingMessages[msg.job_id]
          updatedMessages = [
            ...existing.messages,
            { id: nextId(), role: 'assistant' as const, content: text },
          ]
          const { [msg.job_id]: _, ...rest } = existing.streamingMessages
          updatedStreaming = rest
        }

        return {
          ...prev,
          [agentId]: {
            ...existing,
            messages: updatedMessages,
            streamingMessages: updatedStreaming,
            jobs: updatedJobs,
          },
        }
      })
    } else if (msg.type === 'token') {
      const agentId = jobAgentMap.current[msg.job_id]
      if (!agentId) return

      setSessions((prev) => {
        const existing = prev[agentId] ?? emptySession('')
        return {
          ...prev,
          [agentId]: {
            ...existing,
            streamingMessages: {
              ...existing.streamingMessages,
              [msg.job_id]: (existing.streamingMessages[msg.job_id] ?? '') + msg.delta,
            },
          },
        }
      })
    } else if (msg.type === 'usage') {
      const agentId = jobAgentMap.current[msg.job_id]
      if (!agentId) return

      setSessions((prev) => {
        const existing = prev[agentId] ?? emptySession('')
        return {
          ...prev,
          [agentId]: {
            ...existing,
            input_tokens: msg.input_tokens,
            output_tokens: msg.output_tokens,
            cost_usd: msg.cost_usd,
          },
        }
      })
    } else if (msg.type === 'error') {
      const agentId = jobAgentMap.current[msg.job_id]
      if (!agentId) return

      setSessions((prev) => {
        const existing = prev[agentId] ?? emptySession('')
        return {
          ...prev,
          [agentId]: {
            ...existing,
            messages: [
              ...existing.messages,
              {
                id: nextId(),
                role: 'assistant' as const,
                content: `⚠ Error: ${msg.message}`,
              },
            ],
          },
        }
      })
    }
  }, [])

  // Connect WebSocket
  useEffect(() => {
    wsRef.current = new WsClient(handleWsMessage)
    return () => wsRef.current?.destroy()
  }, [handleWsMessage])

  function handleSend(text: string) {
    if (!activeAgentId) return

    // Optimistically add user message
    setSessions((prev) => {
      const existing = prev[activeAgentId] ?? emptySession('')
      return {
        ...prev,
        [activeAgentId]: {
          ...existing,
          messages: [...existing.messages, { id: nextId(), role: 'user', content: text }],
        },
      }
    })

    wsRef.current?.send({ type: 'chat', agent_id: activeAgentId, text })
  }

  async function handleSaveAgent(config: AgentConfig) {
    const isEdit = agents.some((a) => a.id === config.id)
    const url = isEdit ? `/api/agents/${config.id}` : '/api/agents'
    const method = isEdit ? 'PUT' : 'POST'

    const res = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config),
    })
    if (!res.ok) return

    const saved: AgentConfig = await res.json()
    setAgents((prev) =>
      isEdit ? prev.map((a) => (a.id === saved.id ? saved : a)) : [...prev, saved],
    )
    setShowConfig(false)
    setEditingAgent(null)
    setActiveAgentId(saved.id)
  }

  const activeSession = activeAgentId ? (sessions[activeAgentId] ?? emptySession('')) : null
  const activeAgent = agents.find((a) => a.id === activeAgentId)

  return (
    <div className="flex h-screen overflow-hidden">
      <AgentSidebar
        agents={agents}
        activeAgentId={activeAgentId}
        onSelect={(id) => {
          setActiveAgentId(id)
          setShowConfig(false)
          setEditingAgent(null)
        }}
        onNewAgent={() => {
          setShowConfig(true)
          setEditingAgent(null)
        }}
      />

      <div className="flex flex-col flex-1 min-w-0">
        {showConfig || editingAgent ? (
          <AgentConfigPanel
            agents={agents}
            editingAgent={editingAgent}
            onSave={handleSaveAgent}
            onCancel={() => {
              setShowConfig(false)
              setEditingAgent(null)
            }}
          />
        ) : activeAgent && activeSession ? (
          <>
            <SessionCostBar
              model={activeAgent.model}
              inputTokens={activeSession.input_tokens}
              outputTokens={activeSession.output_tokens}
              costUsd={activeSession.cost_usd}
            />
            <div className="flex flex-1 min-h-0">
              <ChatPanel
                messages={activeSession.messages}
                streamingMessages={activeSession.streamingMessages}
                onSend={handleSend}
              />
              <JobsPanel jobs={activeSession.jobs} />
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center text-gray-600 text-sm">
            Select an agent to start chatting
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add ui/src/App.tsx
git commit -m "feat: assemble App with WebSocket state routing and agent management"
```

---

## Task 15: Verify Full Stack + Run Instructions

**Files:**
- No new files — verify everything works together

- [ ] **Step 1: Start backend**

In terminal 1:
```bash
ANTHROPIC_API_KEY=sk-ant-... uvicorn harness_claw.server:app --reload --port 8000
```

Expected: Server starts, logs `Uvicorn running on http://127.0.0.1:8000`.

- [ ] **Step 2: Start frontend dev server**

In terminal 2:
```bash
cd ui && npm run dev
```

Expected: Vite dev server starts at `http://localhost:5173`.

- [ ] **Step 3: Open the UI and verify**

Open `http://localhost:5173` in your browser.

- [ ] Agents from `agents.yaml` appear in the sidebar
- [ ] Clicking an agent selects it, shows the chat area
- [ ] Typing a message and pressing Enter sends it; tokens stream back
- [ ] `SessionCostBar` updates with token count and cost after the response
- [ ] Jobs panel shows the job as "Running" then "Done"
- [ ] Clicking "+ New Agent" shows the config panel
- [ ] Creating an agent from the config panel adds it to the sidebar

- [ ] **Step 4: Test orchestrator**

Select the "Coordinator" agent (which orchestrates code-writer and reviewer). Send a message like:

```
Write a Python function that validates email addresses
```

Expected:
- Coordinator starts streaming
- Two child jobs appear in the jobs panel (one for code-writer, one for reviewer)
- Both stream their tokens under the coordinator conversation
- Final coordinator synthesis appears after both sub-agents complete

- [ ] **Step 5: Run all backend tests**

```bash
pytest tests/ -v
```

Expected: All tests PASS.

- [ ] **Step 6: Build frontend for production (optional)**

```bash
cd ui && npm run build
```

Expected: `ui/dist/` created. Restart the backend — `http://localhost:8000` now serves the full app (no separate Vite server needed).

- [ ] **Step 7: Final commit**

```bash
git add -A
git commit -m "feat: complete AI gateway UI — FastAPI backend + React frontend with orchestrator support"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| FastAPI backend, single process | Task 8 |
| BaseProvider abstraction | Task 4 |
| Anthropic provider (streaming) | Task 5 |
| Provider tool-use loop (orchestrators) | Task 5 |
| AgentRegistry + agents.yaml | Task 6 |
| JobRunner with async tasks | Task 7 |
| Orchestrator dispatches sub-agents | Task 7 |
| WebSocket streaming protocol | Task 8 |
| REST endpoints (CRUD agents, sessions) | Task 8 |
| Pricing + per-session cost | Task 2, 3 |
| React/Vite/Tailwind scaffold | Task 9 |
| WebSocket client (auto-reconnect) | Task 10 |
| AgentSidebar (sub-agents indented) | Task 11 |
| SessionCostBar (live cost) | Task 11 |
| ChatPanel (streaming, tool-call cards) | Task 12 |
| JobsPanel (status + progress) | Task 12 |
| AgentConfigPanel (create/edit) | Task 13 |
| App state routing by job_id/agent_id | Task 14 |
| Session in-memory only | Task 7 (no persistence) |
| Unit tests (pricing, session) | Tasks 2, 3 |
| Integration test (job_runner + mock) | Task 7 |

All spec requirements are covered. No placeholders or TODOs remain.
