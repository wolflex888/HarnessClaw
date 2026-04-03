# Workflow Definitions Design

**Date:** 2026-04-02

## Goal

Declarative multi-step pipelines defined in `agents.yaml` so the gateway can execute deterministic agent workflows (e.g., write → review → fix → merge) driven by task completion events — without relying on an LLM to sequence steps.

## Architecture

A new `WorkflowEngine` sits alongside the existing `Broker`, sharing the same `EventBus`. Workflows are defined in `agents.yaml` under a top-level `workflows:` key. When a workflow is started (via API or MCP tool), the engine delegates the first step via the `Broker`, subscribes to that task's completion/failure events, and autonomously drives step progression until the workflow reaches a terminal state.

Workflow run state is persisted to SQLite so runs survive server restarts.

---

## Section 1: YAML Schema

Workflows live in a new top-level `workflows:` section in `agents.yaml`:

```yaml
workflows:
  code_review_cycle:
    name: "Code Review Cycle"
    steps:
      - id: write
        caps: [code]
        instructions: "{{input}}"
        on_success: review
        on_failure: stop

      - id: review
        caps: [code_review]
        instructions: "Review the following code:\n{{prev.result}}"
        on_success: stop
        on_failure: fix

      - id: fix
        caps: [code]
        instructions: "Fix these issues:\n{{prev.result}}\n\nOriginal:\n{{steps.write.result}}"
        on_success: review
        on_failure: stop
```

### Step fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | Unique within the workflow |
| `caps` | yes | Capability list for Broker to match an agent |
| `instructions` | yes | Template string sent to the matched agent |
| `on_success` | yes | Next step id, or `stop` |
| `on_failure` | yes | Next step id, or `stop` |

### Template variables

- `{{input}}` — the initial input passed when the workflow was started
- `{{prev.result}}` — result from the immediately preceding step
- `{{steps.<id>.result}}` — result from any named step (for non-linear references like fix → write)

---

## Section 2: WorkflowEngine

New file: `harness_claw/gateway/workflow_engine.py`

### WorkflowRun dataclass

Persisted to SQLite `workflow_runs` table:

```python
@dataclass
class WorkflowRun:
    run_id: str
    workflow_id: str
    status: str          # running | completed | failed | stopped
    current_step_id: str
    step_results: dict   # step_id -> result value
    input: str
    initiated_by: str
    created_at: str
    updated_at: str
```

### WorkflowEngine responsibilities

- `start(workflow_id, input, initiated_by) → run_id`
  - Creates a `WorkflowRun` record in SQLite
  - Renders the first step's instructions template
  - Delegates via `broker.delegate(caps=..., instructions=..., delegated_by=run_id, callback=True)`
  - Subscribes to `task:{task_id}:completed` and `task:{task_id}:failed` on EventBus

- `_on_step_event(event)` — called on task completion or failure
  - Extracts result from event payload
  - Records result in `run.step_results[step_id]`
  - Resolves `on_success` / `on_failure` branch
  - If next step is `stop` and arrived via `on_success`: marks run `completed`
  - If next step is `stop` and arrived via `on_failure`: marks run `failed`
  - Otherwise: renders next step's instructions, delegates, subscribes to new task events

### Template rendering

Simple regex-based substitution: replace `{{input}}`, `{{prev.result}}`, `{{steps.<id>.result}}`. No external templating library needed.

### RoleRegistry changes

`RoleRegistry` gets a `workflow_definitions` property that parses the `workflows:` section from `agents.yaml` into a `dict[str, WorkflowDefinition]`.

### Wiring in server.py

`WorkflowEngine` is instantiated at module level alongside `broker` and `event_bus`, sharing both references. It is registered on startup after the broker listener is wired.

---

## Section 3: API Surface

### New MCP tool

`workflow.run(workflow_id, input) → run_id`

Added to `GatewayMCP` and registered in the `/mcp/tools/call` dispatch table in `server.py`. This lets any agent trigger a workflow the same way it delegates a task.

### New REST endpoints

```
GET  /api/workflows                  → list workflow definitions (id, name, steps summary)
POST /api/workflows/{id}/run         → start a run { input, initiated_by } → { run_id }
GET  /api/workflows/runs             → list all WorkflowRuns (sorted newest-first)
GET  /api/workflows/runs/{run_id}    → run detail with full step_results
```

### WebSocket broadcast events

Emitted via the existing `runner._broadcast()` mechanism:

```
workflow.started    { run_id, workflow_id, step_id }
workflow.step       { run_id, step_id, status: "completed"|"failed", result }
workflow.completed  { run_id }
workflow.failed     { run_id, reason }
```

Agent status on underlying tasks remains available via the existing `agent.status` MCP tool — no new status tool needed.

---

## Section 4: Dashboard UI

New `WorkflowsTab` component added to the React dashboard alongside Tasks, Memory, and Audit tabs.

### Left panel — workflow definitions

- List of workflows from `GET /api/workflows`
- Each row: workflow name + step count
- Click to expand: linear step list showing id, caps, on_success/on_failure edges
- "Run" button opens an input modal → posts to `POST /api/workflows/{id}/run`

### Right panel — workflow runs

- List of recent runs from `GET /api/workflows/runs`, sorted newest-first
- Each run row: workflow name, status badge (running / completed / failed / stopped), initiated_by, timestamp
- Click a run → inline expansion showing step-by-step results: step id, status icon, truncated result
- Live updates via `workflow.*` WebSocket events — no polling

### Layout

Split-panel layout matching the existing `MemoryTab` pattern. No separate tab for runs; definitions and runs co-located.

### TypeScript types to add to `types.ts`

```typescript
export interface WorkflowDefinition {
  id: string
  name: string
  steps: WorkflowStep[]
}

export interface WorkflowStep {
  id: string
  caps: string[]
  instructions: string
  on_success: string
  on_failure: string
}

export interface WorkflowRun {
  run_id: string
  workflow_id: string
  status: 'running' | 'completed' | 'failed' | 'stopped'
  current_step_id: string
  step_results: Record<string, unknown>
  input: string
  initiated_by: string
  created_at: string
  updated_at: string
}
```

WebSocket incoming events to add to `WSIncoming` union:
```typescript
| { type: 'workflow.started'; run_id: string; workflow_id: string; step_id: string }
| { type: 'workflow.step'; run_id: string; step_id: string; status: 'completed' | 'failed'; result: unknown }
| { type: 'workflow.completed'; run_id: string }
| { type: 'workflow.failed'; run_id: string; reason: string }
```
