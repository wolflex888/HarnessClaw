from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from harness_claw.gateway.auth import TokenStore
from harness_claw.gateway.policy import LocalPolicyEngine
from harness_claw.gateway.capability import LocalConnector, AgentAdvertisement
from harness_claw.gateway.broker import Broker, LocalDispatcher
from harness_claw.gateway.memory import SqliteMemoryStore
from harness_claw.gateway.audit import AuditLogger
from harness_claw.gateway.mcp_server import GatewayMCP


@pytest.fixture
def token_store():
    return TokenStore()


@pytest.fixture
def connector():
    return LocalConnector()


@pytest.fixture
def dispatcher():
    d = LocalDispatcher()
    return d


@pytest.fixture
def broker(connector, dispatcher):
    return Broker(connectors=[connector], dispatcher=dispatcher)


@pytest.fixture
def memory(tmp_path):
    return SqliteMemoryStore(tmp_path / "memory.db")


@pytest.fixture
def audit(tmp_path):
    return AuditLogger(tmp_path / "audit.jsonl")


@pytest.fixture
def gateway(token_store, connector, broker, memory, audit):
    return GatewayMCP(
        token_store=token_store,
        policy=LocalPolicyEngine(),
        connectors=[connector],
        broker=broker,
        memory=memory,
        audit=audit,
    )


async def test_agent_list_requires_valid_token(gateway):
    with pytest.raises(Exception, match="invalid|unauthorized"):
        await gateway.agent_list(token="bad-token", caps=[])


async def test_agent_list_requires_agent_list_scope(gateway, token_store):
    token = token_store.issue("s1", ["memory:read"])  # missing agent:list
    with pytest.raises(Exception, match="denied|scope"):
        await gateway.agent_list(token=token, caps=[])


async def test_agent_list_returns_matching_agents(gateway, token_store, connector):
    await connector.register(AgentAdvertisement(
        session_id="s1", role_id="coder", caps=["python"],
        status="idle", task_count=0, connector="local",
    ))
    token = token_store.issue("orch", ["agent:list"])
    results = await gateway.agent_list(token=token, caps=["python"])
    assert any(a["session_id"] == "s1" for a in results)


async def test_memory_set_and_get(gateway, token_store):
    token = token_store.issue("s1", ["memory:read", "memory:write"])
    await gateway.memory_set(token=token, namespace="project:test", key="k1", value="v1", summary=None, tags=[])
    result = await gateway.memory_get(token=token, namespace="project:test", key="k1")
    assert result["value"] == "v1"


async def test_memory_get_requires_read_scope(gateway, token_store):
    token = token_store.issue("s1", ["memory:write"])  # missing memory:read
    with pytest.raises(Exception, match="denied|scope"):
        await gateway.memory_get(token=token, namespace="ns", key="k")


async def test_agent_delegate_with_context_and_callback(gateway, token_store, connector, dispatcher):
    await connector.register(AgentAdvertisement(
        session_id="coder-1", role_id="coder", caps=["python"],
        status="idle", task_count=0, connector="local",
    ))
    dispatcher.register_writer("coder-1", lambda data: None)
    token = token_store.issue("orch", ["agent:delegate"])

    result = await gateway.agent_delegate(
        token=token,
        caps=["python"],
        instructions="write tests",
        context={"files": ["a.py", "b.py"], "priorities": ["bugs"]},
        callback=True,
    )
    assert "task_id" in result

    task = gateway._broker.get_task(result["task_id"])
    assert task.context == {"files": ["a.py", "b.py"], "priorities": ["bugs"]}
    assert task.callback is True


async def test_agent_complete_with_dict_result(gateway, token_store, connector, dispatcher):
    await connector.register(AgentAdvertisement(
        session_id="coder-1", role_id="coder", caps=["python"],
        status="idle", task_count=0, connector="local",
    ))
    dispatcher.register_writer("coder-1", lambda data: None)

    delegate_token = token_store.issue("orch", ["agent:delegate"])
    result = await gateway.agent_delegate(
        token=delegate_token, caps=["python"], instructions="write it"
    )
    task_id = result["task_id"]

    complete_token = token_store.issue("coder-1", ["agent:report"])
    verdict = {"verdict": "APPROVE", "summary": "all good", "findings": []}
    complete_result = await gateway.agent_complete(
        token=complete_token, task_id=task_id, result=verdict
    )
    assert complete_result["status"] == "completed"

    task = gateway._broker.get_task(task_id)
    assert task.result == verdict
    assert task.result["verdict"] == "APPROVE"


async def test_agent_complete_with_string_result_still_works(gateway, token_store, connector, dispatcher):
    await connector.register(AgentAdvertisement(
        session_id="coder-1", role_id="coder", caps=["python"],
        status="idle", task_count=0, connector="local",
    ))
    dispatcher.register_writer("coder-1", lambda data: None)

    delegate_token = token_store.issue("orch", ["agent:delegate"])
    result = await gateway.agent_delegate(
        token=delegate_token, caps=["python"], instructions="write it"
    )
    task_id = result["task_id"]

    complete_token = token_store.issue("coder-1", ["agent:report"])
    complete_result = await gateway.agent_complete(
        token=complete_token, task_id=task_id, result="done!"
    )
    assert complete_result["status"] == "completed"

    task = gateway._broker.get_task(task_id)
    assert task.result == "done!"
