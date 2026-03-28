
# Claude Code Sessions — Design Spec

**Date:** 2026-03-28
**Project:** HarnessClaw

---

## Overview

Rework the AI Gateway UI to use Claude Code (`claude` CLI) as the agent backend instead of the direct Anthropic API. Sessions replace agents as the primary entity. Each session is a conversation with a role (system prompt template) running in a working directory. Claude Code handles tool execution natively; permission requests are surfaced in the browser for user approval.

---

## What Changes

| Area | Before | After |
|------|--------|-------|
| Backend provider | Direct Anthropic API | Claude Code subprocess (`claude -p`) |
| Primary entity | Agent (config) | Session (role + directory + conversation) |
| Sidebar | Agent list | Sessions grouped by working directory |
| Main area | Chat + side jobs panel | Four tabs: Work, Tasks, Agent, Tools |
| Tool execution | Custom `call_agent` tool | Claude Code native tools (Bash, Edit, Read, etc.) |
| Permission prompts | None | Inline permission dialog in Work tab |
| Persistence | In-memory only | `sessions.json` on disk (survives restart) |

---

## Architecture

### Backend

```
harness_claw/
├── server.py              MODIFY — updated WS/REST endpoints
├── role_registry.py       NEW    — loads role templates from agents.yaml
├── session_store.py       NEW    — loads/saves sessions.json to disk
├── session.py             MODIFY — updated Session model
├── job_runner.py          MODIFY — claude-code session handling, permission flow
├── pricing.py             unchanged
└── providers/
    ├── base.py            MODIFY — add cwd, claude_session_id params
    ├── anthropic.py       MODIFY — accept (and ignore) new params
    └── claude_code.py     NEW    — ClaudeCodeProvider
```

### Frontend

```
ui/src/
├── App.tsx                         MODIFY — session-centric state
├── types.ts                        MODIFY — updated types
├── ws.ts                           unchanged
└── components/
    ├── SessionSidebar.tsx          NEW (replaces AgentSidebar)
    ├── SessionCreatePanel.tsx      NEW — directory + role picker
    ├── TabPanel.tsx                NEW — tab container [Work, Tasks, Agent, Tools]
    ├── WorkTab.tsx                 NEW (replaces ChatPanel) — messages + permission dialog
    ├── TasksTab.tsx                NEW (replaces JobsPanel)
    ├── AgentTab.tsx                NEW — role info + edit
    ├── ToolsTab.tsx                NEW — tools list
    ├── PermissionDialog.tsx        NEW — inline approve/deny card
    ├── SessionCostBar.tsx          MODIFY — reads from session
    └── AgentConfigPanel.tsx        REMOVE — replaced by SessionCreatePanel + AgentTab
```

---

## Data Models

### Role Template (`agents.yaml`)

Agents become read-only role templates. The `orchestrates` field is removed (Claude Code handles multi-agent natively).

```yaml
roles:
  - id: general-purpose
    name: General Purpose
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You are a helpful assistant."
    max_tokens: 8192

  - id: code-writer
    name: Code Writer
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You write clean, well-tested code."
    max_tokens: 8192

  - id: reviewer
    name: Code Reviewer
    provider: claude-code
    model: claude-sonnet-4-6
    system_prompt: "You review code for correctness, clarity, and security."
    max_tokens: 4096
```

### Session (`session.py`)

```python
@dataclass
class Session:
    session_id: str                    # our UUID (primary key)
    role_id: str                       # which role template
    working_dir: str                   # e.g. ~/src/HarnessClaw
    model: str
    name: str = ""                     # auto-set from first user message (truncated to 40 chars)
    status: str = "idle"               # idle | running | killed
    claude_session_id: str | None = None   # Claude Code's session ID, used for --resume
    messages: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        return get_cost(self.model, self.input_tokens, self.output_tokens)
```

### Session Store (`session_store.py`)

Loads and saves all sessions to `sessions.json` in the project root. Serializes/deserializes `Session` dataclasses. Exposes `get(session_id)`, `all()`, `save(session)`, `delete(session_id)`.

---

## ClaudeCodeProvider

`harness_claw/providers/claude_code.py` implements `BaseProvider`.

### Invocation

```bash
claude -p \
  --output-format stream-json \
  --input-format stream-json \
  --include-partial-messages \
  --system-prompt "<role system_prompt>" \
  --model <model> \
  [--resume <claude_session_id>]  # on 2nd+ messages
  "<user message>"
```

The subprocess runs with `cwd` set to the session's `working_dir`.

### stream-json event handling

The provider reads JSONL from stdout line by line:

| Event type | Action |
|------------|--------|
| `system` / `subtype: init` | Extract `session_id`, yield `{"type": "session_init", "claude_session_id": "..."}` |
| `assistant` (partial, with `--include-partial-messages`) | Extract text delta, yield `{"type": "token", "delta": "..."}` |
| `tool_input` / permission request | Yield `{"type": "permission_request", "request_id": "...", "tool_name": "...", "input": {...}}`, then block on asyncio.Event until response arrives |
| `result` / `subtype: success` | Extract `cost_usd`, `usage`, yield `{"type": "usage", ...}` then `{"type": "stop"}` |
| `result` / `subtype: error` | Yield `{"type": "error", "message": "..."}` |

### Permission flow

1. Provider yields `permission_request` event and sets `self._pending_permissions[request_id] = asyncio.Event()`
2. `JobRunner` forwards it to the browser as a WS `permission_request` message
3. `server.py` receives `permission_response` from browser, calls `job_runner.resolve_permission(request_id, approved)`
4. `JobRunner` sets the event and stores the response
5. Provider unblocks, writes `{"approved": true/false}` to subprocess stdin, continues streaming

### BaseProvider signature changes

