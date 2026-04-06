# Tool Output Streaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream labeled tool output to the calling agent's terminal in real time as registered tools execute.

**Architecture:** Add an `on_output: Callable[[bytes], Awaitable[None]] | None` parameter to `ToolExecutor.execute()` and thread it through each adapter. CLI streams chunks as they arrive; HTTP and MCP emit a start banner then done+body. In `server.py`, both dispatch paths extract the caller's `session_id` from the validated token and wire a broadcast callback that emits `type: "output"` WebSocket messages — which the existing frontend handler routes to both the terminal tab and task panel automatically.

**Tech Stack:** Python asyncio, pytest-asyncio, FastAPI TestClient, httpx mock patches

---

## File Map

| File | Change |
|---|---|
| `harness_claw/runtime/tool_executor.py` | Add `on_output` param to `execute()`, `_dispatch()`, and all three adapters; add `_safe_emit` helper |
| `harness_claw/server.py` | Add `import base64`; wire `on_output` in `_dispatch_tool` and `mcp_tool_call` |
| `tests/runtime/test_tool_executor.py` | Add streaming tests for CLI, HTTP, MCP adapters |
| `tests/test_server_endpoints.py` | Add integration test verifying broadcast is called during fallthrough tool execution |

---

### Task 1: CLI adapter — real-time streaming

**Files:**
- Modify: `harness_claw/runtime/tool_executor.py`
- Modify: `tests/runtime/test_tool_executor.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/runtime/test_tool_executor.py`:

```python
@pytest.mark.asyncio
async def test_cli_on_output_receives_labeled_chunks(tmp_path):
    """on_output is called with [tool: id]-prefixed chunks during CLI execution."""
    executor = make_executor(tmp_path, [make_tool()])
    chunks: list[bytes] = []

    async def collect(data: bytes) -> None:
        chunks.append(data)

    result = await executor.execute("echo_tool", {"msg": "streaming"}, on_output=collect)
    assert result["exit_code"] == 0
    combined = b"".join(chunks)
    assert b"[tool: echo_tool]" in combined
    assert b"streaming" in combined


@pytest.mark.asyncio
async def test_cli_on_output_exit_status_emitted(tmp_path):
    """A final exit-status line is emitted after the process finishes."""
    executor = make_executor(tmp_path, [make_tool()])
    chunks: list[bytes] = []

    async def collect(data: bytes) -> None:
        chunks.append(data)

    await executor.execute("echo_tool", {"msg": "hi"}, on_output=collect)
    combined = b"".join(chunks)
    assert b"\xe2\x9c\x93 exit 0" in combined or b"exit 0" in combined


@pytest.mark.asyncio
async def test_cli_no_on_output_still_works(tmp_path):
    """Passing no on_output (default None) produces the same result as before."""
    executor = make_executor(tmp_path, [make_tool()])
    result = await executor.execute("echo_tool", {"msg": "no-callback"})
    assert result["exit_code"] == 0
    assert "no-callback" in result["stdout"]


@pytest.mark.asyncio
async def test_cli_on_output_exception_does_not_break_execution(tmp_path):
    """If on_output raises, tool execution completes normally."""
    executor = make_executor(tmp_path, [make_tool()])

    async def bad_callback(data: bytes) -> None:
        raise RuntimeError("callback exploded")

    result = await executor.execute("echo_tool", {"msg": "safe"}, on_output=bad_callback)
    assert result["exit_code"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/juichanglu/src/HarnessClaw
python -m pytest tests/runtime/test_tool_executor.py::test_cli_on_output_receives_labeled_chunks tests/runtime/test_tool_executor.py::test_cli_on_output_exit_status_emitted tests/runtime/test_tool_executor.py::test_cli_no_on_output_still_works tests/runtime/test_tool_executor.py::test_cli_on_output_exception_does_not_break_execution -v
```

Expected: FAIL — `execute()` does not accept `on_output` kwarg.

- [ ] **Step 3: Implement CLI streaming in `tool_executor.py`**

Replace the entire file with:

