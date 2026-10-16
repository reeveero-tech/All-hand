"""BUG-04 (R-143): ``GET /api/tasks/{task_id}`` reads a persisted task straight
from storage when it is not in process memory (after a restart), with the
tenant scope enforced inside the PostgREST query itself.

Before the fix the route read ``TASKS`` only: a task listed by
``GET /api/tasks`` (source=database) answered 404 by ID once memory was lost.

Everything goes through the real HTTP API and the real ``SupabaseStorage``
against a minimal local PostgREST that applies ``eq.`` filters like PostgREST
(``eq.`` never matches NULL) and records the exact query of every request.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

import server
from agent_civilization.core.tenancy import Principal

NOT_FOUND = {"detail": "task not found"}


class _PostgREST(BaseHTTPRequestHandler):
    rows = {"tasks": [], "events": [], "agent_stats": []}
    gets = []  # (table, {param: value}) of every GET, as received over HTTP
    seq = [0]

    def log_message(self, *a):
        pass

    def _table_q(self):
        u = urllib.parse.urlsplit(self.path)
        return u.path.rsplit("/", 1)[-1], {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}

    def _send(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        table, _ = self._table_q()
        row = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.seq[0] += 1
        row.setdefault("created_at", f"2026-10-16T00:00:{self.seq[0]:02d}+00:00")
        self.rows[table].append(row)
        self._send(201)

    def do_PATCH(self):  # noqa: N802
        table, q = self._table_q()
        patch = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        want = q["task_id"].removeprefix("eq.")
        for r in self.rows[table]:
            if r.get("task_id") == want:
                r.update(patch)
        self._send(204)

    def do_GET(self):  # noqa: N802
        table, q = self._table_q()
        self.gets.append((table, dict(q)))
        out = list(self.rows[table])
        for col, val in q.items():
            if col in ("order", "limit", "select"):
                continue
            want = val.removeprefix("eq.")
            out = [r for r in out if r.get(col) is not None and str(r.get(col)) == want]
        if q.get("order") == "created_at.desc":
            out.sort(key=lambda r: r["created_at"], reverse=True)
        if "limit" in q:
            out = out[: int(q["limit"])]
        self._send(200, out)


@pytest.fixture
def db(monkeypatch):
    _PostgREST.rows = {"tasks": [], "events": [], "agent_stats": []}
    _PostgREST.gets = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _PostgREST)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    from agent_civilization.storage import supabase_store as ss
    monkeypatch.setattr(ss, "SUPABASE_URL", f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.setattr(ss, "SUPABASE_KEY", "bug04-test-not-a-real-key")
    monkeypatch.setattr(ss, "_storage", None)
    yield _PostgREST.rows
    httpd.shutdown()
    monkeypatch.setattr(ss, "_storage", None)


@pytest.fixture
def tenants(tenant_auth, monkeypatch):
    """test_tenant (A, user), other_tenant (B, user), test_admin (admin)."""
    key_b = "tb-" + secrets.token_urlsafe(16)
    creds = dict(server.CREDENTIALS)
    creds[hashlib.sha256(key_b.encode()).hexdigest()] = Principal("other_tenant", "user")
    monkeypatch.setattr(server, "CREDENTIALS", creds)
    tenant_auth.headers_b = {"X-API-Key": key_b}
    return tenant_auth


@pytest.fixture(autouse=True)
def _clean_tasks():
    saved = dict(server.TASKS)
    server.TASKS.clear()
    yield
    server.TASKS.clear()
    server.TASKS.update(saved)


def _row(task_id, tenant, status="completed", ok=True, **extra):
    row = {"task_id": task_id, "specialization": "analyst", "prompt": "stored prompt " + task_id,
           "status": status, "tenant_id": tenant, "agent_name": "analyst_007",
           "result": {"ok": ok, "output": "stored output " + task_id}, "elapsed_s": 0.25,
           "created_at": "2026-10-15T10:00:00+00:00", "finished_at": "2026-10-15T10:00:01+00:00"}
    row.update(extra)
    return row


def _task_gets():
    return [q for t, q in _PostgREST.gets if t == "tasks"]


# ------------------------------------------------------- restart round trip

def test_task_created_through_api_is_readable_by_id_after_memory_loss(tenants, db):
    """The real lifecycle: POST /api/tasks -> executed -> persisted; process
    memory is cleared (restart); GET /api/tasks/{id} returns the stored task."""
    with TestClient(server.app) as c:
        r = c.post("/api/tasks", json={"specialization": "analyst", "prompt": "BUG-04: summarise 1 2 3"},
                   headers=tenants.headers)
        assert r.status_code == 202, r.text
        tid = r.json()["task_id"]
        mem = None
        for _ in range(200):
            mem = c.get(f"/api/tasks/{tid}", headers=tenants.headers).json()
            row = next((x for x in db["tasks"] if x["task_id"] == tid), None)
            if mem.get("status") in ("completed", "failed") and row and row["status"] == mem["status"] \
                    and row.get("result") is not None:
                break
            time.sleep(0.05)
        assert mem["status"] in ("completed", "failed")

        server.TASKS.clear()                               # restart: memory lost
        assert tid not in server.TASKS
        got = c.get(f"/api/tasks/{tid}", headers=tenants.headers)
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["id"] == body["task_id"] == tid
        assert body["status"] == mem["status"]
        assert body["result"] == mem["result"]
        assert body["tenant_id"] == tenants.tenant
        assert body["agent_name"] == mem["agent"]
        assert body["source"] == "database"
        # another tenant: identical to a missing ID
        other = c.get(f"/api/tasks/{tid}", headers=tenants.headers_b)
        missing = c.get("/api/tasks/doesnotexist", headers=tenants.headers_b)
        assert (other.status_code, other.json()) == (missing.status_code, missing.json()) == (404, NOT_FOUND)


@pytest.mark.parametrize("status,ok", [("completed", True), ("failed", False)])
def test_storage_only_task_returns_status_and_result(tenants, db, status, ok):
    db["tasks"].append(_row("t_stored_a1", "test_tenant", status=status, ok=ok))
    with TestClient(server.app) as c:
        r = c.get("/api/tasks/t_stored_a1", headers=tenants.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == status
    assert body["result"] == {"ok": ok, "output": "stored output t_stored_a1"}
    assert body["tenant_id"] == "test_tenant" and body["agent_name"] == "analyst_007"
    # the stored row, unchanged, plus the documented id alias and source marker — nothing invented
    assert body == {**_row("t_stored_a1", "test_tenant", status=status, ok=ok),
                    "id": "t_stored_a1", "source": "database"}


# --------------------------------------------------------- memory path

def test_memory_task_returned_without_db_query(tenants, db):
    db["tasks"].append(_row("t_mem_1", "test_tenant", status="completed"))   # would differ if read
    server.TASKS["t_mem_1"] = {"id": "t_mem_1", "specialization": "analyst", "prompt": "p",
                               "status": "running", "created_at": time.time(), "result": None,
                               "tenant_id": "test_tenant"}
    with TestClient(server.app) as c:
        r = c.get("/api/tasks/t_mem_1", headers=tenants.headers)
    assert r.status_code == 200
    assert r.json() == server.TASKS["t_mem_1"]                # exactly the memory record
    assert _task_gets() == []                                 # no storage read


def test_memory_task_of_other_tenant_is_404_without_db_fallback(tenants, db):
    """In memory but out of scope: 404 directly — never retried in storage
    (even if a matching row exists there)."""
    db["tasks"].append(_row("t_mem_a", "other_tenant"))
    server.TASKS["t_mem_a"] = {"id": "t_mem_a", "specialization": "analyst", "prompt": "p",
                               "status": "completed", "created_at": time.time(),
                               "result": {"ok": True}, "tenant_id": "test_tenant"}
    with TestClient(server.app) as c:
        r = c.get("/api/tasks/t_mem_a", headers=tenants.headers_b)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert _task_gets() == []


# ----------------------------------------------------------- isolation

def test_other_tenant_stored_task_is_identical_to_missing(tenants, db):
    db["tasks"].append(_row("t_owned_by_a", "test_tenant"))
    with TestClient(server.app) as c:
        cross = c.get("/api/tasks/t_owned_by_a", headers=tenants.headers_b)
        missing = c.get("/api/tasks/t_no_such_id", headers=tenants.headers_b)
    assert cross.status_code == missing.status_code == 404
    assert cross.json() == missing.json() == NOT_FOUND
    assert cross.content == missing.content
    assert "stored output" not in cross.text and "t_owned_by_a" not in cross.text
    # the refusal comes from the scoped query: B's tenant filter, not a post-filter
    assert _task_gets()[0] == {"task_id": "eq.t_owned_by_a", "tenant_id": "eq.other_tenant", "limit": "1"}


def test_admin_default_scope_vs_all_tenants(tenants, db):
    db["tasks"].append(_row("t_of_a", "test_tenant"))
    with TestClient(server.app) as c:
        default = c.get("/api/tasks/t_of_a", headers=tenants.admin_headers)
        everything = c.get("/api/tasks/t_of_a?all_tenants=true", headers=tenants.admin_headers)
        user_all = c.get("/api/tasks/t_of_a?all_tenants=true", headers=tenants.headers_b)
    assert (default.status_code, default.json()) == (404, NOT_FOUND)
    assert everything.status_code == 200 and everything.json()["tenant_id"] == "test_tenant"
    assert user_all.status_code == 403                         # unchanged SEC-02 rule
    gets = _task_gets()
    assert gets[0] == {"task_id": "eq.t_of_a", "tenant_id": "eq.test_admin", "limit": "1"}
    assert gets[1] == {"task_id": "eq.t_of_a", "limit": "1"}   # ALL_TENANTS: no tenant filter
    assert len(gets) == 2                                      # the 403 never reached storage


def test_legacy_null_owner_row_is_unreadable_by_tenants(tenants, db):
    db["tasks"].append(_row("t_legacy", None))
    with TestClient(server.app) as c:
        a = c.get("/api/tasks/t_legacy", headers=tenants.headers)
        b = c.get("/api/tasks/t_legacy", headers=tenants.headers_b)
        admin_default = c.get("/api/tasks/t_legacy", headers=tenants.admin_headers)
        admin_all = c.get("/api/tasks/t_legacy?all_tenants=true", headers=tenants.admin_headers)
    for r in (a, b, admin_default):
        assert (r.status_code, r.json()) == (404, NOT_FOUND)
    assert admin_all.status_code == 200 and admin_all.json()["tenant_id"] is None


def test_missing_id_is_404_same_format(tenants, db):
    with TestClient(server.app) as c:
        r = c.get("/api/tasks/t_never_existed", headers=tenants.headers)
    assert (r.status_code, r.json()) == (404, NOT_FOUND)


def test_anonymous_is_401_and_never_reaches_storage(tenants, db):
    db["tasks"].append(_row("t_anon", "test_tenant"))
    with TestClient(server.app) as c:
        r = c.get("/api/tasks/t_anon")
    assert r.status_code == 401
    assert _task_gets() == []


# ---------------------------------------------------------- exact query

def test_exact_query_by_id_independent_of_list_limit_and_order(tenants, db):
    """The target is the OLDEST of many rows — beyond any list page. The read
    is one filtered query (task_id + tenant_id + limit=1), no order, no scan."""
    db["tasks"].append(_row("t_oldest", "test_tenant", created_at="2020-01-01T00:00:00+00:00"))
    for i in range(250):
        db["tasks"].append(_row(f"t_new_{i:03d}", "test_tenant", created_at=f"2026-10-15T11:{i // 60:02d}:{i % 60:02d}+00:00"))
    with TestClient(server.app) as c:
        listed = c.get("/api/tasks?limit=200", headers=tenants.headers).json()
        assert "t_oldest" not in {t["task_id"] for t in listed["tasks"]}   # not in the list page
        _PostgREST.gets = []
        r = c.get("/api/tasks/t_oldest", headers=tenants.headers)
    assert r.status_code == 200 and r.json()["task_id"] == "t_oldest"
    assert _task_gets() == [{"task_id": "eq.t_oldest", "tenant_id": "eq.test_tenant", "limit": "1"}]


def test_storage_get_task_refuses_without_scope_and_bad_ids(db):
    """Storage layer: no scope -> nothing, no network; invalid scope -> error;
    an id outside the task-id alphabet is never sent as a filter."""
    import asyncio

    from agent_civilization.storage import supabase_store as ss
    db["tasks"].append(_row("t_x", "test_tenant"))
    st = ss.get_storage()
    assert asyncio.run(st.get_task("t_x")) is None
    assert asyncio.run(st.get_task("t_x", scope=None)) is None
    with pytest.raises(ValueError):
        asyncio.run(st.get_task("t_x", scope="bad tenant!"))
    assert asyncio.run(st.get_task("t_x,tenant_id.is.null", scope="test_tenant")) is None
    assert _task_gets() == []
    assert asyncio.run(st.get_task("t_x", scope="test_tenant"))["task_id"] == "t_x"


# ------------------------------------------------ storage not configured

def test_storage_not_configured_keeps_memory_behaviour(tenants, monkeypatch):
    from agent_civilization.storage import supabase_store as ss
    monkeypatch.setattr(ss, "SUPABASE_URL", "")
    monkeypatch.setattr(ss, "SUPABASE_KEY", "")
    monkeypatch.setattr(ss, "_storage", None)
    server.TASKS["t_m"] = {"id": "t_m", "specialization": "analyst", "prompt": "p", "status": "queued",
                           "created_at": time.time(), "result": None, "tenant_id": "test_tenant"}
    with TestClient(server.app) as c:
        own = c.get("/api/tasks/t_m", headers=tenants.headers)
        other = c.get("/api/tasks/t_m", headers=tenants.headers_b)
        missing = c.get("/api/tasks/t_absent", headers=tenants.headers)
    assert own.status_code == 200 and own.json() == server.TASKS["t_m"]
    assert (other.status_code, other.json()) == (missing.status_code, missing.json()) == (404, NOT_FOUND)
