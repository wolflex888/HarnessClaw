# PTY Terminal Sessions — Design Spec

**Date:** 2026-03-30
**Project:** HarnessClaw

---

## Overview

Replace the Work tab's chat interface with a full xterm.js terminal running an interactive `claude` session in a PTY. This eliminates the per-message subprocess startup overhead (the primary source of slowness), removes the custom stream-json parsing layer, and gives users the native Claude Code terminal experience directly in the browser.

---

## What Changes

| Area | Before | After |
|------|--------|-------|
| Work tab | Chat input + message bubbles | xterm.js terminal (full height) |
| Backend provider | `claude -p` subprocess per message | `claude` in a persistent PTY per session |
| WebSocket protocol | Structured events (token, job_update, etc.) | Raw terminal bytes relayed in both directions |
| Permission prompts | Custom PermissionDialog component | Native Claude Code terminal UI |
| Cost tracking | Parsed from stream-json events | Polled from Claude Code's JSONL file on disk |
| JobRunner | Manages jobs and streaming | Manages PTY process lifecycle |

**What stays unchanged:** SessionSidebar, SessionCreatePanel, SessionCostBar, TabPanel, TasksTab, AgentTab, ToolsTab, REST endpoints, SessionStore, RoleRegistry, agents.yaml.

---

## Backend

### New dependency

Add `ptyprocess` to `pyproject.toml`:

```toml
"ptyprocess>=0.7.0",
```

### PtySession

Replace `ClaudeCodeProvider` with `harness_claw/pty_session.py`. `PtySession` owns a single persistent `claude` process per HarnessClaw session.

```python
class PtySession:
    session_id: str
    process: ptyprocess.PtyProcess | None
    output_callbacks: list[Callable[[bytes], Awaitable[None]]]

    def start(self, system_prompt: str, model: str, cwd: str) -> None:
        # Spawns: claude --system-prompt "<system_prompt>" --model <model>
        # with cwd set, in a PTY

    def write(self, data: bytes) -> None:
        # Write raw bytes (keystrokes) to the PTY

    def resize(self, cols: int, rows: int) -> None:
        # Resize the PTY

    def kill(self) -> None:
        # SIGTERM the process

    async def read_loop(self) -> None:
        # Reads output bytes from PTY in a background asyncio task,
        # calls all registered output_callbacks with each chunk
```

`PtySession.read_loop()` runs as a background asyncio task for the lifetime of the process. When the process exits, the task ends.

### JobRunner changes

`JobRunner` keeps a `dict[str, PtySession]` keyed by `session_id`. On `POST /api/sessions`, a `PtySession` is created and started immediately. On `DELETE /api/sessions/{id}`, the `PtySession` is killed and removed.

The existing job queue, token streaming, and `stream_chat` call are removed.

### WebSocket protocol

The single `/ws` endpoint handles all sessions. Messages are JSON-framed:

**Browser → server:**

| type | fields | description |
|------|--------|-------------|
| `input` | `session_id`, `data: string` (base64) | Keystrokes to write to PTY |
| `resize` | `session_id`, `cols: int`, `rows: int` | Terminal resize event |

**Server → browser:**

| type | fields | description |
|------|--------|-------------|
| `output` | `session_id`, `data: string` (base64) | Raw terminal bytes from PTY |
| `session_update` | `session_id`, `status`, `name` | Status changes (unchanged) |
| `session_deleted` | `session_id` | Session removed (unchanged) |
| `cost_update` | `session_id`, `cost_usd: float`, `input_tokens: int`, `output_tokens: int` | Cost poll result |

Removed message types: `token`, `job_update`, `tool_call`, `usage`, `error`, `permission_request`, `chat`, `cancel`, `resume`, `permission_response`.

### Cost tracking

Claude Code writes every turn to:
```
~/.claude/projects/<url-encoded-cwd>/<claude_session_id>.jsonl
```

Each line is a JSON event. Lines with `type: "result"` contain `total_cost_usd` and `usage` fields.

`harness_claw/cost_poller.py` — a background asyncio task per active session:
- Runs every 3 seconds while the session's PTY process is alive
- Reads the session's JSONL file, sums all `total_cost_usd` and `usage` values
- If the total changed since last poll, sends a `cost_update` WS message to all connected clients