```python
from __future__ import annotations

import asyncio
import json as _json
import re
import time
from typing import Any, Awaitable, Callable

import httpx
from jsonschema import validate

from harness_claw.gateway.tool_registry import ToolRegistry
from harness_claw.gateway.tool_store import ToolDef

OnOutput = Callable[[bytes], Awaitable[None]] | None


async def _safe_emit(on_output: OnOutput, data: bytes) -> None:
    """Call on_output, swallowing any exception so tool execution is never interrupted."""
    if on_output is None:
        return
    try:
        await on_output(data)
    except Exception:
        pass


class ToolExecutor:
    """Routes registered tool calls to the appropriate execution adapter.

    Validates input against tool.input_schema before dispatch and output
    against tool.output_schema before returning. Raises jsonschema.ValidationError
    on schema violations and KeyError for unknown tool IDs.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(
        self,
        tool_id: str,
        arguments: dict,
        on_output: OnOutput = None,
    ) -> dict:
        tool = self._registry.get(tool_id)
        if tool is None:
            raise KeyError(f"Tool {tool_id!r} not registered")
        validate(instance=arguments, schema=tool.input_schema)
        result = await self._dispatch(tool, arguments, on_output)
        validate(instance=result, schema=tool.output_schema)
        return result

    async def _dispatch(self, tool: ToolDef, args: dict, on_output: OnOutput) -> dict:
        if tool.adapter == "cli":
            return await self._run_cli(tool, args, on_output)
        if tool.adapter == "http":
            return await self._run_http(tool, args, on_output)
        if tool.adapter == "mcp":
            return await self._run_mcp(tool, args, on_output)
        raise ValueError(f"Unknown adapter: {tool.adapter!r}")

    async def _run_cli(self, tool: ToolDef, args: dict, on_output: OnOutput) -> dict:
        config = tool.adapter_config
        command = config["command"]
        for key, value in args.items():
            command = command.replace(f"{{{{{key}}}}}", str(value))
        if re.search(r'\{\{[^}]+\}\}', command):
            remaining = re.findall(r'\{\{([^}]+)\}\}', command)
            raise ValueError(f"Unresolved template placeholders in CLI command: {remaining}")
        timeout = float(config.get("timeout", 30))
        working_dir = config.get("working_dir")
        prefix = f"[tool: {tool.id}] ".encode()

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )

        stdout_buf = bytearray()
        stderr_buf = bytearray()

        async def _read(stream: asyncio.StreamReader, buf: bytearray) -> None:
            while True:
                chunk = await stream.read(1024)
                if not chunk:
                    break
                buf.extend(chunk)
                await _safe_emit(on_output, prefix + chunk)

        try:
            await asyncio.wait_for(
                asyncio.gather(
                    _read(proc.stdout, stdout_buf),
                    _read(proc.stderr, stderr_buf),
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"Tool {tool.id!r} timed out after {timeout}s")

        await proc.wait()
        exit_sym = "✓" if proc.returncode == 0 else "✕"
        await _safe_emit(on_output, f"{prefix.decode()}{exit_sym} exit {proc.returncode}\r\n".encode())

        return {
            "stdout": bytes(stdout_buf).decode(),
            "stderr": bytes(stderr_buf).decode(),
            "exit_code": proc.returncode,
        }

    async def _run_http(self, tool: ToolDef, args: dict, on_output: OnOutput) -> dict:
        config = tool.adapter_config
        url = config["url"]
        method = config.get("method", "POST").upper()
        headers = config.get("headers", {})
        timeout = float(config.get("timeout", 30))
        prefix = f"[tool: {tool.id}] "

        await _safe_emit(on_output, f"{prefix}▶ {method} {url}\r\n".encode())

        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, json=args, headers=headers)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            response.raise_for_status()
            result = response.json()

        body = _json.dumps(result, indent=2)
        await _safe_emit(on_output, f"{prefix}✓ {response.status_code} ({elapsed_ms}ms)\r\n{body}\r\n".encode())
        return result

    async def _run_mcp(self, tool: ToolDef, args: dict, on_output: OnOutput) -> dict:
        config = tool.adapter_config
        server_url = config["server_url"]
        tool_name = config["tool_name"]
        timeout = float(config.get("timeout", 30))
        prefix = f"[tool: {tool.id}] "

        await _safe_emit(on_output, f"{prefix}▶ → {server_url} {tool_name}\r\n".encode())

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{server_url}/mcp/tools/call",
                json={"name": tool_name, "arguments": args},
            )
            response.raise_for_status()
            result = response.json()

        body = _json.dumps(result, indent=2)
        await _safe_emit(on_output, f"{prefix}✓ done\r\n{body}\r\n".encode())
        return result
```

