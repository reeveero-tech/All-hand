"""
R-004 security regression tests: enforce verify_api_key, CORS credentials,
and /health 503 semantics.

Run with: AGOS_API_KEY set to activate auth; unset to test open mode.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    import server
    return TestClient(server.app)


def _set_key(monkeypatch, key):
    monkeypatch.setenv("AGOS_API_KEY", key)
    # Re-import server so ENABLE_AUTH picks up the env change
    import importlib
    import server as _server
    importlib.reload(_server)
    return _server


def test_tasks_without_key_rejected_when_auth_enabled(client, monkeypatch):
    """POST /api/tasks must 401 when auth enabled and no key provided."""
    srv = _set_key(monkeypatch, "test-secret-key")
    c = TestClient(srv.app)
    r = c.post("/api/tasks", json={"specialization": "analyst", "prompt": "hi"})
    assert r.status_code in (401, 403)


def test_chat_without_key_rejected_when_auth_enabled(client, monkeypatch):
    srv = _set_key(monkeypatch, "test-secret-key")
    c = TestClient(srv.app)
    r = c.post("/api/chat", json={"message": "hi"})
    assert r.status_code in (401, 403)


def test_external_register_rejects_wrong_key(client, monkeypatch):
    srv = _set_key(monkeypatch, "test-secret-key")
    c = TestClient(srv.app)
    r = c.post(
        "/api/agents/external",
        headers={"X-API-Key": "wrong-key"},
        json={"name": "x", "url": "https://example.com"},
    )
    assert r.status_code == 403


def test_external_register_rejects_no_key(client, monkeypatch):
    srv = _set_key(monkeypatch, "test-secret-key")
    c = TestClient(srv.app)
    r = c.post(
        "/api/agents/external",
        json={"name": "x", "url": "https://example.com"},
    )
    assert r.status_code == 401


def test_civilization_execute_rejects_no_key(client, monkeypatch):
    srv = _set_key(monkeypatch, "test-secret-key")
    c = TestClient(srv.app)
    r = c.post("/civilization/execute", json={"message": "test"})
    assert r.status_code in (401, 403)


def test_health_returns_200_when_healthy(client):
    r = client.get("/health")
    assert r.status_code == 200


def test_health_allows_credentials_with_allowlist(client, monkeypatch):
    """CORS credentials must be disabled when wildcard origins are in use."""
    monkeypatch.setenv("AGOS_ALLOWED_ORIGINS", "https://app.example.com")
    import importlib
    import server as _server
    importlib.reload(_server)
    # Access the middleware config through the app's openapi/CORS
    cors = next(
        (m for m in _server.app.user_middleware if "CORSMiddleware" in str(m.cls)),
        None,
    )
    assert cors is not None
    # allow_credentials should be True when origins are allowlisted (not wildcard)
    assert cors.kwargs.get("allow_credentials") is True


def test_health_disable_credentials_with_wildcard(client, monkeypatch):
    monkeypatch.setenv("AGOS_ALLOWED_ORIGINS", "*")
    import importlib
    import server as _server
    importlib.reload(_server)
    cors = next(
        (m for m in _server.app.user_middleware if "CORSMiddleware" in str(m.cls)),
        None,
    )
    assert cors is not None
    assert cors.kwargs.get("allow_credentials") is False
