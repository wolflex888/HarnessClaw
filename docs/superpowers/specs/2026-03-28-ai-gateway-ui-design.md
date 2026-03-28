# AI Gateway UI — Design Spec

**Date:** 2026-03-28
**Project:** HarnessClaw

---

## Overview

A locally-run multi-agent dashboard built with Python (FastAPI) and React. Users can manage multiple AI agents, chat with them in real time, dispatch long-running jobs, and coordinate agents via an orchestrator pattern. Session costs are tracked live per agent.

---

## Architecture

### Backend (Python / FastAPI)

Single FastAPI process. Agents run as `asyncio` tasks. WebSockets handle real-time streaming.

```
harness_claw/
├── server.py            # FastAPI app, WebSocket endpoint (/ws), REST routes
├── agent_registry.py    # Loads agents from agents.yaml + manages runtime instances
├── job_runner.py        # asyncio task manager; handles nested orchestrator jobs
├── session.py           # Session state: messages, input_tokens, output_tokens, cost_usd
├── pricing.py           # Static dict: model → (input_price_per_m, output_price_per_m)
├── providers/
│   ├── base.py          # BaseProvider ABC: chat(), call_with_tools()
│   └── anthropic.py     # Anthropic Claude implementation
└── model.py             # (existing — ignored for this feature)
```

### Frontend (React + Vite)

```
ui/
├── src/
│   ├── App.tsx
│   ├── components/
│   │   ├── AgentSidebar.tsx       # Agent list; sub-agents indented under orchestrators
│   │   ├── ChatPanel.tsx          # Message thread with streaming token rendering
│   │   ├── JobsPanel.tsx          # Per-agent job list with status + progress
│   │   ├── SessionCostBar.tsx     # Live token count + cost in USD in chat header
│   │   └── AgentConfigPanel.tsx   # Create/edit agent form (replaces chat area)
│   └── ws.ts                      # WebSocket client with auto-reconnect
└── vite.config.ts                 # Proxies /ws and /api to FastAPI in dev
```

### Agent Configuration (agents.yaml)

Agents are defined in `agents.yaml` at the project root. Additional agents can be created at runtime via the UI.

```yaml
agents:
  - id: code-writer
    name: Code Writer
    provider: anthropic
    model: claude-sonnet-4-6
    system_prompt: "You write clean, well-tested Python code."

  - id: reviewer
    name: Reviewer
    provider: anthropic
    model: claude-sonnet-4-6
    system_prompt: "You review code for correctness, clarity, and security."

  - id: coordinator
    name: Coordinator
    provider: anthropic
    model: claude-sonnet-4-6
    orchestrates: [code-writer, reviewer]
    system_prompt: |
      You coordinate the code writer and reviewer.
      Use the call_agent tool to delegate tasks to sub-agents.
```

---

## UI Layout

### Global Layout

```
┌─────────────────────────────────────────────────┐
│  Sidebar (200px)  │  Main Area (flex)            │
│                   │                              │
│  AGENTS           │  [ChatPanel + JobsPanel]     │
│  ⬡ Coordinator   │   OR                         │
│    ↳ Code Writer  │  [AgentConfigPanel]          │
│    ↳ Reviewer     │                              │
│  ○ Researcher     │                              │
│                   │                              │
│  [+ New Agent]    │                              │
└─────────────────────────────────────────────────┘
```

### Chat + Jobs View (active agent selected)

```
┌─────────────────────────────────────┬────────────┐
│  Agent Name              $0.012 · X tokens       │
├─────────────────────────────────────┼────────────┤
│                                     │  JOBS      │
│  [user message]                     │  ┌───────┐ │
│                                     │  │Job 1  │ │
│  ┌──────────────────────┐           │  │● Run  │ │
│  │ → Calling: SubAgent  │           │  │████░░ │ │
│  │ Dispatching task...  │           │  └───────┘ │
│  └──────────────────────┘           │  ┌───────┐ │
│                                     │  │Job 2  │ │
│  [agent streaming response...]█     │  │◌ Wait │ │
│                                     │  └───────┘ │
├─────────────────────────────────────┴────────────┤
│  [Message input...]                      [Send]  │
└──────────────────────────────────────────────────┘
```

### Agent Config Panel (create/edit)

Replaces the main area when "+ New Agent" is clicked or an agent's settings are opened.

Fields: name, model (dropdown), system prompt (textarea), max tokens, provider, `orchestrates` (multi-select of existing agents).

---

## WebSocket Protocol

All messages are JSON. The single endpoint is `/ws`.

**Client → Server:**

