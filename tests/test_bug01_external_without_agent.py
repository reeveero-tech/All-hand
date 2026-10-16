"""BUG-01 (R-140): an ``external`` task is refused before it is created when no
external agent is registered.

Before the fix ``POST /api/tasks`` accepted ``specialization="external"`` with
``EXTERNAL_AGENTS == []``: it created the record (``queued``), wrote it to
storage, logged and published ``task_created`` and scheduled ``_run_task``;
``_pick_agent("external")`` then raised ``HTTPException(404)`` inside the
background task before any status update — the task stayed ``queued`` forever
and the exception was unhandled.

Now the route answers 409 after authentication and before any side effect.
The real API route is used with real test credentials (``tenant_auth``).
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import server
from agent_civilization.agents.external_adapter import ExternalAgentAdapter


# ------------------------------------------------------------------ spies

class _SpyStorage:
    """Delegates to the real storage and records every write call."""

    def __init__(self, real):
        self._real, self.calls = real, []

    def __getattr__(self, name):
        attr = getattr(self._real, name)
        if not callable(attr):
            return attr

        def wrapper(*a, **kw):
            if name.startswith(("insert", "update", "upsert", "delete", "save", "write")):
                self.calls.append(name)  # writes only (close() at app shutdown is not one)
            return attr(*a, **kw)
        return wrapper


@pytest.fixture
def spies(monkeypatch):
    real_storage = server.get_storage()
    storage = _SpyStorage(real_storage)
    published, scheduled, ids = [], [], []
    real_publish, real_run, real_uuid4 = server._publish, server._run_task, server.uuid.uuid4

    async def publish(tenant_id, event):
        published.append((tenant_id, event))
        return await real_publish(tenant_id, event)

    def run_task(*a, **kw):
        scheduled.append(a)
        return real_run(*a, **kw)

    def uuid4():
        ids.append(1)
        return real_uuid4()

    monkeypatch.setattr(server, "get_storage", lambda: storage)
    monkeypatch.setattr(server, "_publish", publish)
    monkeypatch.setattr(server, "_run_task", run_task)
    monkeypatch.setattr(server, "uuid", SimpleNamespace(uuid4=uuid4))
    return SimpleNamespace(storage=storage, published=published, scheduled=scheduled, ids=ids)


@pytest.fixture
def no_external_agents(monkeypatch):
    monkeypatch.setattr(server, "EXTERNAL_AGENTS", [])


@pytest.fixture
def clean_tasks(monkeypatch):
    monkeypatch.setattr(server, "TASKS", {})
    return server.TASKS


def _client():
    return TestClient(server.app, raise_server_exceptions=False)


# ------------------------------------------------------------------ the defect

def test_external_without_agent_is_refused_with_409_and_no_side_effect(
        tenant_auth, no_external_agents, clean_tasks, spies):
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "external", "prompt": "hello"},
                   headers=tenant_auth.headers)
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "no external agent is registered" in detail
        assert tenant_auth.key not in detail and "EXTERNAL_AGENTS=" not in detail
        time.sleep(0.2)  # give a (wrongly) scheduled background task the chance to run
        assert c.get("/api/tasks", headers=tenant_auth.headers).json()["count"] == 0
    assert clean_tasks == {}                       # no record
    assert spies.ids == []                         # no task_id generated
    assert spies.storage.calls == []               # no insert_task / insert_event / update_task
    assert spies.published == []                   # no SSE event
    assert spies.scheduled == []                   # _run_task never scheduled


def test_refusal_happens_after_authentication(no_external_agents, clean_tasks, spies, tenant_auth):
    """SEC-02: an unauthenticated caller cannot probe whether external agents exist."""
    body = {"specialization": "external", "prompt": "probe"}
    with _client() as c:
        assert c.post("/api/tasks", json=body).status_code == 401
        assert c.post("/api/tasks", json=body, headers={"X-API-Key": "wrong-key"}).status_code == 401
    assert clean_tasks == {} and spies.storage.calls == [] and spies.published == [] and spies.scheduled == []


def test_refusal_without_any_configured_credentials_is_still_401(monkeypatch, no_external_agents, clean_tasks):
    monkeypatch.setattr(server, "CREDENTIALS", {})
    monkeypatch.setattr(server, "ENABLE_AUTH", False)
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "external", "prompt": "x"}, headers={"X-API-Key": "k"})
        assert r.status_code == 401
    assert clean_tasks == {}


def test_no_task_is_left_queued_and_no_background_exception(tenant_auth, no_external_agents, clean_tasks):
    errors = []
    with _client() as c:
        c.portal.call(lambda: asyncio.get_running_loop().set_exception_handler(
            lambda loop, ctx: errors.append(ctx.get("exception") or ctx.get("message"))))
        r = c.post("/api/tasks", json={"specialization": "external", "prompt": "stuck?"},
                   headers=tenant_auth.headers)
        time.sleep(0.3)
    assert r.status_code == 409
    assert [t for t in clean_tasks.values() if t["status"] == "queued"] == []
    assert errors == []


def test_agent_removed_after_acceptance_fails_the_task_instead_of_leaving_it_queued(
        tenant_auth, no_external_agents, clean_tasks):
    """Defence for the window between 202 and the background run (an admin may
    remove the last agent): the task ends ``failed`` with a result, no exception."""
    task_id = "bug01race001"
    clean_tasks[task_id] = {"id": task_id, "specialization": "external", "prompt": "p", "status": "queued",
                            "created_at": time.time(), "result": None, "tenant_id": tenant_auth.tenant}
    asyncio.run(server._run_task(task_id, "external", "p", None))  # must not raise
    t = clean_tasks[task_id]
    assert t["status"] == "failed" and t["finished_at"]
    assert t["result"]["ok"] is False and "no external agent is registered" in t["result"]["result"]["error"]


# ------------------------------------------------------------------ the happy paths stay intact

class _AgentHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        prompt = json.loads(self.rfile.read(n))["messages"][0]["content"]
        body = json.dumps({"choices": [{"message": {"content": f"echo: {prompt}"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def local_agent(monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _AgentHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    adapter = ExternalAgentAdapter("bug01-local", f"http://127.0.0.1:{srv.server_port}/v1/chat",
                                   allow_private=True)  # admin-registered agents may be private (R-100)
    monkeypatch.setattr(server, "EXTERNAL_AGENTS", [adapter])
    yield adapter
    srv.shutdown()


def _wait_final(c, task_id, headers, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = c.get(f"/api/tasks/{task_id}", headers=headers).json()
        if t["status"] in ("completed", "failed"):
            return t
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} not final: {t['status']}")


def test_external_task_with_a_registered_agent_still_runs_to_completion(tenant_auth, local_agent, clean_tasks):
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "external", "prompt": "ping-bug01"},
                   headers=tenant_auth.headers)
        assert r.status_code == 202
        t = _wait_final(c, r.json()["task_id"], tenant_auth.headers)
    assert t["status"] == "completed" and t["agent"] == "bug01-local"
    assert t["result"]["ok"] is True and t["result"]["result"]["answer"] == "echo: ping-bug01"


def test_external_task_through_an_agent_registered_via_the_admin_api(tenant_auth, no_external_agents,
                                                                     clean_tasks, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _AgentHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with _client() as c:
            body = {"specialization": "external", "prompt": "after-register"}
            assert c.post("/api/tasks", json=body, headers=tenant_auth.headers).status_code == 409
            reg = {"name": "bug01-api", "url": f"http://127.0.0.1:{srv.server_port}/v1", "model": "", "api_key": ""}
            assert c.post("/api/agents/external", json=reg, headers=tenant_auth.admin_headers).status_code == 201
            r = c.post("/api/tasks", json=body, headers=tenant_auth.headers)
            assert r.status_code == 202
            t = _wait_final(c, r.json()["task_id"], tenant_auth.headers)
            assert t["status"] == "completed" and t["result"]["result"]["answer"] == "echo: after-register"
            assert c.delete("/api/agents/external/bug01-api", headers=tenant_auth.admin_headers).status_code == 200
            assert c.post("/api/tasks", json=body, headers=tenant_auth.headers).status_code == 409
    finally:
        srv.shutdown()


def test_internal_specialization_still_works_without_external_agents(tenant_auth, no_external_agents, clean_tasks):
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "analyst", "prompt": "1 2 3 4"},
                   headers=tenant_auth.headers)
        assert r.status_code == 202
        t = _wait_final(c, r.json()["task_id"], tenant_auth.headers)
    assert t["status"] == "completed"


def test_unknown_specialization_keeps_its_400_contract(tenant_auth, no_external_agents, clean_tasks, spies):
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "astrologer", "prompt": "x"},
                   headers=tenant_auth.headers)
    assert r.status_code == 400
    assert r.json()["detail"].startswith("Unknown specialization 'astrologer'. Valid: [")
    assert r.json()["detail"].endswith("+ ['external']")
    assert clean_tasks == {} and spies.storage.calls == [] and spies.scheduled == []


def test_external_tasks_stay_tenant_isolated(tenant_auth, local_agent, clean_tasks, monkeypatch):
    """SEC-02 regression on the external path: another tenant cannot read the task."""
    import hashlib
    from agent_civilization.core.tenancy import Principal
    other = "other-" + "x" * 20
    creds = dict(server.CREDENTIALS)
    creds[hashlib.sha256(other.encode()).hexdigest()] = Principal("other_tenant", "user")
    monkeypatch.setattr(server, "CREDENTIALS", creds)
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "external", "prompt": "mine"}, headers=tenant_auth.headers)
        tid = r.json()["task_id"]
        _wait_final(c, tid, tenant_auth.headers)
        assert c.get(f"/api/tasks/{tid}", headers={"X-API-Key": other}).status_code == 404
