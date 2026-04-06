# Tool Output Streaming Design

**Date:** 2026-04-05
**Status:** Approved

## Problem

When an agent calls a registered tool, the tool executes silently — no output is visible in the dashboard until the tool finishes and returns a result. For long-running CLI tools or slow HTTP/MCP calls, there is no feedback.

## Goal

Stream labeled tool output to the calling agent's terminal (work tab) and task panel in real time, over the existing WebSocket broadcast infrastructure.

## Architecture

### ToolExecutor

Add an optional `on_output` callback to `execute()`:

```python
async def execute(
    self,
    tool_id: str,
    arguments: dict,
    on_output: Callable[[bytes], Awaitable[None]] | None = None,
) -> dict
```

All output emitted through `on_output` is prefixed `[tool: <tool_id>] ` so it is visually distinct from agent PTY output.

### CLI Adapter

Replace `proc.communicate()` with two concurrent `asyncio.create_task` readers — one on stdout, one on stderr. Each chunk is passed to `on_output` as it arrives. After the process exits, emit a final status line:

```
[tool: my-tool] ✓ exit 0
```
or
```
[tool: my-tool] ✕ exit 1
```

### HTTP Adapter

Emit a start banner before sending the request:
```
[tool: my-tool] ▶ POST https://example.com/endpoint
```

After the response arrives, emit status code, elapsed time, and full response body:
```
[tool: my-tool] ✓ 200 (142ms)
{"result": ...}
```

### MCP Adapter

Emit a start banner:
```
[tool: my-tool] ▶ → mcp-server tool_name
```

After the response, emit done + full response body:
```
[tool: my-tool] ✓ done
{"result": ...}
```

### server.py — wiring

Both dispatch paths (`_dispatch_tool` for MCP SSE, `mcp_tool_call` for HTTP POST) are updated. When routing falls through to `tool_executor.execute()`, the caller's `session_id` is extracted from the validated token (`subject`), and an `on_output` callback is constructed:

```python
async def on_output(data: bytes) -> None:
    await runner._broadcast({
        "type": "output",
        "session_id": subject,
        "data": base64.b64encode(data).decode(),
    })
result = await tool_executor.execute(name, args, on_output=on_output)
```

### Frontend

No changes required. The existing `type: "output"` WebSocket message handler in `App.tsx` already routes output to:
- `terminalWriters.current[session_id]` — the main terminal tab
- `terminalWriters.current["task:" + session_id]` — the inline task panel terminal

## Data Flow

```
Agent (Claude PTY)
  → /mcp/sse or /mcp/tools/call
    → _dispatch_tool / mcp_tool_call
      → token_store.validate(token) → subject (= session_id)
      → tool_executor.execute(name, args, on_output)
        → adapter (_run_cli / _run_http / _run_mcp)
          → on_output(labeled_chunk)
            → runner._broadcast({ type: "output", session_id, data })
              → WebSocket → frontend
                → TerminalTab (work tab) for that session
                → TaskTerminalPanel (task tab) for that session
```

## Error Handling

- If `on_output` raises, the exception is ignored (best-effort streaming) — tool execution continues and returns its result normally.
- Timeout behavior is unchanged; CLI timeout still kills the process and raises `TimeoutError`.

## Testing

- `ToolExecutor` unit tests: pass a mock `on_output` collector and assert chunks arrive in order with correct labels.
- CLI tests: verify concurrent stdout+stderr are both captured.
- HTTP/MCP tests: verify start banner and done+body are emitted.
- `server.py` integration: verify `on_output` is wired when subject is available; verify graceful no-op when token has no subject (e.g. internal calls).