- [ ] **Step 4: Run the new CLI tests**

```bash
python -m pytest tests/runtime/test_tool_executor.py::test_cli_on_output_receives_labeled_chunks tests/runtime/test_tool_executor.py::test_cli_on_output_exit_status_emitted tests/runtime/test_tool_executor.py::test_cli_no_on_output_still_works tests/runtime/test_tool_executor.py::test_cli_on_output_exception_does_not_break_execution -v
```

Expected: all 4 PASS.

- [ ] **Step 5: Run the full tool_executor test suite to check for regressions**

```bash
python -m pytest tests/runtime/test_tool_executor.py -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add harness_claw/runtime/tool_executor.py tests/runtime/test_tool_executor.py
git commit -m "feat: add on_output streaming to ToolExecutor — CLI streams chunks, HTTP/MCP emit start+done+body"
```

---

### Task 2: HTTP and MCP adapter output tests

**Files:**
- Modify: `tests/runtime/test_tool_executor.py`

The HTTP and MCP implementations are already in the file from Task 1. This task adds their tests.

- [ ] **Step 1: Write the failing HTTP and MCP tests**

Add to `tests/runtime/test_tool_executor.py`:

```python
@pytest.mark.asyncio
async def test_http_on_output_emits_start_and_done(tmp_path):
    """HTTP adapter emits start banner then status+body via on_output."""
    tool = make_tool(
        adapter="http",
        adapter_config={"url": "http://fake/api", "method": "POST"},
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    executor = make_executor(tmp_path, [tool])
    chunks: list[bytes] = []

    async def collect(data: bytes) -> None:
        chunks.append(data)

    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True}
    mock_response.raise_for_status = MagicMock()
    mock_response.status_code = 200

    with patch("harness_claw.runtime.tool_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        await executor.execute("echo_tool", {}, on_output=collect)

    combined = b"".join(chunks)
    assert b"[tool: echo_tool]" in combined
    assert b"\xe2\x96\xb6" in combined or b"POST" in combined   # ▶ banner
    assert b"200" in combined
    assert b'"ok": true' in combined or b'"ok":true' in combined


@pytest.mark.asyncio
async def test_mcp_on_output_emits_start_and_done(tmp_path):
    """MCP adapter emits start banner then done+body via on_output."""
    tool = make_tool(
        adapter="mcp",
        adapter_config={"server_url": "http://mcp-server", "tool_name": "do_thing"},
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    executor = make_executor(tmp_path, [tool])
    chunks: list[bytes] = []

    async def collect(data: bytes) -> None:
        chunks.append(data)

    mock_response = MagicMock()
    mock_response.json.return_value = {"done": 1}
    mock_response.raise_for_status = MagicMock()

    with patch("harness_claw.runtime.tool_executor.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        await executor.execute("echo_tool", {}, on_output=collect)

    combined = b"".join(chunks)
    assert b"[tool: echo_tool]" in combined
    assert b"do_thing" in combined
    assert b"done" in combined
    assert b'"done": 1' in combined or b'"done":1' in combined
```

- [ ] **Step 2: Run the new HTTP and MCP tests**

```bash
python -m pytest tests/runtime/test_tool_executor.py::test_http_on_output_emits_start_and_done tests/runtime/test_tool_executor.py::test_mcp_on_output_emits_start_and_done -v
```

Expected: both PASS (implementations already in place from Task 1).