The `claude_session_id` needed to locate the JSONL file is obtained from the `session/init` event in the stream — but since we're no longer parsing stream-json, we instead read it from the `~/.claude/projects/<cwd>/` directory: after starting the process, poll until a new `.jsonl` file appears (compare mtime), then record that as `claude_session_id`.

### Session launch command

```bash
claude --system-prompt "<role system_prompt>" --model <model>
```

Run with `cwd` set to the session's working directory. No `--print` flag — this is interactive mode.

---

## Frontend

### New dependencies

```json
"@xterm/xterm": "^5.5.0",
"@xterm/addon-fit": "^0.10.0"
```

### TerminalTab.tsx

Replaces `WorkTab.tsx`. Mounts xterm.js into a full-height div.

Lifecycle:
1. On mount: create `Terminal` instance, attach `FitAddon`, open into container div
2. Subscribe to `output` WS messages for this `session_id` → write bytes to terminal
3. On terminal data (keystrokes): send `input` WS message
4. `ResizeObserver` on container → call `fitAddon.fit()` → send `resize` WS message with new dimensions
5. On unmount: dispose terminal, unsubscribe

The terminal fills the entire Work tab content area with no padding and a black background.

### types.ts changes

Remove: `PendingPermission`, `ToolCallEvent` (no longer needed client-side).

Add:
```typescript
// WebSocket: server → client (replaces token/job_update/tool_call/usage/error/permission_request)
| { type: 'output'; session_id: string; data: string }        // base64 terminal bytes
| { type: 'cost_update'; session_id: string; cost_usd: number; input_tokens: number; output_tokens: number }

// WebSocket: client → server (replaces chat/cancel/resume/permission_response)
| { type: 'input'; session_id: string; data: string }         // base64 keystrokes
| { type: 'resize'; session_id: string; cols: number; rows: number }
```

Remove from `SessionState`: `streamingMessages`, `pendingPermissions`.

### App.tsx changes

- Remove `handleSend`, `handleAllow`, `handleDeny` handlers
- Remove `job_update`, `token`, `usage`, `error`, `permission_request`, `tool_call` WS message handlers
- Add `output` handler: forwards bytes to `TerminalTab` via a ref/callback
- Add `cost_update` handler: updates session cost/token totals
- Replace `<WorkTab ... />` with `<TerminalTab sessionId={activeSession.session_id} ws={wsRef.current} />`

### PermissionDialog.tsx

Deleted — no longer needed.

---

## File Map

```
# Backend
harness_claw/pty_session.py         CREATE  — PtySession class
harness_claw/cost_poller.py         CREATE  — JSONL-based cost polling
harness_claw/job_runner.py          MODIFY  — use PtySession, remove stream-json job logic
harness_claw/server.py              MODIFY  — updated WS message handling
harness_claw/providers/claude_code.py  DELETE  — replaced by PtySession
pyproject.toml                      MODIFY  — add ptyprocess

# Tests
tests/test_pty_session.py           CREATE
tests/test_cost_poller.py           CREATE
tests/test_claude_code_provider.py  DELETE  — provider removed

# Frontend
ui/src/components/TerminalTab.tsx   CREATE
ui/src/components/WorkTab.tsx       DELETE
ui/src/components/PermissionDialog.tsx  DELETE
ui/src/types.ts                     MODIFY
ui/src/App.tsx                      MODIFY
ui/package.json                     MODIFY  — add xterm packages
```

---

## Error Handling

- **PTY process exits unexpectedly**: `read_loop` ends; `session_update` with `status: killed` sent to browser; terminal shows the exit output (Claude Code prints its own error)
- **Working directory doesn't exist**: validated on `POST /api/sessions`, returns 422
- **`claude` not found on PATH**: `PtySession.start()` raises, session creation returns 500 with clear message
- **JSONL file not found** (cost polling): silently skip poll; cost stays at last known value

---

## Testing

- Unit tests for `PtySession`: mock `ptyprocess.PtyProcess`, verify start/write/resize/kill calls
- Unit tests for `cost_poller`: write a fake JSONL file, verify cost sums and `cost_update` emission
- Manual browser testing: terminal renders, keystrokes work, resize works, cost updates
