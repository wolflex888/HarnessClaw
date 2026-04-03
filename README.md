# HarnessClaw

Run a team of Claude Code agents locally. An orchestrator breaks work into tasks, specialist agents execute them, and a live dashboard shows you everything happening in real time.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Node 18+
- [Claude Code CLI](https://claude.ai/code) installed and authenticated

## Setup

```bash
uv sync
cd ui && npm install && npm run build && cd ..
uv run harnessclaw run
```

Dashboard: http://localhost:8000

---

## Features

### Multi-agent sessions

Each role defined in `agents.yaml` runs as a separate Claude Code session with its own PTY terminal. You can have an orchestrator, a code writer, a code reviewer, and a terminal session all running simultaneously — each with the right model, tools, and system prompt for its job.

Sessions persist across restarts. The dashboard shows all active sessions in a sidebar; click any to switch terminals without losing history.

### Task delegation

Agents delegate work to each other using the `agent.delegate` MCP tool:

```
agent.delegate(
  caps=["code-review"],
  instructions="Review the changes in the last commit",
  callback=True
)
```

The gateway finds the best-matched agent by capability, writes the task to its terminal, and tracks it in SQLite. When the agent calls `agent.complete`, the result is delivered back to the delegator as a terminal notification.

The **Tasks tab** shows every delegated task — status, progress percentage, full result — with a one-click retry button for failed tasks.

### Declarative workflows

Define repeatable multi-step pipelines in `agents.yaml`. The gateway drives execution automatically — no orchestrator agent required:

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

Each step gets the previous step's result via `{{prev.result}}`, or any named step's result via `{{steps.<id>.result}}`. Steps branch on success or failure. Trigger from the dashboard or from any agent with `workflow.run(workflow_id=..., input=...)`.

The **Workflows tab** lists all defined workflows, lets you run them with a text input, and shows run history with step-by-step results.

### Shared persistent memory

Agents read and write to a shared namespaced memory store backed by SQLite. Memory survives restarts and is searchable:

```
memory.set("project:myapp", "auth-decision", "Using JWT with 24h expiry", summary="Auth token strategy", tags=["auth", "decisions"])
memory.search("project:myapp", "authentication patterns")  # hybrid FTS5 + vector search
```

The **Memory tab** lets you browse, inspect, and delete memory entries by namespace.

### Code review automation

The built-in `code-reviewer` role reviews local diffs or GitHub PRs and returns a structured verdict:

```json
{
  "verdict": "REVISE",
  "summary": "One error-level finding in the auth layer",
  "findings": [
    {
      "severity": "error",
      "category": "security",
      "file": "auth/tokens.py",
      "line": 42,
      "message": "Token not validated before use",
      "suggestion": "Call validate_token() before accessing claims"
    }
  ]
}
```

Pair it with the `code_review_cycle` workflow for a fully automated write → review → fix loop.

### Audit trail

Every MCP tool call is logged: who called it, what arguments, what outcome. The **Audit tab** shows the most recent events in reverse-chronological order — useful for understanding what your agents actually did.

### External agent registration

Agents running outside the local process (remote machines, containers, other processes) can register with a bootstrap token and receive delegated tasks over HTTP:

```bash
POST /gateway/agents/register
{ "bootstrap_token": "changeme", "caps": ["data-analysis"], "role_id": "analyst" }
```

---

## Agent roles

Roles are defined in `agents.yaml`. Each role gets a model, capability list, allowed MCP scopes, and a system prompt:

```yaml
roles:
  - id: orchestrator
    name: Orchestrator
    model: claude-opus-4-6
    caps: [orchestration, planning]
    scopes: [agent:list, agent:delegate, agent:spawn, memory:read, memory:write]
    system_prompt: |
      You are an orchestrator. Break complex tasks into subtasks and delegate them
      to specialist agents using agent.delegate...

  - id: code-writer
    name: Code Writer
    model: claude-sonnet-4-6
    caps: [python, typescript, testing]
    scopes: [agent:list, memory:read, memory:write]
    system_prompt: |
      You write clean, well-tested code...

  - id: code-reviewer
    name: Code Reviewer
    model: claude-sonnet-4-6
    caps: [code-review, pr-review]
    scopes: [agent:list, memory:read]
    system_prompt: |
      You review code for bugs, security issues, and convention violations...
```

## MCP tools

| Tool | Scope required | Description |
|------|---------------|-------------|
| `agent.list` | `agent:list` | List agents matching capability requirements |
| `agent.delegate` | `agent:delegate` | Delegate a task; returns task_id |
| `agent.status` | `agent:delegate` | Check task status and progress |
| `agent.progress` | `agent:report` | Report progress on the current task |
| `agent.complete` | `agent:report` | Signal task completion with a result payload |
| `agent.spawn` | `agent:spawn` | Spawn a new agent session with a given role |
| `workflow.run` | `agent:delegate` | Start a named workflow |
| `memory.namespaces` | `memory:read` | List all memory namespaces |
| `memory.list` | `memory:read` | List keys and metadata within a namespace |
| `memory.get` | `memory:read` | Load a specific memory entry |
| `memory.search` | `memory:read` | Hybrid FTS5 + vector search |
| `memory.set` | `memory:write` | Write a value to a namespace |
| `memory.delete` | `memory:write` | Delete a key |
| `memory.tag` | `memory:write` | Add tags to a memory entry |

## Test

```bash
uv run pytest tests/ -v
```

135 tests, no external services required.
