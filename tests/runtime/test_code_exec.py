from __future__ import annotations

import ast
import asyncio
import importlib
import json
import os
import tempfile
import threading
import time
from pathlib import Path

import pytest

from harness_claw.runtime import rpc_stub


def test_call_preserves_explicit_empty_args_dict():
    """Passing args={} must be preserved as {}, not defaulted via `or {}`."""
    captured: dict = {}

    async def server_main(sock_path: str) -> None:
        async def handle(reader, writer):
            line = await reader.readline()
            captured["req"] = json.loads(line.decode())
            writer.write(b'{"result": null}\n')
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(handle, path=sock_path)
        async with server:
            await asyncio.sleep(1)  # serve briefly

    with tempfile.TemporaryDirectory() as tmp:
        sock_path = str(Path(tmp) / "rpc.sock")
        loop = asyncio.new_event_loop()
        server_thread = threading.Thread(
            target=lambda: loop.run_until_complete(server_main(sock_path)),
            daemon=True,
        )
        server_thread.start()

        # Wait for socket to exist
        for _ in range(50):
            if Path(sock_path).exists():
                break
            time.sleep(0.02)

        os.environ["HARNESSCLAW_RPC_SOCKET"] = sock_path
        import importlib as _importlib
        from harness_claw.runtime import rpc_stub as _rpc_stub
        _importlib.reload(_rpc_stub)

        _rpc_stub._call("t", {})  # explicit empty dict
        server_thread.join(timeout=2)

    assert captured["req"] == {"tool": "t", "args": {}}


# ---------------------------------------------------------------------------
# Task 2 — run_rpc_script tests
# ---------------------------------------------------------------------------
from harness_claw.runtime.code_exec import CodeExecResult, run_rpc_script


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


async def test_run_rpc_script_captures_stderr_and_nonzero_exit():
    async def dispatch(token: str, name: str, args: dict):
        return None

    script = "import sys; sys.stderr.write('oh no'); sys.exit(3)\n"
    result = await run_rpc_script(script, token="t", dispatch=dispatch)
    assert result.exit_code == 3
    assert "oh no" in result.stderr
    assert result.tool_calls == 0


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
