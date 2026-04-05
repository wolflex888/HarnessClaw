from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
from jsonschema import validate

from harness_claw.gateway.tool_registry import ToolRegistry
from harness_claw.gateway.tool_store import ToolDef


class ToolExecutor:
    """Routes registered tool calls to the appropriate execution adapter.

    Validates input against tool.input_schema before dispatch and output
    against tool.output_schema before returning. Raises jsonschema.ValidationError
    on schema violations and KeyError for unknown tool IDs.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    async def execute(self, tool_id: str, arguments: dict) -> dict:
        tool = self._registry.get(tool_id)
        if tool is None:
            raise KeyError(f"Tool {tool_id!r} not registered")
        validate(instance=arguments, schema=tool.input_schema)
        result = await self._dispatch(tool, arguments)
        validate(instance=result, schema=tool.output_schema)
        return result

    async def _dispatch(self, tool: ToolDef, args: dict) -> dict:
        if tool.adapter == "cli":
            return await self._run_cli(tool, args)
        if tool.adapter == "http":
            return await self._run_http(tool, args)
        if tool.adapter == "mcp":
            return await self._run_mcp(tool, args)
        raise ValueError(f"Unknown adapter: {tool.adapter!r}")

    async def _run_cli(self, tool: ToolDef, args: dict) -> dict:
        config = tool.adapter_config
        command = config["command"]
        for key, value in args.items():
            command = command.replace(f"{{{{{key}}}}}", str(value))
        if re.search(r'\{\{[^}]+\}\}', command):
            remaining = re.findall(r'\{\{([^}]+)\}\}', command)
            raise ValueError(f"Unresolved template placeholders in CLI command: {remaining}")
        timeout = float(config.get("timeout", 30))
        working_dir = config.get("working_dir")
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"Tool {tool.id!r} timed out after {timeout}s")
        return {
            "stdout": stdout.decode(),
            "stderr": stderr.decode(),
            "exit_code": proc.returncode,
        }

    async def _run_http(self, tool: ToolDef, args: dict) -> dict:
        config = tool.adapter_config
        url = config["url"]
        method = config.get("method", "POST").upper()
        headers = config.get("headers", {})
        timeout = float(config.get("timeout", 30))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(method, url, json=args, headers=headers)
            response.raise_for_status()
            return response.json()

    async def _run_mcp(self, tool: ToolDef, args: dict) -> dict:
        config = tool.adapter_config
        server_url = config["server_url"]
        tool_name = config["tool_name"]
        timeout = float(config.get("timeout", 30))
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{server_url}/mcp/tools/call",
                json={"name": tool_name, "arguments": args},
            )
            response.raise_for_status()
            return response.json()
