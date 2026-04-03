# tests/gateway/test_workflow_mcp.py
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from harness_claw.gateway.mcp_server import GatewayMCP
from harness_claw.gateway.auth import TokenStore
from harness_claw.gateway.policy import LocalPolicyEngine


def make_mcp(workflow_engine=None) -> tuple[GatewayMCP, TokenStore]:
    token_store = TokenStore()
    broker = MagicMock()
    broker.delegate = AsyncMock(return_value="task-1")
    broker.get_task = MagicMock(return_value=None)
    memory = MagicMock()
    audit = MagicMock()
    audit.log = MagicMock()
    connector = MagicMock()
    connector.query = AsyncMock(return_value=[])

    mcp = GatewayMCP(
        token_store=token_store,
        policy=LocalPolicyEngine(),
        connectors=[connector],
        broker=broker,
        memory=memory,
        audit=audit,
        workflow_engine=workflow_engine,
    )
    return mcp, token_store


@pytest.mark.asyncio
async def test_workflow_run_tool():
    wf_engine = MagicMock()
    wf_engine.start = AsyncMock(return_value="run-123")

    mcp, token_store = make_mcp(workflow_engine=wf_engine)
    token = token_store.issue(subject="orch-1", scopes=["agent:delegate"])

    result = await mcp.workflow_run(token=token, workflow_id="code_review_cycle", input="review my PR")
    assert result == {"run_id": "run-123"}
    wf_engine.start.assert_called_once_with(
        workflow_id="code_review_cycle",
        input="review my PR",
        initiated_by="orch-1",
    )


@pytest.mark.asyncio
async def test_workflow_run_tool_no_engine():
    mcp, token_store = make_mcp(workflow_engine=None)
    token = token_store.issue(subject="orch-1", scopes=["agent:delegate"])

    with pytest.raises(RuntimeError, match="workflow engine"):
        await mcp.workflow_run(token=token, workflow_id="wf1", input="x")
