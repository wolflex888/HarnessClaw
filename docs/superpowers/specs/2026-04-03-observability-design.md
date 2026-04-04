# Observability Layer Design

**Date:** 2026-04-03
**Status:** Approved

## Overview

Add a real-time observability layer so operators can see what each agent is doing right now — current tool and elapsed time — plus a recent activity timeline of the last 50 tool calls. Stuck detection flags agents that have been running the same tool too long, are looping, or have gone silent.

The implementation is provider-aware: Claude Code agents use hooks + JSONL polling; all other providers get a `NullProvider` no-op with "not available" shown in the UI. Future providers slot in without touching `JobRunner`.

## Goals

- Show current tool + elapsed time per agent in the Sessions tab
- Show recent activity timeline (last 50 tool calls) per agent
- Detect stuck agents via tool timeout, tool loop, and PTY silence signals
- Provider abstraction so non-Claude runtimes can be added without refactoring

## Non-Goals

- Automatic interventions (interrupt / nudge) — follow-up feature
- Persistent tool history across server restarts
- Per-tool configurable thresholds (static defaults only)

---

## Module Structure

New module: `harness_claw/observability/`

| File | Responsibility |
|------|----------------|
| `models.py` | `ToolEvent`, `AgentStatus` dataclasses |
| `provider.py` | `ObservabilityProvider` protocol, `NullProvider`, `ClaudeCodeProvider` |
| `store.py` | `AgentObservabilityStore` — in-memory state keyed by session_id |

---

## Data Model

### `ToolEvent`

```python
@dataclass
class ToolEvent:
    tool_name: str
    started_at: datetime
    ended_at: datetime | None   # None if still running
    duration_ms: int | None
    session_id: str
```

### `AgentStatus`

```python
@dataclass
class AgentStatus:
    session_id: str
    current_tool: str | None
    current_tool_started_at: datetime | None
    last_output_at: datetime | None
    recent_activity: list[ToolEvent]   # last 50, newest first
    is_stuck: bool
    stuck_reason: str | None           # "tool_timeout" | "tool_loop" | "output_silence" | None
```

### Stuck detection thresholds (static defaults)

| Signal | Threshold | `stuck_reason` |
|--------|-----------|----------------|
| Tool timeout | current tool running > 5 minutes | `"tool_timeout"` |
| Tool loop | last 10 `recent_activity` entries are the same tool | `"tool_loop"` |
| Output silence | no PTY output for > 5 minutes | `"output_silence"` |

Stuck is re-evaluated on every `update_tool_start`, `update_tool_end`, and `update_output` call.

---

## Provider Abstraction

### Protocol

```python
class ObservabilityProvider(Protocol):
    def configure_session(
        self, session_id: str, working_dir: str, extra_env: dict[str, str]
    ) -> None: ...

    def unconfigure_session(self, session_id: str, working_dir: str) -> None: ...
```

### `NullProvider`

No-op implementation. Used for all non-Claude providers. `configure_session` and `unconfigure_session` do nothing.

### `ClaudeCodeProvider`

`configure_session`:
1. Reads existing `.claude/settings.json` in working dir (if present)
2. Merges in `PreToolUse` and `PostToolUse` hook entries under a `"harnessclaw"` key
3. Writes back to `.claude/settings.json`

`unconfigure_session`:
1. Reads `.claude/settings.json`
2. Removes only the harnessclaw hook entries
3. Writes back (or deletes the file if empty after removal)

Hook command (both events):
```bash
jq --arg sid "$HARNESS_SESSION_ID" '. + {session_id: $sid}' | \
  curl -s -X POST "$HARNESS_BASE_URL/agent/tool-event" \
    -H 'Content-Type: application/json' -d @-
```

`HARNESS_SESSION_ID` and `HARNESS_BASE_URL` are injected into `extra_env` alongside `HARNESS_TOKEN` at session start. `HARNESS_BASE_URL` is set from `JobRunner._mcp_base_url`.

Hook payload schema:
```json
{
  "session_id": "string",
  "event": "tool_start | tool_end",
  "tool_name": "string",
  "timestamp_ms": 1234567890000,
  "duration_ms": 1200
}
```
`duration_ms` is present only on `tool_end`.

