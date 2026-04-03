# HarnessClaw: Phase 2 — Code Review Workflow + Task Callbacks

**Date:** 2026-04-02
**Scope:** EventBus primitive, task callbacks, structured task results, code-reviewer role, orchestrator-driven review cycle
**Status:** Draft
**Depends on:** Phase 1 gateway (complete)

---

## Overview

Phase 2 introduces the first real multi-agent workflow: an orchestrator-driven code review cycle. To make this work, we add one new gateway primitive — the EventBus — which replaces polling-based task status checks with publish/subscribe callbacks.

The review workflow is the proving ground, but the EventBus and structured task results are general-purpose primitives that any future workflow can use.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                  HarnessClaw Gateway                  │
│                                                       │
│  ┌─────────────┐  ┌───────────────┐  ┌────────────┐  │
│  │   Auth &    │  │  Capability   │  │   Audit    │  │
│  │   Policy    │  │   Registry    │  │    Log     │  │
│  └─────────────┘  └───────────────┘  └────────────┘  │
│                                                       │
│  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │     Broker       │  │      Memory Store        │  │
│  │ (routing, tasks) │  │  (working/long-term)     │  │
│  └────────┬─────────┘  └──────────────────────────┘  │
│           │                                           │
│  ┌────────▼─────────┐  ← NEW                         │
│  │    EventBus      │                                 │
│  │ (pub/sub topics) │                                 │
│  └──────────────────┘                                 │
│                                                       │
│  ┌─────────────────────────────────────────────────┐  │
│  │                Runtime Layer                     │  │
│  │   JobRunner · PtySession · CostPoller            │  │
│  └─────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

---

## EventBus

The EventBus is a pluggable pub/sub primitive. Agents and gateway components subscribe to topics and receive events asynchronously. The broker uses it to deliver task completion callbacks; future phases can use it for any inter-component messaging.

### Protocol

```python
@dataclass
class Event:
    topic: str
    payload: dict
    timestamp: datetime
    source: str          # session_id or "system"

@dataclass
class Subscription:
    id: str
    topic: str
    handler: Callable[[Event], Awaitable[None]]

class EventBus(Protocol):
    async def publish(self, topic: str, payload: dict, source: str) -> None: ...
    async def subscribe(self, topic: str, handler: Callable[[Event], Awaitable[None]]) -> Subscription: ...
    async def unsubscribe(self, sub: Subscription) -> None: ...
```

### Topic Convention

```
task:{task_id}:completed    — task finished successfully (carries result payload)
task:{task_id}:failed       — task errored (carries error details)
task:{task_id}:progress     — progress update from agent
agent:{agent_id}:*          — wildcard: all events for an agent (Phase N)
```

### Adapters

- **`LocalEventBus`** — In-process, asyncio-based. Each subscription gets an `asyncio.Queue`. `publish` fans out to all matching subscribers. Supports exact topic matching; wildcard matching deferred to Phase N. Phase 2 default.
- **`RedisEventBus`** — Redis pub/sub backend. Same interface, distributed. Phase N.

### Configuration

```yaml
# agents.yaml
event_bus:
  backend: local       # local | redis
  # redis_url: ...     # only for redis backend
```

---

## Task Callbacks

### The Problem

In Phase 1, the orchestrator delegates a task and must poll `agent.status` to learn when it completes. This wastes tokens on repeated status checks and blocks the orchestrator from doing other work.

### The Solution

`agent.delegate` gains a `callback` parameter. When `callback=true`:

1. Broker creates the task and dispatches it as before.
2. Broker subscribes the delegating agent to `task:{task_id}:completed` and `task:{task_id}:failed` on the EventBus.
3. When the target agent calls `agent.complete` or the task fails, the broker publishes to the appropriate topic.
4. The EventBus delivers the event to the callback handler.
5. The callback handler writes a formatted notification into the delegating agent's PTY stdin.

The orchestrator "sees" the callback as a message appearing in its terminal, as if a human typed it. This reuses the existing PTY injection mechanism from `LocalDispatcher`.

### Callback Message Format

```
[TASK CALLBACK] task_id=abc-456 status=completed
Result: {"verdict": "REVISE", "summary": "2 bugs found", "findings": [...]}
```

The format is unambiguous and parseable. The orchestrator's system prompt includes instructions for recognizing and acting on callback messages.

### Auto-Unsubscribe

Subscriptions are automatically cleaned up when:
- The task reaches a terminal state (completed, failed)
- The subscribing agent's session ends

---

## Structured Task Results

### Changes to `agent.complete`

Currently `agent.complete` accepts freeform text. Phase 2 extends it to accept a structured `result` payload:

```python
# MCP tool: agent.complete
agent.complete(
    task_id: str,
    result: dict | str      # structured JSON or freeform text (backward compatible)
)
```

