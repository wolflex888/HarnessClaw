# RPC Code Execution Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `code.exec` MCP tool that runs Python scripts in a subprocess with RPC access to every HarnessClaw tool. Only the script's stdout/stderr/exit_code returns to the LLM — intermediate tool calls stay out of the context window.

**Architecture:** A new built-in MCP tool on `GatewayMCP` spawns a Python subprocess and an asyncio UDS server. The subprocess imports `harnessclaw_tools` (a stub module copied into its cwd) and calls `_call(tool, args)`; each call travels over the UDS as newline-delimited JSON and is dispatched by the server through the same `_dispatch_tool` used by the MCP HTTP endpoint. Policy, scopes, and audit apply to every RPC call because they flow through the existing dispatcher.

**Tech Stack:** Python 3.12, asyncio, unix domain sockets, pytest-asyncio. No new dependencies.

---

## File Structure

**New files:**
- `harness_claw/runtime/rpc_stub.py` — the Python stub module copied into the sandbox as `harnessclaw_tools.py`. Stdlib-only, lazy connect.
- `harness_claw/runtime/code_exec.py` — `run_rpc_script` coroutine: spawn subprocess + UDS server, dispatch RPC calls, capture stdout, enforce limits.
- `tests/runtime/test_code_exec.py` — unit tests for the executor.
- `tests/gateway/test_execute_code.py` — unit tests for `GatewayMCP.execute_code`.

**Modified files:**
- `harness_claw/gateway/mcp_server.py` — add `execute_code` method.
- `harness_claw/server.py` — register `code.exec` in `_MCP_TOOLS`, both handler dicts, `_dispatch_tool`, and `/api/mcp/tools`.
- `agents.yaml` — grant `code:exec` scope to the orchestrator role.

---

## Task 1: RPC stub module

**Files:**
- Create: `harness_claw/runtime/rpc_stub.py`
- Test: `tests/runtime/test_code_exec.py`

This file will be **copied** into the sandbox's working directory as `harnessclaw_tools.py`. It is never imported by HarnessClaw itself. Keep it stdlib-only, with lazy socket connection so that importing it without the env var does not crash.

- [ ] **Step 1: Write the failing test**

Create `tests/runtime/test_code_exec.py`:

```python
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from harness_claw.runtime import rpc_stub


def test_stub_module_parses_as_python():
    src = Path(rpc_stub.__file__).read_text()
    ast.parse(src)


def test_stub_module_exposes_underscore_call():
    src = Path(rpc_stub.__file__).read_text()
    assert "def _call(" in src


def test_stub_module_uses_only_stdlib():
    src = Path(rpc_stub.__file__).read_text()
    tree = ast.parse(src)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(n.name for n in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    stdlib = {"json", "os", "socket"}
    assert set(imports) <= stdlib, f"non-stdlib import: {set(imports) - stdlib}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/runtime/test_code_exec.py -v`
Expected: FAIL — `ModuleNotFoundError: harness_claw.runtime.rpc_stub`

- [ ] **Step 3: Implement the stub module**

Create `harness_claw/runtime/rpc_stub.py`:

```python
"""RPC stub module.

This file is copied into a sandbox cwd as ``harnessclaw_tools.py`` by
``harness_claw.runtime.code_exec.run_rpc_script``. The sandboxed Python
process imports it and uses ``_call(tool_name, args)`` to invoke HarnessClaw
tools over a unix domain socket.

Keep imports to stdlib only. Connect lazily — the env var may not be set at
import time during test collection.
"""
import json
import os
import socket


class _RPCClient:
    def __init__(self) -> None:
        self._sock: socket.socket | None = None
        self._buf = b""

    def _connect(self) -> None:
        path = os.environ["HARNESSCLAW_RPC_SOCKET"]
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(path)

    def _readline(self) -> bytes:
        assert self._sock is not None
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("RPC server closed connection")
            self._buf += chunk
        line, _, self._buf = self._buf.partition(b"\n")
        return line

    def call(self, name, args=None):
        if self._sock is None:
            self._connect()
        assert self._sock is not None
        req = json.dumps({"tool": name, "args": args or {}}) + "\n"
        self._sock.sendall(req.encode())
        line = self._readline()
        resp = json.loads(line)
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp["result"]


_client = _RPCClient()


def _call(name, args=None):
    """Invoke a HarnessClaw tool via RPC. Returns the tool's result."""
    return _client.call(name, args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/runtime/test_code_exec.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/runtime/rpc_stub.py tests/runtime/test_code_exec.py
git commit -m "feat(runtime): add rpc_stub module for code-exec sandbox"
```

