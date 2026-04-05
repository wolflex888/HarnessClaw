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