- [ ] **Step 3: Run full suite one more time**

```bash
python -m pytest tests/runtime/test_tool_executor.py -v
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/runtime/test_tool_executor.py
git commit -m "test: add on_output streaming tests for HTTP and MCP adapters"
```

---

### Task 3: Wire `on_output` in server.py

**Files:**
- Modify: `harness_claw/server.py`
- Modify: `tests/test_server_endpoints.py`

- [ ] **Step 1: Write the failing integration test**

Add to `tests/test_server_endpoints.py` (note: sync function, matching the existing test style in this file):

```python
def test_fallthrough_tool_broadcasts_output(client):
    """When a registered CLI tool is called via /mcp/tools/call, runner._broadcast
    is called with type='output' messages containing labeled tool output."""
    import base64 as _b64
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock, patch
    from harness_claw.server import runner, token_store, tool_registry
    from harness_claw.gateway.tool_store import ToolDef

    now = datetime.now(timezone.utc).isoformat()
    tool = ToolDef(
        id="stream_test_tool",
        name="stream_test_tool",
        description="test",
        input_schema={"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
        output_schema={"type": "object"},
        adapter="cli",
        adapter_config={"command": "echo {{msg}}", "timeout": 5},
        cap_allowlist=[], allowed_roles=[], required_scopes=[],
        metadata={}, created_at=now, updated_at=now,
    )
    tool_registry.save(tool)

    token = token_store.issue("sess-stream-test", ["tool:use"])
    mock_broadcast = AsyncMock()

    with patch.object(runner, "_broadcast", mock_broadcast):
        resp = client.post(
            "/mcp/tools/call",
            json={"name": "stream_test_tool", "arguments": {"msg": "hello-stream"}},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 200
    output_calls = [
        call.args[0] for call in mock_broadcast.call_args_list
        if call.args[0].get("type") == "output"
    ]
    assert len(output_calls) > 0
    combined = b"".join(_b64.b64decode(m["data"]) for m in output_calls)
    assert b"[tool: stream_test_tool]" in combined
    assert b"hello-stream" in combined

    tool_registry.delete("stream_test_tool")
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
python -m pytest tests/test_server_endpoints.py::test_fallthrough_tool_broadcasts_output -v
```

Expected: FAIL — `_broadcast` is not currently called during tool execution.

- [ ] **Step 3: Add `import base64` to `server.py`**

In `harness_claw/server.py`, find the imports block at the top. After the existing stdlib imports (after `import json`), add:

```python
import base64
```

- [ ] **Step 4: Wire `on_output` in `mcp_tool_call` in `server.py`**

Find the fallthrough block in `mcp_tool_call` (around line 199):

```python
            result = await tool_executor.execute(tool, args)
```

Replace it with:

```python
            async def _on_output(data: bytes) -> None:
                await runner._broadcast({
                    "type": "output",
                    "session_id": subject,
                    "data": base64.b64encode(data).decode(),
                })
            result = await tool_executor.execute(tool, args, on_output=_on_output)
```

- [ ] **Step 5: Wire `on_output` in `_dispatch_tool` in `server.py`**

Find the fallthrough block in `_dispatch_tool` (around line 602):

```python
    return await tool_executor.execute(name, args)
```

Replace it with:

```python
    async def _on_output(data: bytes) -> None:
        await runner._broadcast({
            "type": "output",
            "session_id": subject,
            "data": base64.b64encode(data).decode(),
        })
    return await tool_executor.execute(name, args, on_output=_on_output)
```

- [ ] **Step 6: Run the integration test**

```bash
python -m pytest tests/test_server_endpoints.py::test_fallthrough_tool_broadcasts_output -v
```

Expected: PASS.

- [ ] **Step 7: Run full test suite**

```bash
python -m pytest -v
```

Expected: all tests PASS. If any fail, fix before committing.

- [ ] **Step 8: Commit**

```bash
git add harness_claw/server.py tests/test_server_endpoints.py
git commit -m "feat: wire on_output broadcast in server.py — tool output streams to caller session via WebSocket"
```
