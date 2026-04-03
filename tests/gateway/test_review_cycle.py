# tests/gateway/test_review_cycle.py
"""Integration test: simulates the full orchestrator-driven review cycle."""
from __future__ import annotations

from unittest.mock import AsyncMock

from harness_claw.gateway.broker import Broker
from harness_claw.gateway.capability import AgentAdvertisement, LocalConnector
from harness_claw.gateway.event_bus import Event, LocalEventBus


def make_agent(session_id: str, caps: list[str], role_id: str = "coder") -> AgentAdvertisement:
    return AgentAdvertisement(
        session_id=session_id, role_id=role_id,
        caps=caps, status="idle", task_count=0, connector="local",
    )


async def test_full_review_cycle_approve_on_first_pass():
    """Orchestrator delegates to writer, then reviewer. Reviewer approves."""
    conn = LocalConnector()
    await conn.register(make_agent("writer-1", ["python"], role_id="code-writer"))
    await conn.register(make_agent("reviewer-1", ["code-review"], role_id="code-reviewer"))

    dispatcher = AsyncMock()
    bus = LocalEventBus()
    broker = Broker(connectors=[conn], dispatcher=dispatcher, event_bus=bus)

    orch_callbacks: list[Event] = []

    async def orch_handler(event: Event) -> None:
        orch_callbacks.append(event)

    broker.register_callback_handler("orch-1", orch_handler)

    # Step 1: Orchestrator delegates to code-writer
    write_task_id = await broker.delegate(
        delegated_by="orch-1",
        caps=["python"],
        instructions="Build feature X",
        callback=True,
    )
    assert dispatcher.dispatch.call_count == 1

    # Step 2: Code-writer completes
    await broker.complete_task(write_task_id, result="done, changed files: [api.py]")
    assert len(orch_callbacks) == 1
    assert orch_callbacks[0].payload["task"]["status"] == "completed"

    # Step 3: Orchestrator delegates to reviewer
    review_task_id = await broker.delegate(
        delegated_by="orch-1",
        caps=["code-review"],
        instructions="Review git diff for api.py",
        context={"files": ["api.py"], "priorities": ["bugs", "security"]},
        callback=True,
    )

    # Step 4: Reviewer approves
    verdict = {
        "verdict": "APPROVE",
        "summary": "Clean implementation, no issues found",
        "findings": [],
        "priority_focus": "bugs, security",
    }
    await broker.complete_task(review_task_id, result=verdict)
    assert len(orch_callbacks) == 2
    assert orch_callbacks[1].payload["task"]["result"]["verdict"] == "APPROVE"


async def test_full_review_cycle_revise_then_approve():
    """Reviewer requests revision, code-writer fixes, reviewer approves on re-review."""
    conn = LocalConnector()
    await conn.register(make_agent("writer-1", ["python"], role_id="code-writer"))
    await conn.register(make_agent("reviewer-1", ["code-review"], role_id="code-reviewer"))

    dispatcher = AsyncMock()
    bus = LocalEventBus()
    broker = Broker(connectors=[conn], dispatcher=dispatcher, event_bus=bus)

    orch_callbacks: list[Event] = []

    async def orch_handler(event: Event) -> None:
        orch_callbacks.append(event)

    broker.register_callback_handler("orch-1", orch_handler)

    # Step 1: Write code
    write_task_id = await broker.delegate(
        delegated_by="orch-1", caps=["python"],
        instructions="Build feature X", callback=True,
    )
    await broker.complete_task(write_task_id, result="done")
    assert len(orch_callbacks) == 1

    # Step 2: First review — REVISE
    review1_id = await broker.delegate(
        delegated_by="orch-1", caps=["code-review"],
        instructions="Review", context={"files": ["api.py"]}, callback=True,
    )
    verdict1 = {
        "verdict": "REVISE",
        "summary": "1 bug found",
        "findings": [
            {
                "severity": "error",
                "category": "bug",
                "file": "api.py",
                "line": 42,
                "message": "Missing null check",
                "suggestion": "Add `if x is None: return`",
            }
        ],
        "priority_focus": "bugs",
    }
    await broker.complete_task(review1_id, result=verdict1)
    assert len(orch_callbacks) == 2
    assert orch_callbacks[1].payload["task"]["result"]["verdict"] == "REVISE"

    # Step 3: Code-writer fixes
    fix_task_id = await broker.delegate(
        delegated_by="orch-1", caps=["python"],
        instructions="Fix: add null check at api.py:42",
        context={"findings": verdict1["findings"]}, callback=True,
    )
    await broker.complete_task(fix_task_id, result="fixed")
    assert len(orch_callbacks) == 3

    # Step 4: Second review (diff-only) — APPROVE
    review2_id = await broker.delegate(
        delegated_by="orch-1", caps=["code-review"],
        instructions="Re-review diff only",
        context={"scope": "diff-only", "previous_findings": verdict1["findings"]},
        callback=True,
    )
    verdict2 = {
        "verdict": "APPROVE",
        "summary": "Fix looks good",
        "findings": [],
        "priority_focus": "bugs",
    }
    await broker.complete_task(review2_id, result=verdict2)
    assert len(orch_callbacks) == 4
    assert orch_callbacks[3].payload["task"]["result"]["verdict"] == "APPROVE"


async def test_review_cycle_escalates_after_two_rounds():
    """After 2 REVISE rounds, orchestrator should escalate (simulated by checking callbacks)."""
    conn = LocalConnector()
    await conn.register(make_agent("writer-1", ["python"], role_id="code-writer"))
    await conn.register(make_agent("reviewer-1", ["code-review"], role_id="code-reviewer"))

    dispatcher = AsyncMock()
    bus = LocalEventBus()
    broker = Broker(connectors=[conn], dispatcher=dispatcher, event_bus=bus)

    orch_callbacks: list[Event] = []

    async def orch_handler(event: Event) -> None:
        orch_callbacks.append(event)

    broker.register_callback_handler("orch-1", orch_handler)

    revise_verdict = {
        "verdict": "REVISE",
        "summary": "Still has issues",
        "findings": [{"severity": "error", "category": "bug", "file": "x.py",
                       "line": 1, "message": "broken", "suggestion": "fix it"}],
        "priority_focus": "bugs",
    }

    # Round 1: write → review (REVISE) → fix → review (REVISE)
    t1 = await broker.delegate("orch-1", ["python"], "write", callback=True)
    await broker.complete_task(t1, result="done")

    r1 = await broker.delegate("orch-1", ["code-review"], "review", callback=True)
    await broker.complete_task(r1, result=revise_verdict)

    t2 = await broker.delegate("orch-1", ["python"], "fix round 1", callback=True)
    await broker.complete_task(t2, result="fixed")

    # Round 2: re-review still REVISE
    r2 = await broker.delegate("orch-1", ["code-review"], "re-review", callback=True)
    await broker.complete_task(r2, result=revise_verdict)

    # Orchestrator now has 4 callbacks — the last one is still REVISE
    assert len(orch_callbacks) == 4
    assert orch_callbacks[3].payload["task"]["result"]["verdict"] == "REVISE"
    # No third round delegated — escalation logic lives in the orchestrator prompt
