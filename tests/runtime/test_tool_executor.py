from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from harness_claw.gateway.tool_store import SqliteToolStore, ToolDef
from harness_claw.gateway.tool_registry import ToolRegistry
from harness_claw.runtime.tool_executor import ToolExecutor


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_tool(
    id: str = "echo_tool",
    adapter: str = "cli",
    adapter_config: dict | None = None,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
) -> ToolDef:
    now = _now()
    return ToolDef(
        id=id, name=id, description="desc",
        input_schema=input_schema if input_schema is not None else {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        },
        output_schema=output_schema if output_schema is not None else {
            "type": "object",
            "properties": {
                "stdout": {"type": "string"},
                "stderr": {"type": "string"},
                "exit_code": {"type": "integer"},
            },
            "required": ["stdout", "stderr", "exit_code"],
        },
        adapter=adapter,
        adapter_config=adapter_config or {"command": "echo {{msg}}", "timeout": 5},
        cap_allowlist=["python"], allowed_roles=[], required_scopes=[],
        metadata={}, created_at=now, updated_at=now,
    )


def make_executor(tmp_path, tools: list[ToolDef] | None = None) -> ToolExecutor:
    store = SqliteToolStore(tmp_path / "tools.db")
    registry = ToolRegistry(store)
    for t in (tools or []):
        registry.save(t)
    return ToolExecutor(registry)


@pytest.mark.asyncio
async def test_cli_adapter_runs_command(tmp_path):
    executor = make_executor(tmp_path, [make_tool()])
    result = await executor.execute("echo_tool", {"msg": "hello"})
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]


@pytest.mark.asyncio
async def test_cli_substitutes_template_args(tmp_path):
    tool = make_tool(adapter_config={"command": "echo {{word}}", "timeout": 5},
                     input_schema={"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"]})
    executor = make_executor(tmp_path, [tool])
    result = await executor.execute("echo_tool", {"word": "foobar"})
    assert "foobar" in result["stdout"]


@pytest.mark.asyncio
async def test_raises_key_error_for_unknown_tool(tmp_path):
    executor = make_executor(tmp_path)
    with pytest.raises(KeyError, match="nonexistent"):
        await executor.execute("nonexistent", {})


@pytest.mark.asyncio
async def test_raises_validation_error_on_bad_input(tmp_path):
    from jsonschema import ValidationError
    executor = make_executor(tmp_path, [make_tool()])
    with pytest.raises(ValidationError):
        # msg is required but not provided
        await executor.execute("echo_tool", {})


@pytest.mark.asyncio
async def test_raises_value_error_for_unknown_adapter(tmp_path):
    tool = make_tool(
        adapter="unknown",
        adapter_config={},
        input_schema={},
        output_schema={},
    )
    executor = make_executor(tmp_path, [tool])
    with pytest.raises(ValueError, match="Unknown adapter"):
        await executor.execute("echo_tool", {})


@pytest.mark.asyncio
async def test_raises_validation_error_on_bad_output(tmp_path):
    from jsonschema import ValidationError
    # Tool with strict output schema requiring "result" field
    tool = make_tool(
        adapter="cli",
        adapter_config={"command": "echo hello", "timeout": 5},
        input_schema={"type": "object"},
        output_schema={
            "type": "object",
            "properties": {"result": {"type": "string"}},
            "required": ["result"],
        },
    )
    executor = make_executor(tmp_path, [tool])
    # CLI returns {stdout, stderr, exit_code} which doesn't match output_schema
    with pytest.raises(ValidationError):
        await executor.execute("echo_tool", {})


@pytest.mark.asyncio
async def test_cli_timeout_raises_timeout_error(tmp_path):
    tool = make_tool(
        adapter_config={"command": "sleep 10", "timeout": 0.1},
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    executor = make_executor(tmp_path, [tool])
    with pytest.raises(TimeoutError):
        await executor.execute("echo_tool", {})


@pytest.mark.asyncio
async def test_http_adapter_posts_to_url(tmp_path):
    import httpx
    tool = make_tool(
        adapter="http",
        adapter_config={"url": "http://fake-server/api", "method": "POST"},
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )
    executor = make_executor(tmp_path, [tool])

    mock_response = MagicMock()
    mock_response.json.return_value = {"result": "ok"}
    mock_response.raise_for_status = MagicMock()

    with patch("harness_claw.runtime.tool_executor.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await executor.execute("echo_tool", {"key": "value"})
        assert result == {"result": "ok"}
        mock_client.request.assert_called_once_with(
            "POST", "http://fake-server/api", json={"key": "value"}, headers={}
        )


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
    assert b"[tool: echo_tool] \xe2\x9c\x93 exit 0" in combined


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
