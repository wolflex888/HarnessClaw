# HarnessClaw

A local agent orchestration platform. Run multiple Claude Code sessions, delegate tasks between agents, and observe everything through a live dashboard.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Node 18+
- [Claude Code CLI](https://claude.ai/code) installed and authenticated

## Setup

```bash
# Install Python dependencies
uv sync

# Install and build frontend
cd ui && npm install && npm run build && cd ..
```

## Run

```bash
uv run harnessclaw run
```

Dashboard: http://localhost:8000

## What it does

HarnessClaw is a gateway that sits between you and your Claude agents. It manages sessions, routes tasks, enforces capability policies, and persists state — so agents can hand work to each other without you manually copying context between terminals.

**Core capabilities:**

- **PTY-based Claude Code sessions** — each agent runs in a real terminal; sessions survive tab switches via persistent xterm.js instances
- **Task delegation** — agents call `agent.delegate` to hand tasks to capability-matched peers; tasks are tracked and persisted to SQLite with 7-day expiry and restart recovery
- **EventBus callbacks** — delegating agents can request a callback when a task completes; the result is injected back into their terminal
- **Declarative workflows** — multi-step pipelines defined in `agents.yaml`; the gateway drives step progression automatically via task completion events
- **Persistent memory** — agents share a namespaced SQLite memory store with hybrid FTS5 + vector search
- **Audit trail** — every MCP tool call is logged with subject, outcome, and details
- **External agent registration** — agents outside the local process can register via bootstrap token and receive delegated tasks over HTTP

## Architecture

```
agents.yaml          ← role definitions, gateway config, workflow definitions
     │
     ▼
RoleRegistry         ← parses roles, capabilities, workflows
     │
     ├── Broker           ← routes agent.delegate calls, tracks tasks in SQLite
     │     └── LocalDispatcher  ← writes task payloads to PTY sessions
     │
     ├── EventBus         ← pub/sub for task completion callbacks
     │
     ├── WorkflowEngine   ← drives multi-step workflows via EventBus events
     │
     ├── GatewayMCP       ← MCP tool implementations (agent.*, memory.*, workflow.*)
     │
     ├── SqliteMemoryStore ← namespaced memory with FTS5 + vector search
     ├── SqliteTaskStore   ← task persistence, 7-day expiry, restart recovery
     └── WorkflowRunStore  ← workflow run state persistence
```

The FastAPI server exposes:
- `/mcp/tools/call` — MCP tool execution with token auth and audit logging
- `/api/sessions` — CRUD for Claude Code sessions
- `/api/tasks` — task list and retry
- `/api/workflows` — workflow definitions and run history
- `/api/memory` — memory namespaces and entries
- `/api/audit` — audit log
- WebSocket `/ws` — real-time terminal output, session status, task and workflow events

## Dashboard tabs

| Tab | What it shows |
|-----|---------------|
| Work | PTY terminal for the active agent session |
| Tasks | All delegated tasks with status, progress, results, retry button |
| Agent | Active session info (model, role, tools) |
| Tools | All available MCP tools |
| Memory | Browse and delete memory entries by namespace |
| Audit | Recent MCP tool call audit log |
| Workflows | Workflow definitions + run history with step-by-step results |

## Workflows

Workflows are declared in `agents.yaml` under a `workflows:` key. Each step specifies capability requirements, instructions with template variables, and success/failure branches:

```yaml
workflows:
  code_review_cycle:
    name: "Code Review Cycle"
    steps:
      - id: write
        caps: [python, typescript]
        instructions: "{{input}}"
        on_success: review
        on_failure: stop

      - id: review
        caps: [code-review]
        instructions: "Review this code:\n\n{{prev.result}}"
        on_success: stop
        on_failure: fix

      - id: fix
        caps: [python, typescript]
        instructions: "Fix these issues:\n\n{{prev.result}}\n\nOriginal:\n{{steps.write.result}}"
        on_success: review
        on_failure: stop
```

**Template variables:** `{{input}}`, `{{prev.result}}`, `{{steps.<id>.result}}`

Trigger a workflow from the dashboard, or from any agent via MCP:
```
workflow.run(workflow_id="code_review_cycle", input="Implement the auth module")
```

## MCP tools available to agents

| Tool | Description |
|------|-------------|
| `agent.list` | List agents matching capability requirements |
| `agent.delegate` | Delegate a task to a capability-matched agent |
| `agent.status` | Check task status and progress |
| `agent.progress` | Report progress on the current task |
| `agent.complete` | Signal task completion with a result payload |
| `agent.spawn` | Spawn a new agent session with a given role |
| `memory.namespaces` | List all memory namespaces |
| `memory.list` | List keys and metadata within a namespace |
| `memory.get` | Load a specific memory entry |
| `memory.search` | Hybrid FTS5 + vector search across a namespace |
| `memory.set` | Write a value to a namespace |
| `memory.delete` | Delete a key |
| `memory.tag` | Add tags to a memory entry |
| `workflow.run` | Start a named workflow with an input string |

## Configuration

All configuration lives in `agents.yaml`. Key sections:

```yaml
tasks:
  retention_days: 7        # how long to keep completed tasks

connectors:
  - type: local            # in-process agents (Claude Code sessions)
  - type: gateway          # external agents registering over HTTP
    bootstrap_token: "changeme"
    heartbeat_ttl: 30

roles:
  - id: orchestrator
    name: Orchestrator
    model: claude-opus-4-6
    caps: [orchestration, planning]
    scopes: [agent:list, agent:delegate, agent:spawn, memory:read, memory:write]
    system_prompt: |
      ...
```

## Test

```bash
uv run pytest tests/ -v
```

135 tests, no external services required.
