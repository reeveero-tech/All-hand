"""R-101: the web/ UI works when the server runs in authenticated mode.

Before R-101 web/src/api.js never sent X-API-Key, so setting AGOS_API_KEY — the
recommended way to enable test_runner since R-100 (D-044) — broke every write
from the UI.  These tests pin the server half of the contract the UI now relies
on, using the exact payloads web/src/api.js sends:

* the UI can detect the mode (``security.authentication``) and keep reading
  without a key (status, specializations, agents, tasks, events, SSE stream);
* writes answer 401 without a key and 403 with a wrong key (the UI maps these
  to its two messages) and succeed with the key in ``X-API-Key``;
* CORS lets a browser send the ``X-API-Key`` header;
* with the key, test_runner really executes (R-100 policy: authenticated mode).

The client half (header sent, messages, storage) is web/tests/api.test.mjs.
"""
import importlib
import inspect
import time

import pytest
from fastapi.testclient import TestClient

KEY = "r101-test-key"


@pytest.fixture
def auth_server(monkeypatch):
    monkeypatch.setenv("AGOS_API_KEY", KEY)
    import server
    srv = importlib.reload(server)
    yield srv
    monkeypatch.delenv("AGOS_API_KEY", raising=False)
    importlib.reload(server)


@pytest.fixture
def client(auth_server):
    # Context manager = a running event loop between requests, as under uvicorn;
    # without it, tasks that await a worker thread (test_runner) never resume.
    with TestClient(auth_server.app, raise_server_exceptions=False) as c:
        yield c


def _poll(client, task_id, headers=None, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/tasks/{task_id}", headers=headers or {})
        assert resp.status_code in (200, 429), resp.text
        t = resp.json()
        if resp.status_code == 200 and t["status"] in ("completed", "failed"):
            return t
        time.sleep(0.3)
    raise AssertionError("task did not finish")


def test_ui_can_detect_authenticated_mode_without_a_key(client):
    r = client.get("/api/system/status")
    assert r.status_code == 200
    assert r.json()["security"]["authentication"] == "enabled"


@pytest.mark.parametrize("path", ["/api/specializations", "/api/agents", "/api/tasks?limit=50",
                                  "/api/events?limit=50"])
def test_read_routes_the_ui_polls_do_not_require_the_key(client, path):
    assert client.get(path).status_code == 200


def test_sse_stream_needs_no_key_because_eventsource_cannot_send_headers(auth_server):
    route = next(r for r in auth_server.app.routes if getattr(r, "path", "") == "/api/events/stream")
    assert "api_key" not in inspect.signature(route.endpoint).parameters


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/tasks", {"specialization": "analyst", "prompt": "1 2 3", "data": None}),
    ("post", "/api/chat", {"message": "hello"}),
    ("post", "/api/agents/external", {"name": "r101-x", "url": "http://agent.example.org:9/x",
                                      "model": "", "api_key": ""}),
    ("delete", "/api/agents/external/r101-x", None),
])
def test_writes_401_without_key_and_403_with_wrong_key(client, method, path, body):
    kw = {"json": body} if body is not None else {}
    r = getattr(client, method)(path, **kw)
    assert r.status_code == 401 and r.json()["detail"] == "API key required"
    r = getattr(client, method)(path, headers={"X-API-Key": "wrong"}, **kw)
    assert r.status_code == 403 and r.json()["detail"] == "Invalid API key"


def test_ui_flows_succeed_with_the_key(client):
    h = {"X-API-Key": KEY}
    r = client.post("/api/tasks", headers=h,
                    json={"specialization": "analyst", "prompt": "4 8 15 16 23 42", "data": None})
    assert r.status_code == 202
    assert _poll(client, r.json()["task_id"])["status"] == "completed"
    r = client.post("/api/chat", headers=h, json={"message": "حلل 1 2 3"})
    assert r.status_code == 200 and "agent" in r.json()
    r = client.post("/api/agents/external", headers=h,
                    json={"name": "r101-ok", "url": "http://agent.example.org:9/x", "model": "", "api_key": ""})
    assert r.status_code == 201, r.text
    assert client.delete("/api/agents/external/r101-ok", headers=h).status_code == 200


def test_cors_preflight_allows_the_x_api_key_header(client):
    r = client.options("/api/tasks", headers={
        "Origin": "https://ui.example.org",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-api-key",
    })
    assert r.status_code == 200
    assert "x-api-key" in r.headers.get("access-control-allow-headers", "").lower()


def test_test_runner_executes_in_authenticated_mode_from_the_ui(client):
    # R-100: with AGOS_API_KEY set, code execution is allowed (sandboxed).
    h = {"X-API-Key": KEY}
    r = client.post("/api/tasks", headers=h,
                    json={"specialization": "test_runner", "prompt": "print(6 * 7)", "data": None})
    assert r.status_code == 202
    t = _poll(client, r.json()["task_id"])
    out = t["result"]["result"]
    assert t["status"] == "completed", t
    assert out["tool_output"]["sandbox"] is True and out["tool_output"]["stdout"].strip() == "42"