---

## Task 2: `run_rpc_script` happy path

**Files:**
- Create: `harness_claw/runtime/code_exec.py`
- Test: `tests/runtime/test_code_exec.py`

The executor spawns a Python subprocess with `PYTHONPATH` and `HARNESSCLAW_RPC_SOCKET` set, copies the stub into the sandbox cwd, and starts an asyncio UDS server that dispatches each incoming request through a callback.

- [ ] **Step 1: Write the failing test**

Append to `tests/runtime/test_code_exec.py`:

```python
import pytest

from harness_claw.runtime.code_exec import CodeExecResult, run_rpc_script


@pytest.mark.asyncio
async def test_run_rpc_script_returns_stdout():
    async def dispatch(token: str, name: str, args: dict):
        return {"echoed": name, "args": args}

    script = (
        "import harnessclaw_tools as h\n"
        "r = h._call('memory.list', {'namespace': 'foo'})\n"
        "print(r['echoed'])\n"
    )
    result = await run_rpc_script(script, token="t", dispatch=dispatch)
    assert isinstance(result, CodeExecResult)
    assert result.exit_code == 0
    assert result.stdout.strip() == "memory.list"
    assert result.tool_calls == 1


@pytest.mark.asyncio
async def test_run_rpc_script_forwards_errors_from_dispatch():
    async def dispatch(token: str, name: str, args: dict):
        raise ValueError("nope")

    script = (
        "import harnessclaw_tools as h\n"
        "try:\n"
        "    h._call('x')\n"
        "except RuntimeError as e:\n"
        "    print('caught:', e)\n"
    )
    result = await run_rpc_script(script, token="t", dispatch=dispatch)
    assert result.exit_code == 0
    assert "caught: nope" in result.stdout


@pytest.mark.asyncio
async def test_run_rpc_script_captures_stderr_and_nonzero_exit():
    async def dispatch(token: str, name: str, args: dict):
        return None

    script = "import sys; sys.stderr.write('oh no'); sys.exit(3)\n"
    result = await run_rpc_script(script, token="t", dispatch=dispatch)
    assert result.exit_code == 3
    assert "oh no" in result.stderr
    assert result.tool_calls == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/runtime/test_code_exec.py::test_run_rpc_script_returns_stdout -v`
Expected: FAIL — `ModuleNotFoundError: harness_claw.runtime.code_exec`

- [ ] **Step 3: Implement the executor**

Create `harness_claw/runtime/code_exec.py`:

