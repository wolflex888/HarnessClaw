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
