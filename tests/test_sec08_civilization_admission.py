"""SEC-08 (batch 293): the /civilization/* routes bypassed every protection the
/api/* routes have. Reproduced on main 710997f9 before the fix:

* D1 no rate limit — with AGOS_RATE_LIMIT_REQUESTS=2, five POST /civilization/plan
  calls all returned 200 (GET /api/agents returned 429 on the third);
* D2 unbounded input — POST /civilization/execute accepted a ~1 MB message
  (POST /api/tasks refuses > 20000 chars with 422);
* D3 no admission — execute/coordinate create an in-flight record in the shared
  task table without the R-164 caps; once in-flight records exceed
  MAX_TASKS_KEPT the pruner evicts running ones, and the handler then raised
  KeyError → 500 AFTER the work was done (result lost).

Acceptance (fixed before the change):
* every /civilization/* route is subject to the same per-client rate limit
  (429 + Retry-After) as the /api/* routes;
* ``message`` > 20000 chars → 422 on execute, coordinate and plan; nothing runs;
* execute/coordinate pass the same admission as volatile POST /api/tasks
  (per-tenant pending cap → 429, global cap → 503, accept rate → 429) BEFORE any
  record is written; a refused request leaves no record;
* if the record is gone when the run finishes, the response is still the
  result (no 500) and the record is restored as finished;
* behaviour of accepted requests is unchanged (existing route tests).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def srv(monkeypatch, tenant_auth):
    import server
    monkeypatch.setattr(server, "RATE_LIMIT_REQUESTS", 100000)
    server.reset_rate_limits()
    server._ACCEPTED_AT.clear()
    yield server
    server.reset_rate_limits()
    server._ACCEPTED_AT.clear()


@pytest.fixture
def client(srv, tenant_auth):
    return TestClient(srv.app, headers=tenant_auth.headers, raise_server_exceptions=False)


ROUTES = [
    ("get", "/civilization/registry", None),
    ("post", "/civilization/plan", {"message": "x"}),
    ("post", "/civilization/execute", {"message": "   "}),          # 400 after the limiter
    ("post", "/civilization/coordinate", {"message": "   "}),
    ("get", "/civilization/runs/nope", None),
    ("post", "/civilization/runs/nope/resume", {}),
]


@pytest.mark.parametrize("method,path,body", ROUTES)
def test_every_civilization_route_is_rate_limited(srv, client, monkeypatch, method, path, body):
    monkeypatch.setattr(srv, "RATE_LIMIT_REQUESTS", 2)
    srv.reset_rate_limits()
    call = getattr(client, method)
    codes = [(call(path, json=body) if body is not None else call(path)).status_code for _ in range(3)]
    assert codes[2] == 429, codes
    r = call(path, json=body) if body is not None else call(path)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


@pytest.mark.parametrize("path", ["/civilization/execute", "/civilization/coordinate", "/civilization/plan"])
def test_oversized_message_is_refused_before_anything_runs(srv, client, path):
    before = len(srv.TASKS)
    r = client.post(path, json={"message": "a" * 20001})
    assert r.status_code == 422
    assert len(srv.TASKS) == before


def test_message_at_the_limit_is_still_accepted(client):
    assert client.post("/civilization/plan", json={"message": "a" * 20000}).status_code == 200


def _fill_pending(srv, n, tenant):
    for i in range(n):
        srv.TASKS[f"fill-{tenant}-{i}"] = {"id": f"fill-{tenant}-{i}", "status": "running",
                                           "created_at": 0.0, "tenant_id": tenant}


@pytest.mark.parametrize("path", ["/civilization/execute", "/civilization/coordinate"])
def test_per_tenant_queue_cap_applies(srv, client, monkeypatch, tenant_auth, path):
    monkeypatch.setattr(srv, "TASK_QUEUE_MAX_PER_TENANT", 2)
    monkeypatch.setattr(srv, "TASKS", {})
    _fill_pending(srv, 2, tenant_auth.tenant)
    r = client.post(path, json={"message": "hello world"})
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert set(srv.TASKS) == {f"fill-{tenant_auth.tenant}-0", f"fill-{tenant_auth.tenant}-1"}


@pytest.mark.parametrize("path", ["/civilization/execute", "/civilization/coordinate"])
def test_global_cap_applies_and_running_records_are_never_evicted(srv, client, monkeypatch, path):
    monkeypatch.setattr(srv, "TASK_QUEUE_MAX_PER_TENANT", 0)
    monkeypatch.setattr(srv, "TASKS", {})
    _fill_pending(srv, srv.MAX_TASKS_KEPT, "other")
    r = client.post(path, json={"message": "hello world"})
    assert r.status_code == 503 and "Retry-After" in r.headers
    assert len(srv.TASKS) == srv.MAX_TASKS_KEPT
    assert all(t["status"] == "running" for t in srv.TASKS.values())


@pytest.mark.parametrize("path", ["/civilization/execute", "/civilization/coordinate"])
def test_accept_rate_applies(srv, client, monkeypatch, path):
    monkeypatch.setattr(srv, "TASK_ACCEPT_RATE_PER_MIN", 1)
    srv._ACCEPTED_AT.clear()
    first = client.post(path, json={"message": "hello world"})
    assert first.status_code == 200, first.text
    r = client.post(path, json={"message": "hello world"})
    assert r.status_code == 429


def test_refused_request_gives_its_accept_slot_back(srv, client, monkeypatch, tenant_auth):
    monkeypatch.setattr(srv, "TASK_ACCEPT_RATE_PER_MIN", 5)
    monkeypatch.setattr(srv, "TASK_QUEUE_MAX_PER_TENANT", 1)
    monkeypatch.setattr(srv, "TASKS", {})
    srv._ACCEPTED_AT.clear()
    _fill_pending(srv, 1, tenant_auth.tenant)
    assert client.post("/civilization/execute", json={"message": "hello"}).status_code == 429
    assert srv._ACCEPTED_AT.get(tenant_auth.tenant, []) == []


def test_record_lost_mid_run_does_not_turn_into_500(srv, client, monkeypatch):
    from agent_civilization import civilization_routes as cr

    async def fake(plan, civ, **kw):
        srv.TASKS.pop(kw["task_id"], None)     # evicted while running
        return {"run_id": "r-x", "ok": True}

    monkeypatch.setattr(cr, "execute_plan", fake)
    r = client.post("/civilization/execute", json={"message": "hello"})
    assert r.status_code == 200, r.text
    tid = r.json()["task_id"]
    assert srv.TASKS[tid]["status"] == "completed" and srv.TASKS[tid]["run_id"] == "r-x"
