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
