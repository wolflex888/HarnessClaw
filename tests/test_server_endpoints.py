import pytest
from fastapi.testclient import TestClient
from harness_claw.server import app, broker
from harness_claw.gateway.task_store import Task


@pytest.fixture
def client():
    return TestClient(app)


def make_paused_task() -> Task:
    task = Task(
        task_id="paused-1",
        delegated_by="orch",
        delegated_to="s1",
        instructions="do it",
        caps_requested=["python"],
        status="paused",
        pause_reason="Cost $0.51 exceeded limit $0.50",
        resume=True,
    )
    return task


def test_resume_paused_task(client):
    task = make_paused_task()
    broker._store.save(task)

    resp = client.post("/api/tasks/paused-1/resume")
    assert resp.status_code == 200
    data = resp.json()
    assert "task_id" in data

    reloaded = broker.get_task("paused-1")
    assert reloaded.status == "queued"
    assert reloaded.pause_reason is None


def test_resume_non_paused_task_returns_400(client):
    task = Task(
        task_id="running-1",
        delegated_by="orch",
        delegated_to="s1",
        instructions="do it",
        caps_requested=["python"],
        status="running",
    )
    broker._store.save(task)
    resp = client.post("/api/tasks/running-1/resume")
    assert resp.status_code == 400


def test_resume_missing_task_returns_404(client):
    resp = client.post("/api/tasks/nonexistent/resume")
    assert resp.status_code == 404


def test_execute_plan_missing_file_returns_error(client):
    # Calls the REST tool dispatch endpoint with a nonexistent plan path.
    # We test via the /mcp/tools/call HTTP path used by non-SSE clients.
    # Since execute_plan requires a running event loop and broker, we test
    # that a missing file raises a descriptive error, not a 500.
    resp = client.post(
        "/mcp/tools/call",
        json={"name": "execute_plan", "arguments": {"plan_path": "/nonexistent/plan.md"}},
        headers={"Authorization": f"Bearer {_get_bootstrap_token()}"},
    )
    assert resp.status_code in (200, 400, 422)
    body = resp.text
    assert "not found" in body.lower() or "error" in body.lower() or "FileNotFoundError" in body.lower()


def _get_bootstrap_token():
    from harness_claw.server import token_store
    return token_store._tokens.get("bootstrap", {}).get("token", "changeme")