Provider selection in `JobRunner.start_session`:
```python
provider = ClaudeCodeProvider() if role.provider == "claude" else NullProvider()
provider.configure_session(session_id, working_dir, extra_env)
```

---

## `AgentObservabilityStore`

In-memory. No SQLite — tool history is ephemeral; JSONL is the durable record.

```python
class AgentObservabilityStore:
    def init_session(self, session_id: str) -> None
    def remove_session(self, session_id: str) -> None
    def update_tool_start(self, session_id: str, tool_name: str, timestamp: datetime) -> None
    def update_tool_end(self, session_id: str, tool_name: str, ended_at: datetime, duration_ms: int) -> None
    def update_output(self, session_id: str, at: datetime) -> None
    def get_status(self, session_id: str) -> AgentStatus | None
    def all_statuses(self) -> list[AgentStatus]
```

`recent_activity` is a `collections.deque(maxlen=50)`. Stuck detection runs inside each `update_*` method.

---

## JSONL Backfill

`CostPoller` is extended to also parse `tool_use` / `tool_result` message pairs from the JSONL files on each 10s tick. Parsed events are passed to `AgentObservabilityStore.update_tool_end` for any events not already present (deduplication by `started_at`). This ensures the timeline is complete even if hook events are missed.

---

## Server Changes

### New endpoint

```
POST /agent/tool-event
```

No token auth required (localhost only). Updates `AgentObservabilityStore`, then broadcasts over the existing `/ws` WebSocket:

```json
{
  "type": "agent_status",
  "session_id": "string",
  "current_tool": "Bash | null",
  "current_tool_started_at": "ISO8601 | null",
  "recent_activity": [...],
  "is_stuck": false,
  "stuck_reason": null
}
```

### New endpoint

```
GET /agent/{session_id}/status
```

Returns `AgentStatus` as JSON. Used for initial dashboard load.

---

## UI Changes

All changes in `SessionsTab.tsx`.

### Live tool badge (session row)

A small badge next to the session name showing current tool and elapsed time. Hidden when `current_tool` is null (idle). Elapsed time counts up client-side via `setInterval(1000)` using `current_tool_started_at`.

```
● agent-1   [Bash  14s]   running
● agent-2                 running   ← idle, no badge
```

Stuck sessions show an amber warning icon next to the badge with `stuck_reason` as tooltip.

### Activity timeline (session detail)

Expanding a session row reveals the recent activity timeline — a compact list of the last 50 tool calls:

```
Bash          1.2s   14:03:22
Read          0.3s   14:03:19
WebSearch     4.1s   14:03:12
```

Populated from `recent_activity` in the latest `agent_status` WebSocket message for that session. Also fetched on initial expand via `GET /agent/{session_id}/status`.

---

## Testing

### 1. Hook injection

- `configure_session` on empty working dir → creates `.claude/settings.json` with correct hook entries
- `configure_session` on existing settings file → merges, does not overwrite unrelated keys
- `unconfigure_session` → removes only harnessclaw hooks; leaves other settings intact
- `unconfigure_session` on file with only harnessclaw hooks → file removed

### 2. Tool event processing

- POST `tool_start` → `AgentStatus.current_tool` set, `current_tool_started_at` set
- POST `tool_end` → `current_tool` cleared, event appended to `recent_activity`
- Push 51 events → `recent_activity` length stays at 50 (oldest dropped)

### 3. Stuck detection

- Set `current_tool_started_at` to 6 minutes ago → `is_stuck=True`, `stuck_reason="tool_timeout"`
- Fill `recent_activity` with 10 identical tool entries → `is_stuck=True`, `stuck_reason="tool_loop"`
- Call `update_output` with `at=now` → `is_stuck=False` (silence signal cleared)
- Set `last_output_at` to 6 minutes ago, no current tool → `is_stuck=True`, `stuck_reason="output_silence"`

### 4. JSONL backfill

- Write sample JSONL with `tool_use`/`tool_result` pairs → `CostPoller` tick populates `recent_activity` with correct tool names and durations
- Events already present (same `started_at`) are not duplicated