```python
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

_STUB_SRC = Path(__file__).parent / "rpc_stub.py"

DispatchFn = Callable[[str, str, dict], Awaitable[Any]]


@dataclass
class CodeExecResult:
    stdout: str
    stderr: str
    exit_code: int
    tool_calls: int
    timed_out: bool


async def run_rpc_script(
    script: str,
    *,
    token: str,
    dispatch: DispatchFn,
    max_tool_calls: int = 100,
    timeout_seconds: float = 120.0,
) -> CodeExecResult:
    """Run ``script`` in a Python subprocess with RPC access to HarnessClaw tools.

    The script can ``import harnessclaw_tools as h`` and call ``h._call(name, args)``
    to invoke any tool the caller's token is authorized for. Intermediate calls
    do not appear in the return value — only stdout/stderr/exit_code.
    """
    with tempfile.TemporaryDirectory(prefix="hc_exec_") as tmpdir:
        tmp = Path(tmpdir)
        sock_path = tmp / "rpc.sock"
        shutil.copy(_STUB_SRC, tmp / "harnessclaw_tools.py")
        (tmp / "script.py").write_text(script)

        state = {"count": 0}

        async def handle_client(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                while True:
                    line = await reader.readline()
                    if not line:
                        return
                    try:
                        msg = json.loads(line.decode())
                        name = msg["tool"]
                        args = msg.get("args", {}) or {}
                    except Exception as e:
                        writer.write(
                            (json.dumps({"error": f"bad request: {e}"}) + "\n").encode()
                        )
                        await writer.drain()
                        continue

                    state["count"] += 1
                    try:
                        result = await dispatch(token, name, args)
                        writer.write((json.dumps({"result": result}) + "\n").encode())
                    except Exception as e:
                        writer.write((json.dumps({"error": str(e)}) + "\n").encode())
                    await writer.drain()
            finally:
                writer.close()

        server = await asyncio.start_unix_server(handle_client, path=str(sock_path))

        env = {
            **os.environ,
            "HARNESSCLAW_RPC_SOCKET": str(sock_path),
            "PYTHONPATH": str(tmp),
        }
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(tmp / "script.py"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=str(tmp),
        )

        timed_out = False
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds
            )
            exit_code = proc.returncode if proc.returncode is not None else 0
        except asyncio.TimeoutError:
            timed_out = True
            proc.kill()
            stdout_b, stderr_b = await proc.communicate()
            exit_code = -1
        finally:
            server.close()
            await server.wait_closed()

        return CodeExecResult(
            stdout=stdout_b.decode(errors="replace"),
            stderr=stderr_b.decode(errors="replace"),
            exit_code=exit_code,
            tool_calls=state["count"],
            timed_out=timed_out,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/runtime/test_code_exec.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/runtime/code_exec.py tests/runtime/test_code_exec.py
git commit -m "feat(runtime): add run_rpc_script with UDS tool dispatch"
```

---

## Task 3: Enforce `max_tool_calls`, timeout, and self-recursion block

**Files:**
- Modify: `harness_claw/runtime/code_exec.py`
- Test: `tests/runtime/test_code_exec.py`

Prevent: scripts making unbounded RPC calls, scripts hanging forever, and scripts invoking `code.exec` recursively from inside themselves.

- [ ] **Step 1: Write the failing tests**

Append to `tests/runtime/test_code_exec.py`:

```python
@pytest.mark.asyncio
async def test_max_tool_calls_returns_error_to_script():
    async def dispatch(token: str, name: str, args: dict):
        return "ok"

    script = (
        "import harnessclaw_tools as h\n"
        "for i in range(5):\n"
        "    try:\n"
        "        print(i, h._call('x'))\n"
        "    except RuntimeError as e:\n"
        "        print('stop:', e); break\n"
    )
    result = await run_rpc_script(script, token="t", dispatch=dispatch, max_tool_calls=2)
    assert result.exit_code == 0
    assert "stop: max_tool_calls" in result.stdout
    assert result.tool_calls == 2


@pytest.mark.asyncio
async def test_timeout_kills_long_running_script():
    async def dispatch(token: str, name: str, args: dict):
        return None

    script = "import time; time.sleep(10); print('never')\n"
    result = await run_rpc_script(
        script, token="t", dispatch=dispatch, timeout_seconds=0.5
    )
    assert result.timed_out is True
    assert result.exit_code == -1
    assert "never" not in result.stdout


@pytest.mark.asyncio
async def test_script_cannot_call_code_exec_recursively():
    async def dispatch(token: str, name: str, args: dict):
        return "ok"  # would succeed if not blocked

    script = (
        "import harnessclaw_tools as h\n"
        "try:\n"
        "    h._call('code.exec', {'script': 'print(1)'})\n"
        "except RuntimeError as e:\n"
        "    print('blocked:', e)\n"
    )
    result = await run_rpc_script(script, token="t", dispatch=dispatch)
    assert result.exit_code == 0
    assert "blocked: code.exec is not callable" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/runtime/test_code_exec.py -v`
Expected: 3 new tests FAIL.

