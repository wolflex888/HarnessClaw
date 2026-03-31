# HarnessClaw: Agent Operating System — Phase 1 Design

**Date:** 2026-03-31
**Scope:** Phase 1 — Gateway-first restructure with auth, policy, capability registry, MCP interface, task delegation, and memory
**Status:** Approved

---

## Overview

HarnessClaw evolves from a multi-agent dashboard into an **operating system for agents**. The gateway becomes the kernel — every component (dashboard, PTY agents, future external callers) is a client that connects to it. Nothing gets special treatment.

The project is decomposed into three phases, each with its own spec → plan → implementation cycle:

- **Phase 1 (this spec):** Gateway — auth, policy, capability registry, MCP interface, task delegation, memory
- **Phase 2:** Inter-agent communication — task delegation with callbacks, capability-based routing at scale
- **Phase 3:** Resource management — scheduling, prioritization, observability, load balancing

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                  HarnessClaw Gateway                  │
│                                                       │
│  ┌─────────────┐  ┌───────────────┐  ┌────────────┐  │
│  │    Auth &   │  │  Capability   │  │   Audit    │  │
│  │   Policy    │  │   Registry    │  │    Log     │  │
│  └─────────────┘  └───────────────┘  └────────────┘  │
│                                                       │
│  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │     Broker       │  │      Memory Store        │  │
│  │ (routing, tasks) │  │  (working/long-term)     │  │
│  └──────────────────┘  └──────────────────────────┘  │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │                Runtime Layer                     │  │
│  │   JobRunner · PtySession · CostPoller            │  │
│  └─────────────────────────────────────────────────┘  │
│                                                       │
│  Interfaces:                                          │
│    /mcp          ← Claude Code agents (MCP tools)     │
│    /ws/terminal  ← Dashboard terminal I/O (xterm)     │
│    /api/*        ← Dashboard REST (sessions, roles)   │
│    /gateway/*    ← Future external callers (Phase D)  │
└──────────────────────────────────────────────────────┘
```

**The core shift:** The current `server.py` is dashboard-centric — sessions and WebSocket exist to serve the UI. In the new structure, the dashboard uses the same gateway interfaces any other client would. PTY sessions are runtime processes managed by the runtime layer, not the server directly.

---

## File Structure

```
harness_claw/
  gateway/
    __init__.py
    auth.py           # Token issuance & scope validation
    policy.py         # PolicyEngine protocol + LocalPolicyEngine
    capability.py     # CapabilityConnector protocol + LocalConnector + GatewayConnector
    broker.py         # TaskDispatcher protocol + LocalDispatcher + task lifecycle
    audit.py          # AuditEvent log (JSONL, pluggable)
    mcp_server.py     # MCP endpoint: exposes gateway tools to Claude Code agents
    memory.py         # MemoryStore protocol + SqliteMemoryStore
  runtime/
    __init__.py
    job_runner.py     # (refactored) PTY lifecycle management
    pty_session.py    # (existing, unchanged)
    cost_poller.py    # (existing, unchanged)
    session_store.py  # (existing, unchanged)
  api/
    __init__.py
    sessions.py       # /api/sessions routes
    roles.py          # /api/roles routes
    websocket.py      # /ws/terminal route
  model.py            # (existing + Phase 1 additions)
  role_registry.py    # (existing, unchanged)
  session.py          # (existing, unchanged)
  pricing.py          # (existing, unchanged)
  cli.py              # (existing, unchanged)
  server.py           # FastAPI app assembly
tests/
  gateway/
    test_auth.py
    test_policy.py
    test_capability.py
    test_broker.py
    test_mcp.py
    test_memory.py
  runtime/
    test_pty_session.py    # moved from tests/
    test_cost_poller.py    # moved from tests/
    test_session_store.py  # moved from tests/
    test_session.py        # moved from tests/
    test_job_runner.py     # moved from tests/
  api/
    test_sessions.py
    test_roles.py
```

---

## Auth & Policy

### Tokens and Scopes

When a PTY session starts, HarnessClaw issues a scoped token and injects it as `HARNESS_TOKEN` into the `claude` process environment. The token's scopes are derived from the role definition in `agents.yaml`.

**Scopes:**
```
agent:list          # query the capability registry
agent:delegate      # delegate tasks to other agents + check their status
agent:spawn         # spawn new agent sessions
agent:report        # report progress/completion on tasks assigned to this agent (all agents get this by default)
memory:read         # read from memory namespaces
memory:write        # write to memory namespaces
audit:read          # read audit log (admin roles only)
```

**Role definition in `agents.yaml`:**
```yaml
roles:
  - id: orchestrator
    name: Orchestrator
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You orchestrate other agents to complete complex tasks."
    max_tokens: 8192
    scopes: [agent:list, agent:delegate, agent:spawn, memory:read, memory:write]
    caps: [orchestration, planning]

  - id: code-writer
    name: Code Writer
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You write clean, well-tested code."
    max_tokens: 8192
    scopes: [agent:list, memory:read, memory:write]
    caps: [python, typescript, testing]
```

**Token storage:** Phase 1 tokens are in-memory — issued at session start, revoked at session end. `auth.py` is the only place tokens are created or validated.

### Policy Engine (Pluggable)

Every gateway operation passes through the policy engine before executing. The engine is a pluggable connector — the rest of the system calls one function:

```python
class PolicyEngine(Protocol):
    async def check(self, subject: str, operation: str, resource: str) -> PolicyDecision: ...

class LocalPolicyEngine:
    """Scope-based policy. Phase 1 default."""
    ...

class OPAPolicyEngine:
    """Delegates to an external OPA server. Phase N optional."""
    ...
```

The active engine is configured in `agents.yaml`:
```yaml
policy:
  engine: local          # local | opa
  # opa_url: http://...  # only for opa engine
```

`PolicyDecision` (already in `model.py`): `allowed: bool`, `reason: str | None`, `metadata: dict`.

---

## Capability Registry

Agents advertise what they can do. The registry is the gateway's service discovery layer.

### CapabilityConnector Protocol

```python
class CapabilityConnector(Protocol):
    async def register(self, agent: AgentAdvertisement) -> None: ...
    async def deregister(self, session_id: str) -> None: ...
    async def query(self, caps: list[str]) -> list[AgentAdvertisement]: ...
```

**Built-in connectors:**

- **`LocalConnector`** — tracks HarnessClaw's own PTY sessions. Registered automatically when a session starts (using the role's `caps`), deregistered on kill. Always enabled.
- **`GatewayConnector`** — external agents self-register via `POST /gateway/agents/register` with a token + capability advertisement. They heartbeat to stay alive; missing heartbeats auto-deregister. External agents obtain a token via a pre-shared bootstrap token configured in `agents.yaml` under `connectors[gateway].bootstrap_token`.

Third parties implement a `CapabilityConnector` to bridge their own agent runtimes (LangGraph, CrewAI, custom Python agents). Those agents appear in the registry alongside native HarnessClaw sessions.

**AgentAdvertisement:**
```python
@dataclass
class AgentAdvertisement:
    session_id: str
    role_id: str
    caps: list[str]          # e.g. ["python", "typescript", "testing"]
    status: str              # idle | busy | killed
    task_count: int          # current in-flight tasks
    connector: str           # "local" | "gateway" | custom name
```

**Matching:** set-intersection — find all agents whose `caps` contain all requested capabilities. Broker picks the least-loaded match (lowest `task_count`). Phase 3 makes this smarter.

**`agents.yaml` connector config:**
```yaml
connectors:
  - type: local              # always on
  - type: gateway            # enables external self-registration
    heartbeat_ttl: 30s
    bootstrap_token: "changeme"   # external agents use this to obtain a session token
```

---

## MCP Interface

The MCP server at `/mcp` is how Claude Code agents interact with the gateway. When `JobRunner` starts a PTY session it:

1. Issues a scoped token for that session
2. Launches the `claude` process with `--mcp-server http://localhost:8000/mcp` and `HARNESS_TOKEN=<token>` in the environment
3. Every MCP tool call is authenticated against the token's scopes before executing

The MCP endpoint speaks the standard MCP protocol over HTTP (SSE + POST), so any MCP-compatible client can connect in the future.

### MCP Tools

**Agent tools:**

| Tool | Scope | Description |
|------|-------|-------------|
| `agent.list` | `agent:list` | List agents in registry, optionally filtered by caps |
| `agent.delegate` | `agent:delegate` | Delegate a task to the best-matched agent; returns task_id |
| `agent.status` | `agent:delegate` | Check status + progress of a delegated task |
| `agent.spawn` | `agent:spawn` | Spawn a new agent session with a given role |
| `agent.progress` | `agent:report` | Report progress on current task (message + % complete) |
| `agent.complete` | `agent:report` | Signal task completion with a result payload |

**Memory tools:**

| Tool | Scope | Description |
|------|-------|-------------|
| `memory.namespaces` | `memory:read` | List all namespaces the agent has access to |
| `memory.list` | `memory:read` | List keys + metadata within a namespace |
| `memory.get` | `memory:read` | Load a specific entry into context |
| `memory.search` | `memory:read` | Full-text search across namespaces, returns ranked matches |
| `memory.set` | `memory:write` | Write a value to a namespace |
| `memory.delete` | `memory:write` | Delete a key |
| `memory.tag` | `memory:write` | Add tags to an entry |

### Memory is Agent-Driven (Pull, Not Push)

Agents decide what memory to load — the system does not auto-inject. A typical workflow:

```
[agent.memory.namespaces]
→ project:harnessclaw, task:abc-456, global

[agent.memory.search namespace=project:harnessclaw query="auth design"]
→ 3 matches: "auth-design-notes" (summary: "token scope decisions"), ...

[agent.memory.get namespace=project:harnessclaw key="auth-design-notes"]
→ [full content loaded into context]
```

---

## Broker & Task Delegation

### Task Lifecycle

```
1. Agent calls agent.delegate(caps, instructions) via MCP
2. MCP server authenticates token → extracts subject + scopes
3. Policy engine checks: does this subject have agent:delegate?
4. Broker creates Task record, queries CapabilityRegistry for best match
5. TaskDispatcher delivers instructions to target agent
6. Target agent calls agent.progress / agent.complete via MCP as it works
7. Broker updates Task record, broadcasts task.updated via WebSocket
8. Dashboard TasksTab receives events, updates live
9. Originating agent receives task.completed event
```

### Task Record

```python
@dataclass
class Task:
    task_id: str
    delegated_by: str        # session_id of orchestrator
    delegated_to: str        # session_id of subagent
    instructions: str
    caps_requested: list[str]
    status: str              # queued | running | completed | failed
    progress_pct: int        # 0–100
    progress_msg: str
    result: str | None
    created_at: datetime
    updated_at: datetime
```

Phase 1: in-memory. Phase N: pluggable persistent store (Redis, Postgres) required for remote dispatch.

### TaskDispatcher (Pluggable)

```python
class TaskDispatcher(Protocol):
    async def dispatch(self, task: Task, agent: AgentAdvertisement) -> None: ...
    async def cancel(self, task_id: str) -> None: ...
```

**Built-in dispatchers:**

- **`LocalDispatcher`** — writes instructions directly to the target agent's PTY terminal input, prefixed with the task_id so the subagent can reference it in `agent.progress` and `agent.complete` calls. Phase 1 default.
- **`QueueDispatcher`** — pushes tasks to a message queue (Redis, RabbitMQ, SQS). Containerized agents pull tasks, execute, and report back via `GatewayConnector`. Phase N.

**Remote/containerized agent workflow:**
```
HarnessClaw Gateway
  └─ QueueDispatcher → [message queue] → containerized agent
                                              │ registers via GatewayConnector
                                              │ pulls task
                                              │ calls agent.progress / agent.complete
                                              └─→ Gateway → Dashboard
```

**`agents.yaml` broker config:**
```yaml
broker:
  dispatcher: local          # local | queue
  # queue_url: redis://...   # only for queue dispatcher
```

### WebSocket Task Events

| Event | Payload | Recipients |
|-------|---------|------------|
| `task.created` | full task record | all dashboard clients |
| `task.updated` | task_id, status, progress_pct, progress_msg | all dashboard clients |
| `task.completed` | task_id, result | all dashboard clients + orchestrator agent |

---

## Memory Store

### Three-Tier Memory Hierarchy

| Tier | Lifetime | Scope | Use case |
|------|----------|-------|----------|
| Working memory | Task lifetime | task-scoped | Shared scratchpad between orchestrator and subagents |
| Session memory | Session lifetime | agent-scoped | Claude Code JSONL conversation history (already exists) |
| Long-term memory | Persistent | namespace-scoped | Knowledge, decisions, artifacts across sessions |

### Namespaces

```
task:<task_id>        # working memory, auto-deleted when task ends
project:<name>        # long-term, shared across all agents in a project
agent:<session_id>    # private to one agent
global                # shared across everything (requires admin scope)
```

### MemoryStore Protocol (Pluggable)

```python
class MemoryStore(Protocol):
    async def set(self, namespace: str, key: str, value: str, summary: str | None, tags: list[str]) -> None: ...
    async def get(self, namespace: str, key: str) -> MemoryEntry | None: ...
    async def list(self, namespace: str) -> list[MemoryEntry]: ...
    async def search(self, namespace: str, query: str) -> list[MemoryEntry]: ...
    async def delete(self, namespace: str, key: str) -> None: ...
    async def namespaces(self, subject: str) -> list[str]: ...
```

**Built-in stores:**
- **`SqliteMemoryStore`** — Phase 1 default. Full-text search via SQLite FTS5. Zero extra dependencies.
- **`PostgresMemoryStore`**, **`ChromaMemoryStore`** (vector/semantic search), **`RedisMemoryStore`** — Phase N.

**`agents.yaml` memory config:**
```yaml
memory:
  backend: sqlite            # sqlite | postgres | chroma | redis
  path: ./memory.db          # sqlite only
```

### MemoryEntry

```python
@dataclass
class MemoryEntry:
    namespace: str
    key: str
    value: str
    summary: str | None      # agent-written 1-line description
    tags: list[str]
    size_bytes: int
    created_at: datetime
    updated_at: datetime
```

The `summary` field lets agents make loading decisions without paying the context cost of loading the full value first.

---

## Error Handling

Every gateway operation returns a structured error using `ErrorBody` from `model.py` (`code`, `message`, `details`). All errors are written to the audit log.

| Code | Meaning |
|------|---------|
| `auth.invalid_token` | Token missing or expired |
| `auth.missing_token` | No token provided |
| `policy.denied` | Scope check failed |
| `registry.no_match` | No agent matches requested caps |
| `registry.no_capacity` | Matching agents exist but all busy |
| `task.not_found` | Unknown task ID |
| `task.not_owner` | Agent tried to complete a task it didn't receive |
| `dispatch.failed` | Dispatcher could not deliver task |
| `memory.not_found` | Key does not exist in namespace |
| `memory.access_denied` | Agent does not have access to this namespace |

---

## Audit Log

Every significant gateway event is recorded as an append-only structured log.

```python
@dataclass
class AuditEvent:
    event_id: str
    timestamp: datetime
    subject: str             # session_id or "system"
    operation: str           # e.g. "agent.delegate", "memory.set"
    resource: str            # e.g. task_id, namespace/key
    outcome: str             # "allowed" | "denied" | "error"
    details: dict
```

**Phase 1:** Append to `audit.jsonl` (simple, zero dependencies, survives restarts).
**Phase N:** Pluggable backend — same connector pattern — for database or log aggregator.

Dashboard exposes `GET /api/audit` — filterable by subject, operation, and outcome.

---

## Dashboard Changes

### TasksTab (currently empty)

The `TasksTab` becomes a live task board. Tasks are expandable — clicking a task opens an inline detail panel:

```
┌─────────────────────────────────────────────────────────────────┐
│ Tasks                                                           │
├──────────┬──────────────┬──────────┬──────────────────────────┤
│ Task ID  │ Delegated to │ Progress │ Status                   │
├──────────┼──────────────┼──────────┼──────────────────────────┤
│ ▶ abc-456│ code-writer  │ ████░ 80%│ running                  │
│ ▼ abc-457│ reviewer     │ ██░░░ 40%│ running        [collapse]│
│          │                                                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ reviewer · abc-457 · delegated by orchestrator · 40%    │   │
│  ├─────────────────────────────────────────────────────────┤   │
│  │ Reading src/auth.py...                                  │   │
│  │ Found 3 issues:                                         │   │
│  │ 1. Token not invalidated on logout                      │   │
│  │ ▌                                                       │   │
│  └─────────────────────────────────────────────────────────┘   │
│ ▶ abc-458│ code-writer  │ █████100%│ ✓ completed              │
└──────────┴──────────────┴──────────┴──────────────────────────┘
```

The expanded panel embeds a **read-only xterm instance** subscribed to that subagent's output stream — reusing existing terminal infrastructure. Multiple tasks can be expanded simultaneously. Completed tasks show a result summary at the bottom.

### Memory Tab (new)

A new **Memory** tab in the dashboard where users can:
- Browse namespaces
- Inspect stored values and metadata
- Delete entries manually

---

## Testing Strategy

All gateway components are tested with `LocalPolicyEngine`, `LocalConnector`, and `LocalDispatcher` — no external dependencies required.

| File | Coverage |
|------|----------|
| `tests/gateway/test_auth.py` | Token issuance, scope validation, expiry, revocation |
| `tests/gateway/test_policy.py` | LocalPolicyEngine allow/deny, PolicyEngine interface contract |
| `tests/gateway/test_capability.py` | Register, deregister, query, least-loaded matching, connector interface |
| `tests/gateway/test_broker.py` | Task lifecycle, dispatch, progress, completion, cancellation |
| `tests/gateway/test_mcp.py` | Each MCP tool call, auth failure, scope denial |
| `tests/gateway/test_memory.py` | All MemoryStore operations, namespace scoping, FTS search |
| `tests/runtime/` | Existing tests moved, unchanged |
| `tests/api/` | Session and role routes updated for new structure |

---

## Files Replaced by This Restructure

The following top-level stubs are replaced by the new `gateway/` package:
- `harness_claw/gateway_api.py` → deleted (superseded by `harness_claw/gateway/`)
- `harness_claw/audit.py` → deleted (superseded by `harness_claw/gateway/audit.py`)

---

## What Does Not Change

- `pty_session.py` — unchanged
- `cost_poller.py` — unchanged
- `session_store.py` — unchanged
- `session.py` — unchanged
- `role_registry.py` — unchanged
- `pricing.py` — unchanged
- `cli.py` — unchanged
- Frontend xterm terminal rendering — unchanged
- Session sidebar, cost bar, agent tab — unchanged

---

## Out of Scope (Phase 2+)

- Inter-agent pub/sub messaging beyond task delegation
- Capability-based routing at scale / smart load balancing
- Container orchestration for remote agents
- Vector/semantic memory search
- External policy engine integration (OPA, Cedar)
- Persistent task store (Redis, Postgres)
- `audit:read` dashboard viewer beyond basic list
