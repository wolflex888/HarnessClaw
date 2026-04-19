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

        tool_call_count = 0
        BLOCKED_TOOLS = {"code.exec"}

        async def handle_client(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            nonlocal tool_call_count
            try:
                while True:
                    line = await reader.readline()
                    if not line:
                        return
                    try:
                        msg = json.loads(line.decode())
                        name = msg["tool"]
                        args = msg.get("args")
                        if args is None:
                            args = {}
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

                    if tool_call_count >= max_tool_calls:
                        writer.write(
                            (json.dumps({"error": f"max_tool_calls ({max_tool_calls}) exceeded"}) + "\n").encode()
                        )
                        await writer.drain()
                        continue

                    tool_call_count += 1
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
            tool_calls=tool_call_count,
            timed_out=timed_out,
        )