- [ ] **Step 3: Add the enforcement logic**

In `harness_claw/runtime/code_exec.py`, replace the `handle_client` function body with:

```python
        BLOCKED_TOOLS = {"code.exec"}

        async def handle_client(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                while True:
                    line = await reader.readline()
                    if not line:
                        return
                    try:
                        msg = json.loads(line.decode())
                        name = msg["tool"]
                        args = msg.get("args", {}) or {}
                    except Exception as e:
                        writer.write(
                            (json.dumps({"error": f"bad request: {e}"}) + "\n").encode()
                        )
                        await writer.drain()
                        continue

                    if name in BLOCKED_TOOLS:
                        writer.write(
                            (json.dumps({"error": f"{name} is not callable from code.exec"}) + "\n").encode()
                        )
                        await writer.drain()
                        continue

                    if state["count"] >= max_tool_calls:
                        writer.write(
                            (json.dumps({"error": f"max_tool_calls ({max_tool_calls}) exceeded"}) + "\n").encode()
                        )
                        await writer.drain()
                        continue

                    state["count"] += 1
                    try:
                        result = await dispatch(token, name, args)
                        writer.write((json.dumps({"result": result}) + "\n").encode())
                    except Exception as e:
                        writer.write((json.dumps({"error": str(e)}) + "\n").encode())
                    await writer.drain()
            finally:
                writer.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/runtime/test_code_exec.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/runtime/code_exec.py tests/runtime/test_code_exec.py
git commit -m "feat(runtime): enforce max_tool_calls, timeout, and block code.exec recursion"
```

---

## Task 4: Add `execute_code` to `GatewayMCP`

**Files:**
- Modify: `harness_claw/gateway/mcp_server.py`
- Test: `tests/gateway/test_execute_code.py`

Thin auth-and-audit wrapper over `run_rpc_script`. The dispatch callback is passed at call time by `server.py` because `_dispatch_tool` closes over `gateway_mcp` itself.

- [ ] **Step 1: Write the failing test**

Create `tests/gateway/test_execute_code.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from harness_claw.gateway.audit import AuditLogger
from harness_claw.gateway.auth import TokenStore
from harness_claw.gateway.broker import Broker, LocalDispatcher
from harness_claw.gateway.capability import LocalConnector
from harness_claw.gateway.event_bus import LocalEventBus
from harness_claw.gateway.memory import SqliteMemoryStore
from harness_claw.gateway.mcp_server import GatewayMCP, PermissionError as GwPermissionError
from harness_claw.gateway.policy import LocalPolicyEngine
from harness_claw.gateway.task_store import SqliteTaskStore


def make_gw(tmp_path):
    ts = TokenStore()
    memory = SqliteMemoryStore(tmp_path / "m.db")
    task_store = SqliteTaskStore(tmp_path / "t.db")
    connector = LocalConnector()
    broker = Broker(
        connectors=[connector],
        dispatcher=LocalDispatcher(),
        event_bus=LocalEventBus(),
        task_store=task_store,
    )
    return GatewayMCP(
        token_store=ts,
        policy=LocalPolicyEngine(),
        connectors=[connector],
        broker=broker,
        memory=memory,
        audit=AuditLogger(tmp_path / "audit.jsonl"),
    ), ts


@pytest.mark.asyncio
async def test_execute_code_requires_code_exec_scope(tmp_path):
    gw, ts = make_gw(tmp_path)
    token = ts.issue("subject", scopes=["memory:read"])  # missing code:exec

    async def dispatch(t, n, a):
        return None

    with pytest.raises(GwPermissionError):
        await gw.execute_code(token=token, script="print(1)", dispatch=dispatch)


@pytest.mark.asyncio
async def test_execute_code_runs_script_and_returns_stdout(tmp_path):
    gw, ts = make_gw(tmp_path)
    token = ts.issue("subject", scopes=["code:exec"])

    async def dispatch(t, n, a):
        return {"n": n, "a": a}

    script = (
        "import harnessclaw_tools as h\n"
        "r = h._call('memory.list', {'namespace': 'foo'})\n"
        "print(r['n'])\n"
    )
    out = await gw.execute_code(token=token, script=script, dispatch=dispatch)
    assert out["exit_code"] == 0
    assert out["stdout"].strip() == "memory.list"
    assert out["tool_calls"] == 1
    assert out["timed_out"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/gateway/test_execute_code.py -v`
