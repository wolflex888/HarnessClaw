# Execute Plan Design

**Date:** 2026-04-05
**Status:** Approved

## Overview

Integrate the superpowers subagent-driven-development workflow into HarnessClaw as a first-class MCP tool. The brainstorming and planning phases remain as-is (interactive, superpowers skills in a Claude Code session). At the implementation phase, instead of dispatching internal `Agent` tool subagents, the controller calls `harnessclaw.execute_plan(plan_path)` — a new MCP tool that drives the full implementation loop using real HarnessClaw agent jobs.

## Goals

- `execute_plan(plan_path)` MCP tool: reads a plan markdown file, executes each task end-to-end, streams progress back to the calling session, blocks until all tasks are done
- Per-task loop: code-writer implements → spec-reviewer + code-reviewer review in parallel → both must APPROVE → any REVISE sends aggregated findings back to code-writer → repeat until both approve
- Reviewers receive previous round's findings on re-review so they can verify issues were addressed
- Progress visible both inline (MCP streaming notifications) and in the dashboard (tasks/workflow UI)
- One new role: `spec-reviewer`; existing `code-writer` and `code-reviewer` roles are reused unchanged

## Non-Goals

- Modifying the brainstorming or writing-plans superpowers skills
- Replacing the interactive terminal session for brainstorming
- Automated plan generation (a human still writes or approves the plan before calling execute_plan)
- Parallel task execution (tasks run sequentially; only the two reviews per task run concurrently)

---

## New Role: `spec-reviewer`

Added to `agents.yaml`:

```yaml
- id: spec-reviewer
  name: Spec Reviewer
  provider: claude-code
  model: claude-sonnet-4-6
  system_prompt: |
    You are a spec compliance reviewer. Your only job is to check whether
    an implementation matches its spec exactly — no more, no less.

    You receive:
    - The plan task spec (what was supposed to be built)
    - The implementation summary (what was actually built: files changed, commits)
    - Previous review findings (if this is a re-review after a rewrite)

    Return a JSON verdict:
    {
      "verdict": "APPROVE" | "REVISE",
      "summary": "Brief assessment",
      "findings": [
        {
          "type": "missing" | "extra" | "incorrect",
          "description": "What's wrong"
        }
      ]
    }

    APPROVE if the implementation covers all spec requirements with nothing
    significant missing or added. REVISE requires at least one finding.
    If this is a re-review, check whether prior findings were addressed.
  scopes: [memory:read]
  caps: [spec-review]
```

---

## `execute_plan` MCP Tool

**Signature:** `execute_plan(plan_path: str) -> str`

- `plan_path`: path to a plan markdown file (e.g. `docs/superpowers/plans/2026-04-05-my-feature.md`)
- Returns: a text summary of all tasks completed and their final verdicts
- Streams progress via MCP `progress` notifications as each task completes
- Blocks until all tasks are done or a task is permanently blocked

### Progress output (inline in calling session)

```
[Task 1/9] Implementing: RoleConfig limit fields...
[Task 1/9] Reviewing in parallel...
[Task 1/9] ✓ APPROVED — moving to next task
[Task 2/9] Implementing: Task limit fields + SQLite migration...
[Task 2/9] Reviewing in parallel...
[Task 2/9] ⚠ REVISE — sending findings to code-writer (round 2)
[Task 2/9] Reviewing in parallel (round 2, with prior findings)...
[Task 2/9] ✓ APPROVED — moving to next task
...
```

---

## Per-Task Loop

```
┌─────────────────────────────────────────────┐
│              For each task in plan           │
│                                              │
│  1. Delegate to code-writer                  │
│     caps: [python, typescript]               │
│     instructions: full task text             │
│     → wait for result                        │
│                                              │
│  2. Delegate to spec-reviewer  ──┐           │
│     caps: [spec-review]          ├─ parallel │
│  3. Delegate to code-reviewer  ──┘           │
│     caps: [code-review]                      │
│     → wait for BOTH results                  │
│                                              │
│  4. If both APPROVE → next task              │
│                                              │
│  5. If any REVISE:                           │
│     - aggregate findings from both reviewers │
│     - delegate to code-writer with:          │
│       * original task text                   │
│       * aggregated findings                  │
│     → go to step 2 (round N+1)               │
│     → reviewers receive previous findings    │
└─────────────────────────────────────────────┘
```

### Review instructions on re-review

When dispatching reviewers after a rewrite, the instructions include the prior round's findings:

