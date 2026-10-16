"""
Tests for API Endpoints

Tests the FastAPI server endpoints.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tenant_auth):
    """Create test client (SEC-02: authenticated as the test tenant)."""
    import os
    
    # Mock the environment
    os.environ["AGOS_ALLOWED_ORIGINS"] = "*"
    
    # Import after mocking
    from server import app
    return TestClient(app, headers=tenant_auth.headers)


def test_api_root(client):
    """Test API root endpoint."""
    response = client.get("/api")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["name"] == "AGOS Agent Civilization"
    assert "version" in data
    assert "status" in data
    assert "specializations" in data


def test_api_health(client):
    """Test health endpoint."""
    response = client.get("/health")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "status" in data


def test_api_liveness(client):
    """Test liveness probe."""
    response = client.get("/health/live")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["status"] == "alive"
    assert "timestamp" in data


def test_api_readiness(client):
    """Test readiness probe."""
    response = client.get("/health/ready")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["status"] in ["ready", "not_ready"]


def test_api_metrics(client):
    """Test metrics endpoint."""
    response = client.get("/metrics")
    
    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_api_system_status(client):
    """Test system status endpoint."""
    response = client.get("/api/system/status")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "status" in data
    assert "uptime_s" in data
    assert "specializations" in data
    assert "total_agents" in data


def test_api_list_specializations(client):
    """Test listing specializations."""
    response = client.get("/api/specializations")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "specializations" in data
    specializations = data["specializations"]
    
    # Check that all 20 specializations are listed
    assert len(specializations) == 20
    
    # Check structure of specialization
    spec = specializations[0]
    assert "name" in spec
    assert "description" in spec
    assert "agents" in spec


def test_api_list_agents(client):
    """Test listing agents."""
    response = client.get("/api/agents")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "count" in data
    assert "agents" in data
    
    # Should have agents
    assert data["count"] > 0


def test_api_list_agents_filter(client):
    """Test filtering agents by specialization."""
    response = client.get("/api/agents?specialization=analyst")
    
    assert response.status_code == 200
    data = response.json()
    
    for agent in data["agents"]:
        assert agent["specialization"] == "analyst"


def test_api_create_task(client):
    """Test creating a task."""
    response = client.post(
        "/api/tasks",
        json={
            "specialization": "analyst",
            "prompt": "Test task",
        },
    )
    
    assert response.status_code == 202
    data = response.json()
    
    assert "task_id" in data
    assert data["status"] == "queued"


def test_api_create_task_invalid_specialization(client):
    """Test creating task with invalid specialization."""
    response = client.post(
        "/api/tasks",
        json={
            "specialization": "invalid_specialization",
            "prompt": "Test task",
        },
    )
    
    assert response.status_code == 400


def test_api_create_task_empty_prompt(client):
    """Test creating task with empty prompt."""
    response = client.post(
        "/api/tasks",
        json={
            "specialization": "analyst",
            "prompt": "",
        },
    )
    
    assert response.status_code == 422  # Validation error


def test_api_get_task(client):
    """Test getting a specific task."""
    # Create a task first
    create_response = client.post(
        "/api/tasks",
        json={
            "specialization": "analyst",
            "prompt": "Get test",
        },
    )
    task_id = create_response.json()["task_id"]
    
    # Get the task
    response = client.get(f"/api/tasks/{task_id}")
    
    assert response.status_code == 200
    data = response.json()
    
    assert data["id"] == task_id


def test_api_get_task_not_found(client):
    """Test getting non-existent task."""
    response = client.get("/api/tasks/nonexistent_id")
    
    assert response.status_code == 404


def test_api_list_tasks(client):
    """Test listing tasks."""
    response = client.get("/api/tasks")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "tasks" in data
    assert "count" in data


def test_api_chat(client):
    """Test chat endpoint."""
    response = client.post(
        "/api/chat",
        json={
            "message": "Test chat message",
        },
    )
    
    assert response.status_code == 200
    data = response.json()
    
    assert "task_id" in data
    assert "routed_to" in data
    assert "agent" in data


def test_api_events(client):
    """Test events endpoint."""
    response = client.get("/api/events")
    
    assert response.status_code == 200
    data = response.json()
    
    assert "events" in data


def test_api_cors_headers(client):
    """Test CORS headers."""
    response = client.options(
        "/api",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    
    # Should have CORS headers
    assert "access-control-allow-origin" in response.headers or response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