```python
@abstractmethod
async def stream_chat(
    self,
    messages: list[dict],
    system: str,
    model: str,
    max_tokens: int,
    cwd: str | None = None,
    claude_session_id: str | None = None,
) -> AsyncIterator[dict]: ...
```

`AnthropicProvider` accepts and ignores `cwd` and `claude_session_id`.

---

## Session Lifecycle

```
[Create]  →  idle
[Send message]  →  running
[Response complete]  →  idle
[Kill]  →  killed
[Resume (send new message)]  →  running  →  idle
[Delete]  →  removed from store + Claude Code session deleted from disk
```

### Delete

1. Kill subprocess if running (SIGTERM)
2. Locate Claude Code session data: `~/.claude/projects/<encoded_cwd>/<claude_session_id>.jsonl`
3. Delete the session file
4. Remove from `sessions.json`
5. Broadcast `session_deleted {session_id}` over WebSocket

---

## UI Layout

### Global Layout (unchanged structure)

```
┌─────────────────────────────────────────────────┐
│  SessionSidebar (220px) │  Main Area (flex)      │
│                         │                        │
│  ~/src/HarnessClaw      │  SessionCostBar        │
│   ● Code Writer —...    │  [Work][Tasks][Agent]  │
│   ○ Reviewer    —...    │  [Tools]               │
│                         │                        │
│  ~/src/other            │  <active tab content>  │
│   ✕ General —...        │                        │
│                         │                        │
│  [+ New Session]        │                        │
└─────────────────────────────────────────────────┘
```

### SessionSidebar

Sessions grouped by `working_dir`. Within each group, sorted by most recent activity.

Session row:
```
● Code Writer — Write a JWT auth module    [■]
```

- `●` running (blue), `○` idle (gray), `✕` killed (red)
- `[■]` kill button — only shown when running
- Right-click or hover menu: **Resume** (if killed), **Delete**
- "New session" shows until first message sets the name

### Session Create Panel

Shown when "+ New Session" is clicked. Replaces main area.

Fields:
- **Directory** — text input, default `~/src`, constrained to paths under `~/src`
- **Role** — dropdown, default "General Purpose", lists all roles from `/api/roles`
- **[Create]** button — creates session via `POST /api/sessions`, immediately switches to Work tab

### Tabs: Work, Tasks, Agent, Tools

**Work tab** — message thread (same as current ChatPanel). Permission dialogs appear inline as cards above the streaming response:

```
┌─────────────────────────────────────┐
│ 🔧 Bash                             │
│ $ pytest tests/ -v                  │
│                    [Allow]  [Deny]  │
└─────────────────────────────────────┘
```

Stream is paused until Allow or Deny is clicked.

**Tasks tab** — job list (same as current JobsPanel). Renamed Jobs → Tasks throughout.

**Agent tab** — read-only view of the session's role:
- Role name, model, system prompt (full text), working directory
- **[Edit]** button opens inline editing of system prompt and model (does not affect other sessions using the same role)

**Tools tab** — lists all tools Claude Code has enabled for this session. Sourced from the `tools` array in the `system/init` stream-json event. Each tool shown as a row: icon, name, description. No interaction — display only.

---

## WebSocket Protocol

### Client → Server

| type | fields | description |
|------|--------|-------------|
| `chat` | `session_id`, `text` | Send message to session |
| `cancel` | `job_id` | Cancel running job (kill subprocess) |
| `resume` | `session_id` | Resume a killed session (no message, just reconnect) |
| `permission_response` | `request_id`, `approved: bool` | Respond to permission prompt |

### Server → Client

| type | fields | description |
|------|--------|-------------|
| `token` | `job_id`, `delta` | Streaming token chunk |
| `job_update` | `job_id`, `session_id`, `status`, `progress`, `title` | Task status change |
| `tool_call` | `job_id`, `tool_name`, `input` | Tool use event (for display) |
| `usage` | `job_id`, `input_tokens`, `output_tokens`, `cost_usd` | Token usage update |
| `error` | `job_id`, `message` | Error in chat thread |
| `permission_request` | `session_id`, `request_id`, `tool_name`, `input` | Awaiting user approval |
| `session_update` | `session_id`, `name`, `status` | Name or status changed |
| `session_deleted` | `session_id` | Session removed |

---

## REST Endpoints

| method | path | description |
|--------|------|-------------|
| `GET` | `/api/roles` | List role templates from agents.yaml |
| `GET` | `/api/sessions` | All sessions as `{working_dir: [Session, ...]}` |
| `POST` | `/api/sessions` | Create session: `{role_id, working_dir}` |
| `DELETE` | `/api/sessions/{id}` | Kill + delete session + Claude Code history |

---

## Error Handling

- **Subprocess crash**: treated as job failure; session status → `killed`; error message in Work tab
- **`--resume` with missing session file**: Claude Code starts a fresh session; `claude_session_id` updated
- **Invalid working directory**: validated on `POST /api/sessions`; returns 422 if path is outside `~/src` or doesn't exist
- **Permission denied by user**: written to subprocess stdin; Claude Code receives denial and responds accordingly (typically explains it cannot complete the action)

---

## Testing

- Unit tests for `role_registry.py` (load roles from YAML)
- Unit tests for `session_store.py` (save/load/delete)
- Integration test for `ClaudeCodeProvider` using a mock subprocess that emits canned stream-json events
- Integration test for the permission flow (mock subprocess emits permission_request, response resolves it)
- Manual browser testing for UI interactions

---

## Tech Stack (additions)

| layer | addition |
|-------|---------|
| Backend | `asyncio.create_subprocess_exec` for Claude Code subprocess |
| Backend | `sessions.json` for session persistence |
| Frontend | Tab-based layout (no new libraries — pure Tailwind) |

---