> "In your previous review (round N) you found:
> [prior findings]
>
> Here is the rewrite. Check whether these findings were addressed, and look for anything new."

### Blocked task handling

If code-writer returns `BLOCKED` (unable to complete), `execute_plan` stops immediately and returns an error summary. The operator resolves the blocker manually and re-runs `execute_plan` with a revised plan.

### Max rewrite rounds

Hard cap of 5 rewrite rounds per task. If both reviewers haven't approved after 5 rounds, the task is marked STUCK and `execute_plan` stops with a summary of the unresolved findings.

---

## Plan Parser

`harness_claw/runtime/plan_parser.py`

Parses plan markdown files produced by `superpowers:writing-plans`. Extracts tasks by splitting on `## Task N:` headers. Each task is a `PlanTask` dataclass:

```python
@dataclass
class PlanTask:
    index: int          # 1-based
    title: str          # e.g. "RoleConfig limit fields"
    full_text: str      # complete markdown for this task (steps, code blocks, etc.)
```

The parser is tolerant of minor formatting variation — it only requires `## Task` to appear at the start of a header line.

---

## Plan Executor

`harness_claw/runtime/plan_executor.py`

`PlanExecutor` drives the per-task loop. It depends on `Broker` (for `delegate` and `get_task`) and a progress callback for streaming updates.

```python
class PlanExecutor:
    def __init__(self, broker: Broker, on_progress: Callable[[str], Awaitable[None]])
    async def execute(self, plan_path: str) -> str
```

Internally manages:
- Task result accumulation (code-writer result, round-N review results)
- Parallel fan-out of spec-reviewer + code-reviewer via `asyncio.gather`
- Review history passed as context on re-review
- Rewrite loop with round counter

---

## Data Flow

```
execute_plan("docs/superpowers/plans/feature.md")
    │
    ├─ plan_parser.parse(plan_path) → [PlanTask, ...]
    │
    └─ for each PlanTask:
           PlanExecutor.run_task(task)
               │
               ├─ broker.delegate(caps=["python","typescript"], instructions=task.full_text)
               │      └─ code-writer → result: {files_changed, commits, summary}
               │
               ├─ asyncio.gather(
               │      broker.delegate(caps=["spec-review"],   instructions=spec_review_prompt),
               │      broker.delegate(caps=["code-review"],   instructions=code_review_prompt),
               │  ) → (spec_result, quality_result)
               │
               ├─ if both APPROVE → done
               │
               └─ else → aggregate findings
                      → broker.delegate(caps=["python","typescript"], instructions=rewrite_prompt)
                      → loop (max 5 rounds)
```

---

## Waiting for Task Results

`PlanExecutor` delegates tasks via `broker.delegate` and needs to block until each completes. It does this by subscribing to the existing `EventBus` for `task.updated` events filtered by `task_id`, using an `asyncio.Event` to unblock when `status == "completed" | "failed"`.

```python
async def _wait_for_task(self, task_id: str) -> Task:
    done = asyncio.Event()
    def on_update(event):
        if event["task_id"] == task_id and event["status"] in ("completed", "failed"):
            done.set()
    self._event_bus.subscribe("task.updated", on_update)
    await done.wait()
    self._event_bus.unsubscribe("task.updated", on_update)
    return self._broker.get_task(task_id)
```

Parallel reviews use `asyncio.gather` on two `_wait_for_task` calls after dispatching both delegates.

---

## File Map

| File | Change |
|------|--------|
| `harness_claw/runtime/plan_parser.py` | New: `PlanTask` dataclass, `parse_plan(path) -> list[PlanTask]` |
| `harness_claw/runtime/plan_executor.py` | New: `PlanExecutor` class |
| `harness_claw/gateway/mcp_server.py` | Add `execute_plan` tool handler |
| `harness_claw/server.py` | Register `execute_plan` in MCP tool schema |
| `agents.yaml` | Add `spec-reviewer` role |
| `tests/runtime/test_plan_parser.py` | New: parser unit tests |
| `tests/runtime/test_plan_executor.py` | New: executor unit tests (mocked broker) |

---

## Testing

1. `parse_plan` correctly extracts tasks from a real plan file
2. `parse_plan` handles plans with 1 task and plans with 9+ tasks
3. `PlanExecutor` calls broker.delegate in correct order (implement → parallel review → rewrite if needed)
4. `PlanExecutor` passes previous review findings on re-review
5. `PlanExecutor` stops after max rewrite rounds with STUCK status
6. `execute_plan` MCP tool returns error on missing plan file
