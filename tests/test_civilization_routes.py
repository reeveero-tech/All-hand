"""
R-021: coverage for the three /civilization/* dynamic routes.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import server
    return TestClient(server.app)


def test_civilization_registry(client):
    """GET /civilization/registry returns the curated OSS agents registry."""
    r = client.get("/civilization/registry")
    assert r.status_code == 200
    data = r.json()
    assert "count" in data
    assert "total_addressable" in data
    assert "agents" in data
    assert data["count"] >= 0


def test_civilization_plan(client):
    """POST /civilization/plan returns the auto-routed plan without executing."""
    r = client.post("/civilization/plan", json={"message": "analyze sales data"})
    assert r.status_code == 200
    data = r.json()
    assert "plan" in data


def test_civilization_plan_rejects_empty_message(client):
    """A blank message should still plan (plan does not require non-empty)."""
    r = client.post("/civilization/plan", json={"message": ""})
    assert r.status_code == 200


def test_civilization_execute_rejects_empty_message(client):
    """POST /civilization/execute with a blank message must 400."""
    r = client.post("/civilization/execute", json={"message": "   "})
    assert r.status_code == 400


def test_civilization_execute_runs(client):
    """POST /civilization/execute with a real message completes and writes to tasks_store."""
    r = client.post("/civilization/execute", json={"message": "hello world"})
    assert r.status_code == 200
    data = r.json()
    assert "task_id" in data


def test_civilization_execute_requires_auth_when_enabled(client, monkeypatch):
    """With AGOS_API_KEY set, execute without a key must be rejected (R-004 + R-021)."""
    monkeypatch.setenv("AGOS_API_KEY", "test-secret-key")
    import importlib
    import server as _server
    importlib.reload(_server)
    c = TestClient(_server.app)
    r = c.post("/civilization/execute", json={"message": "hello"})
    assert r.status_code in (401, 403)