Expected: FAIL — `AttributeError: 'GatewayMCP' object has no attribute 'execute_code'`

- [ ] **Step 3: Implement `execute_code`**

In `harness_claw/gateway/mcp_server.py`, add an import at the top:

```python
from harness_claw.runtime.code_exec import run_rpc_script, DispatchFn
```

Then add this method to `GatewayMCP` (place it right after `execute_plan`):

```python
    async def execute_code(
        self,
        *,
        token: str,
        script: str,
        dispatch: DispatchFn,
        timeout_seconds: float = 120.0,
        max_tool_calls: int = 100,
    ) -> dict[str, Any]:
        """Run a Python script in a subprocess with RPC access to HarnessClaw tools.

        Only stdout/stderr/exit_code return to the caller. Intermediate tool
        calls stay out of the LLM context window.
        """
        subject = self._auth(token, "code:exec")
        result = await run_rpc_script(
            script,
            token=token,
            dispatch=dispatch,
            max_tool_calls=max_tool_calls,
            timeout_seconds=timeout_seconds,
        )
        self._audit.log(AuditEvent(
            subject=subject, operation="code:exec", resource="sandbox",
            outcome="allowed", details={
                "script_bytes": len(script.encode()),
                "tool_calls": result.tool_calls,
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
            },
        ))
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.exit_code,
            "tool_calls": result.tool_calls,
            "timed_out": result.timed_out,
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/gateway/test_execute_code.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add harness_claw/gateway/mcp_server.py tests/gateway/test_execute_code.py
git commit -m "feat(gateway): add GatewayMCP.execute_code with scope + audit"
```

---

## Task 5: Wire `code.exec` as an MCP tool

**Files:**
- Modify: `harness_claw/server.py`

Three places register MCP tools: the `_MCP_TOOLS` list (SSE schema), the `handlers` dict in `/mcp/tools/call` (HTTP POST), `_dispatch_tool` (SSE call_tool), and `/api/mcp/tools` (dashboard listing). Add `code.exec` to all four, using `_dispatch_tool` itself as the callback. The forward reference resolves at call time.

- [ ] **Step 1: Register the tool in `server.py`**

Open `harness_claw/server.py`.

**(a)** In the `handlers` dict inside `mcp_tool_call` (around `server.py:204`), add:

```python
        "code.exec": lambda a: gateway_mcp.execute_code(token=token, dispatch=_dispatch_tool, **a),
```

**(b)** In `_MCP_TOOLS` (around `server.py:611`), add a new entry after the `execute_plan` tool:

```python
    mcp_types.Tool(
        name="code.exec",
        description="Run a Python script in a sandbox with RPC access to HarnessClaw tools. Only stdout/stderr/exit_code return — intermediate tool calls stay out of context.",
        inputSchema={
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "Python source. Use `import harnessclaw_tools as h; h._call(name, args)` to invoke tools."},
                "timeout_seconds": {"type": "number", "description": "Default 120."},
                "max_tool_calls": {"type": "integer", "description": "Default 100."},
            },
            "required": ["script"],
        },
    ),
```

**(c)** In `_dispatch_tool`'s `handlers` dict (around `server.py:700`), add:

```python
        "code.exec": lambda a: gateway_mcp.execute_code(token=token, dispatch=_dispatch_tool, **a),
```

**(d)** In `/api/mcp/tools` (around `server.py:587`), add:

```python
        {"name": "code.exec", "description": "Run Python in a sandbox with RPC access to HarnessClaw tools; only stdout returns to the LLM"},
```

- [ ] **Step 2: Smoke-check the wiring via type check + grep**

Run:
```bash
python -c "from harness_claw import server; assert 'code.exec' in [t.name for t in server._MCP_TOOLS]; print('ok')"
```
Expected output: `ok`. If you get an error, the `_MCP_TOOLS` edit was wrong.