| type | fields | description |
|------|--------|-------------|
| `chat` | `agent_id`, `text` | Send a message to an agent |
| `cancel` | `job_id` | Cancel a running job |

**Server → Client:**

| type | fields | description |
|------|--------|-------------|
| `token` | `job_id`, `delta` | Streaming token chunk |
| `job_update` | `job_id`, `status`, `progress` | Job status change (queued/running/completed/failed). `progress` is `null` for open-ended chat; `0–100` for orchestrator jobs with known steps. |
| `tool_call` | `job_id`, `tool_name`, `input` | Orchestrator dispatching to a sub-agent |
| `usage` | `job_id`, `input_tokens`, `output_tokens`, `cost_usd` | Token usage update |
| `error` | `job_id`, `message` | Error surfaced in chat thread |

---

## Provider Abstraction

```python
class BaseProvider(ABC):
    @abstractmethod
    async def chat(self, messages: list[dict], system: str, model: str, max_tokens: int) -> AsyncIterator[dict]:
        """Stream token deltas and usage events."""

    @abstractmethod
    async def call_with_tools(self, messages: list[dict], system: str, model: str, tools: list[dict], max_tokens: int) -> AsyncIterator[dict]:
        """Stream token deltas, tool_use blocks, and usage events. Used by orchestrators."""
```

`anthropic.py` maps Anthropic's streaming API to this interface. Future providers (OpenAI, Ollama) implement the same interface. Models that don't support tool calling can be used as sub-agents but not as orchestrators.

---

## Orchestrator Pattern

When a coordinator agent is active:

1. `job_runner` calls `provider.call_with_tools()` with the sub-agents exposed as tools via a `call_agent(agent_id, prompt)` tool definition.
2. When Claude returns a `tool_use` block, `job_runner` dispatches a child job to the target sub-agent's provider.
3. The child job streams its output back; its result is returned to the orchestrator as a `tool_result` message.
4. The orchestrator continues — potentially calling more sub-agents or synthesizing a final response.
5. Each `tool_use` dispatch is surfaced in the chat as an inline `→ Calling: SubAgent` card.
6. Sub-agents appear indented under their coordinator in the sidebar.

---

## Session & Pricing

`session.py` maintains per-session state. Sessions are **in-memory only** — they are cleared when the server restarts. Persistence to disk is out of scope for this version.


```python
class Session:
    session_id: str
    agent_id: str
    messages: list[dict]        # full message history
    input_tokens: int = 0
    output_tokens: int = 0
    model: str

    @property
    def cost_usd(self) -> float:
        input_price, output_price = PRICING[self.model]
        return (self.input_tokens / 1_000_000) * input_price + \
               (self.output_tokens / 1_000_000) * output_price
```

`pricing.py` holds a static dict:

```python
PRICING = {
    "claude-sonnet-4-6": (3.00, 15.00),   # (input $/M, output $/M)
    "claude-haiku-4-5":  (0.80,  4.00),
    "claude-opus-4-6":   (15.00, 75.00),
}
```

The `SessionCostBar` component updates on every `usage` WebSocket event.

---

## REST Endpoints

| method | path | description |
|--------|------|-------------|
| `GET` | `/api/agents` | List all agents (config + runtime) |
| `POST` | `/api/agents` | Create a new agent at runtime |
| `PUT` | `/api/agents/{id}` | Update agent config |
| `DELETE` | `/api/agents/{id}` | Remove a runtime agent |
| `GET` | `/api/sessions/{agent_id}` | Get session history + cost for an agent |

---

## Error Handling

- **API errors** (rate limits, auth failures): surfaced as a red error message in the chat thread; job marked `failed`.
- **WebSocket disconnect**: client auto-reconnects with exponential backoff; session state preserved server-side.
- **Sub-agent failure mid-orchestration**: coordinator receives the error as a `tool_result` with `is_error: true`; it decides whether to retry or surface the failure.
- **Invalid agent config**: validated with Pydantic on `POST /api/agents`; errors returned as 422 with field-level messages.

---

## Testing

- Unit tests for `pricing.py` (cost calculation correctness) and `session.py` (token accumulation, cost property).
- Integration test: full WebSocket chat round-trip using a mocked `BaseProvider` that returns canned streaming chunks.
- Manual testing in the browser for UI interactions — no E2E browser tests at this stage.

---

## Tech Stack Summary

| layer | technology |
|-------|-----------|
| Backend | Python 3.12, FastAPI, uvicorn, pydantic, anthropic SDK |
| Frontend | React 18, TypeScript, Vite |
| Styling | Tailwind CSS |
| Config | PyYAML |
| Testing | pytest, pytest-asyncio |