The `result` field is stored on the `Task` record and included in the callback event payload.

### Changes to `Task` Record

```python
@dataclass
class Task:
    task_id: str
    delegated_by: str
    delegated_to: str
    instructions: str
    context: dict | None     # NEW — structured context passed at delegation time
    caps_requested: list[str]
    status: str              # queued | running | completed | failed
    progress_pct: int
    progress_msg: str
    result: dict | str | None  # CHANGED — was str | None, now supports dict
    created_at: datetime
    updated_at: datetime
    callback: bool           # NEW — whether to notify delegating agent on completion
```

### Changes to `agent.delegate`

```python
# MCP tool: agent.delegate
agent.delegate(
    target: str | None,       # role_id or None for cap-based matching
    caps: list[str] | None,   # capability requirements
    instruction: str,         # what to do
    context: dict | None,     # NEW — structured context (files, priorities, etc.)
    callback: bool = False    # NEW — subscribe to completion event
) -> str                      # returns task_id
```

---

## Code Reviewer Role

### Role Definition

```yaml
# agents.yaml
roles:
  # ... existing roles ...

  - id: code-reviewer
    name: Code Reviewer
    provider: claude-code
    model: claude-sonnet-4-6        # Sonnet, not Opus — review is pattern-matching
    system_prompt: |
      You are a code reviewer. You review code for bugs, security issues,
      architectural problems, and convention violations.

      When you receive a review task, you will be given either:
      - A git diff and/or file paths to review (local dev)
      - A GitHub PR number to review (PR mode)

      Your review priorities are configurable via the task context. Default
      priority order: bugs > security > architecture > conventions.

      For local reviews: read the git diff and referenced files, analyze them,
      then call agent.complete with a structured verdict.

      For PR reviews: use `gh pr diff` and `gh pr view` to examine the PR,
      then call agent.complete with your verdict. If the verdict is APPROVE,
      also run `gh pr review --approve`. If REVISE, post inline comments with
      `gh pr review --comment`.

      Verdict schema:
      {
        "verdict": "APPROVE" | "REVISE",
        "summary": "Brief overall assessment",
        "findings": [
          {
            "severity": "error" | "warning" | "suggestion",
            "category": "bug" | "convention" | "architecture" | "security",
            "file": "path/to/file.py",
            "line": 42,
            "message": "What's wrong and why",
            "suggestion": "How to fix it"
          }
        ],
        "priority_focus": "What was prioritized this review"
      }

      A verdict of REVISE requires at least one finding with severity "error".
      Warnings and suggestions alone should result in APPROVE with the findings
      included as advisory notes.
    max_tokens: 8192
    scopes: [agent:list, memory:read]
    caps: [code-review, pr-review]
```

### Verdict Rules

- **APPROVE**: No errors found. Warnings and suggestions may be included as advisory.
- **REVISE**: At least one finding with `severity: "error"`. The code-writer must address all errors before re-review.
- Severity levels:
  - `error` — Must fix. Bugs, security holes, broken logic.
  - `warning` — Should fix. Unclear naming, missing error handling, potential edge cases.
  - `suggestion` — Consider. Style preferences, alternative approaches, minor improvements.

---

## Review Cycle Workflow

The orchestrator drives the review cycle. The logic lives in the orchestrator's system prompt, not in gateway code — keeping the gateway general-purpose.

### Flow

```
1. Human → Orchestrator: "Build feature X"
2. Orchestrator → Broker: agent.delegate(target="code-writer", instruction="...", callback=true)
3. Code Writer works, calls agent.complete(result="done, changed files: [...]")
4. Broker publishes task:completed → EventBus → callback handler → Orchestrator PTY
5. Orchestrator → Broker: agent.delegate(target="code-reviewer", instruction="review git diff",
                           context={files: [...], priorities: ["bugs", "security"]}, callback=true)
6. Reviewer reads diff, calls agent.complete(result={verdict: "REVISE", findings: [...]})
7. Broker publishes → Orchestrator receives callback
8. If REVISE and round < 2:
   a. Orchestrator → Code Writer: agent.delegate(instruction="fix these issues: ...",
                                   context={findings: [...]}, callback=true)
   b. Code Writer fixes, completes
   c. Orchestrator → Reviewer: agent.delegate(instruction="re-review, diff only",
                                context={previous_findings: [...], scope: "diff-only"}, callback=true)
   d. Reviewer re-reviews, returns verdict
9. If still REVISE after round 2:
   Orchestrator reports to human: "Review unresolved after 2 rounds. Remaining issues: ..."
10. If APPROVE at any point:
    Orchestrator reports to human: "Feature complete, code reviewed and approved."
```

### Review Modes