Then run:
```bash
pytest tests/gateway/test_execute_code.py -v
```
Expected: the 2 tests from Task 4 still pass (wiring changes are additive — no regressions).

- [ ] **Step 3: Commit**

```bash
git add harness_claw/server.py
git commit -m "feat(server): wire code.exec tool into MCP HTTP and SSE surfaces"
```

---

## Task 6: Grant `code:exec` scope to the orchestrator role

**Files:**
- Modify: `agents.yaml`

- [ ] **Step 1: Edit `agents.yaml`**

Find the orchestrator role's scopes line (around `agents.yaml:90`):

```yaml
    scopes: [agent:list, agent:delegate, agent:spawn, memory:read, memory:write]
```

Replace with:

```yaml
    scopes: [agent:list, agent:delegate, agent:spawn, memory:read, memory:write, code:exec]
```

- [ ] **Step 2: Verify the server still starts**

Run: `python -c "from harness_claw.role_registry import RoleRegistry; r = RoleRegistry('agents.yaml'); print(r.get('orchestrator').scopes)"`
Expected output: a list containing `'code:exec'`.

- [ ] **Step 3: Commit**

```bash
git add agents.yaml
git commit -m "feat(roles): grant code:exec scope to orchestrator"
```

---

## Task 7: End-to-end integration test

**Files:**
- Test: `tests/gateway/test_execute_code.py`

Prove the full loop works against a real `SqliteMemoryStore`: script imports `harnessclaw_tools`, calls `memory.set`, calls `memory.list`, prints results. Verifies that the dispatched RPC calls actually mutate state through the real handler chain.

- [ ] **Step 1: Write the failing test**

Append to `tests/gateway/test_execute_code.py`:

```python
@pytest.mark.asyncio
async def test_execute_code_end_to_end_with_real_memory_store(tmp_path):
    gw, ts = make_gw(tmp_path)
    token = ts.issue("subject", scopes=["code:exec", "memory:read", "memory:write"])

    async def dispatch(t, name, args):
        # Route the two tool names we use in the script through GatewayMCP
        if name == "memory.set":
            return await gw.memory_set(token=t, **args)
        if name == "memory.list":
            return await gw.memory_list(token=t, **args)
        raise ValueError(f"unsupported tool in this test: {name}")

    script = (
        "import harnessclaw_tools as h\n"
        "for i in range(3):\n"
        "    h._call('memory.set', {\n"
        "        'namespace': 'e2e', 'key': f'k{i}', 'value': f'v{i}',\n"
        "        'summary': '', 'tags': []\n"
        "    })\n"
        "entries = h._call('memory.list', {'namespace': 'e2e'})\n"
        "print(len(entries))\n"
    )
    out = await gw.execute_code(token=token, script=script, dispatch=dispatch)
    assert out["exit_code"] == 0, out["stderr"]
    assert out["stdout"].strip() == "3"
    assert out["tool_calls"] == 4
```

- [ ] **Step 2: Run it to verify it passes**

Run: `pytest tests/gateway/test_execute_code.py::test_execute_code_end_to_end_with_real_memory_store -v`
Expected: PASS. If the test fails on a signature mismatch with `memory_set` or `memory_list`, adjust the keyword args in the `dispatch` helper to match the method signatures in `mcp_server.py` — do NOT change the test assertions.

- [ ] **Step 3: Commit**

```bash
git add tests/gateway/test_execute_code.py
git commit -m "test(gateway): e2e code.exec round-trip against real memory store"
```

---

## Out of scope for this plan

- Docker / SSH / Modal transports (defer until unified terminal tool is scoped)
- Typed per-tool wrappers in the stub (`h.memory_list(...)` vs `h._call('memory.list', ...)`)
- Streaming stdout back to the dashboard while the script runs
- Sandboxing beyond subprocess isolation (no seccomp, no filesystem jailing)
- `execute_plan` recursion through code.exec — blocked only at self-recursion; indirect paths still reachable