**Local dev loop:**
- Reviewer reads `git diff HEAD~1` (or a specific commit range) and changed files
- No external tool dependencies beyond git
- Used during active development

**PR review:**
- Reviewer uses `gh pr diff <number>` and `gh pr view <number>`
- Posts inline comments via `gh pr review --comment`
- Approves via `gh pr review --approve`
- Used when code is ready to merge

The orchestrator decides which mode based on context (whether a PR exists, what the human asked for).

### Token Cost Strategy

- **Reviewer model:** Sonnet (cheaper than Opus). Code review is primarily pattern-matching and doesn't need the heaviest reasoning model.
- **Round 2 scope:** Diff-only re-review. The reviewer only sees what changed since the first review, not the entire file set again. The orchestrator passes `scope: "diff-only"` in the task context.
- **Max rounds:** 2 automated rounds. After that, escalate to the human rather than burning tokens on a third agent cycle.
- **Estimated cost per cycle:** ~20-50k tokens for a full cycle (1 code write + 1 review + 1 fix + 1 re-review). A single-pass review is ~10-30k tokens.

### Orchestrator System Prompt Addition

The orchestrator's system prompt is updated with review cycle instructions:

```
## Code Review Protocol

After a code-writer completes a coding task, trigger a review cycle:

1. Delegate to a code-reviewer with callback=true. Include the list of
   changed files and any priority focus areas in the context.
2. When the review callback arrives, check the verdict:
   - APPROVE: Report success to the human. Include the reviewer's summary.
   - REVISE: Forward the findings to the code-writer as a new task.
3. After the code-writer addresses the findings, send the diff back to
   the reviewer for a second pass (scope: "diff-only").
4. If the reviewer still returns REVISE after the second round, escalate
   to the human with the unresolved findings. Do not start a third round.

Review priorities default to: bugs > security > architecture > conventions.
The human can override by specifying priorities in their request.
```

---

## New & Changed Files

### New Files

| File | Purpose |
|------|---------|
| `harness_claw/gateway/event_bus.py` | EventBus protocol + LocalEventBus adapter |
| `tests/gateway/test_event_bus.py` | EventBus unit tests |

### Changed Files

| File | Changes |
|------|---------|
| `harness_claw/gateway/broker.py` | Accept EventBus dependency. On `agent.delegate(callback=true)`, subscribe delegator to task topic. On `agent.complete`/fail, publish to task topic. Auto-unsubscribe on terminal state. |
| `harness_claw/gateway/mcp_server.py` | `agent.delegate` gains `context` and `callback` params. `agent.complete` accepts `dict` result. |
| `harness_claw/server.py` | Instantiate EventBus from config, inject into Broker. |
| `agents.yaml` | Add `code-reviewer` role. Add `event_bus` config section. Update orchestrator system prompt with review cycle instructions. |
| `tests/gateway/test_broker.py` | Test callback subscription, event publishing, auto-unsubscribe. |
| `tests/gateway/test_mcp.py` | Test new delegate/complete params. |

### Unchanged

Everything else from Phase 1 remains unchanged. The EventBus is additive — no existing behavior is modified. `callback=false` (the default) preserves Phase 1 polling behavior.

---

## Error Handling

New error codes:

| Code | Meaning |
|------|---------|
| `event_bus.publish_failed` | EventBus could not deliver an event (LocalEventBus: should not happen; RedisEventBus: connection failure) |
| `event_bus.subscribe_failed` | Could not create subscription |
| `task.invalid_result` | Structured result failed validation |

Callback delivery failures are logged to audit but do not fail the task. The task result is still stored on the Task record and accessible via `agent.status` as a fallback.

---

## Testing Strategy

| File | Coverage |
|------|---------|
| `tests/gateway/test_event_bus.py` | Publish/subscribe, multiple subscribers, unsubscribe, topic isolation, concurrent delivery |
| `tests/gateway/test_broker.py` | Callback flow end-to-end: delegate with callback → complete → verify event delivered. Auto-unsubscribe. Fallback when EventBus is unavailable. |
| `tests/gateway/test_mcp.py` | New params on delegate/complete. Structured result round-trip. Backward compatibility with string results. |

Integration test: A simulated review cycle where a mock orchestrator delegates to a mock code-writer, then to a mock reviewer, receives callbacks, and drives the full 2-round flow.

---

## Out of Scope

- Wildcard topic subscriptions (`agent:{id}:*`) — deferred to Phase N
- RedisEventBus adapter — interface designed for it, implementation deferred
- Persistent event log / event replay — deferred
- PR review GitHub integration (inline comments, approvals) — included in the reviewer's system prompt but not tested end-to-end in Phase 2; depends on `gh` CLI availability
- Dashboard TasksTab updates for callback visualization — separate spec
