"""BUG-02 (R-141): one status for a /api/chat execution — memory, storage and
the chat_completed event agree.

Before the fix ``POST /api/chat`` set the in-memory record (``TASKS``) to
``completed``/``failed`` from ``outcome["ok"]`` but passed a hard-coded
``"completed"`` to ``storage.insert_task`` and ``storage.update_task``, and
the ``chat_completed`` event carried ``ok`` with no ``status`` — a failed
execution was stored as completed in Supabase.

Three levels of evidence, all through the real route with test credentials:
  * a recording storage double: the exact arguments of every storage call;
  * the real ``SupabaseStorage`` against a minimal local PostgREST: the stored
    row and the full stored event (then read back through the API);
  * a real agent failing for a real reason (``test_runner`` with code
    execution disabled — the R-100 default), no agent double at all.
The agent is reached by the unchanged keyword router; only its ``execute``
is replaced where a controlled outcome is needed.
"""
from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

import server

MESSAGE = "run unit tests"          # keyword router -> test_runner (no LLM in tests)
SPEC = "test_runner"
RESPONSE_KEYS = {"task_id", "routed_to", "routing", "agent", "outcome"}


# ------------------------------------------------------------------ doubles

class _RecordingStorage:
    """Storage double: records the exact arguments of every call."""

    def __init__(self):
        self.calls = []

    def _rec(self, name, *a, **kw):
        self.calls.append((name, a, kw))

    async def insert_task(self, task_id, specialization, prompt, status="queued", *, tenant_id=None):
        self._rec("insert_task", task_id, specialization, prompt, status, tenant_id=tenant_id)
        return True

    async def update_task(self, task_id, status, agent_name=None, result=None, elapsed_s=None):
        self._rec("update_task", task_id, status, agent_name=agent_name, result=result, elapsed_s=elapsed_s)
        return True

    async def upsert_agent_stats(self, *a, **kw):
        self._rec("upsert_agent_stats", *a, **kw)
        return True

    async def insert_event(self, event_type, event_data, *, tenant_id=None):
        self._rec("insert_event", event_type, event_data, tenant_id=tenant_id)
        return True

    async def get_tasks(self, *a, **kw):
        return []

    async def get_events(self, *a, **kw):
        return []

    async def get_agent_stats(self):
        return []

    async def close(self):
        pass

    def only(self, name):
        found = [c for c in self.calls if c[0] == name]
        assert len(found) == 1, (name, self.calls)
        return found[0]


@pytest.fixture
def rec_storage(monkeypatch):
    st = _RecordingStorage()
    monkeypatch.setattr(server, "get_storage", lambda: st)
    return st


def _outcome(ok: bool, agent_name: str):
    result = {"answer": "all good"} if ok else {"error": "BUG02 test failure: simulated tool crash"}
    return {"ok": ok, "agent": agent_name, "specialization": SPEC, "elapsed_s": 0.01, "result": result}


@pytest.fixture
def agent_outcome(monkeypatch):
    """Make every test_runner agent return the outcome chosen by the test."""
    def _set(ok: bool):
        for agent in server.CIVILIZATION[SPEC]:
            async def execute(task, _agent=agent):
                return _outcome(ok, _agent.name)
            monkeypatch.setattr(agent, "execute", execute)
    return _set


def _chat(c, headers):
    r = c.post("/api/chat", json={"message": MESSAGE}, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == RESPONSE_KEYS            # response contract unchanged
    assert body["routed_to"] == SPEC and body["routing"]["method"] == "keywords"
    return body


def _assert_task_status(body, st, tenant, expected_status, ok):
    """Memory and both storage task writes carry one status."""
    tid, agent = body["task_id"], body["agent"]
    assert body["outcome"]["ok"] is ok
    # memory
    rec = server.TASKS[tid]
    assert rec["status"] == expected_status and rec["result"]["ok"] is ok and rec["tenant_id"] == tenant
    # storage: exact arguments of both task writes
    _, a, kw = st.only("insert_task")
    assert a == (tid, SPEC, MESSAGE, expected_status) and kw == {"tenant_id": tenant}
    _, a, kw = st.only("update_task")
    assert a == (tid, expected_status) and kw["agent_name"] == agent
    assert kw["result"]["ok"] is ok and kw["result"] == rec["result"]


def _assert_event(body, st, tenant, expected_status, ok):
    """The full chat_completed event record: ok and the same status."""
    tid, agent = body["task_id"], body["agent"]
    _, a, kw = st.only("insert_event")
    assert a == ("chat_completed", {"task_id": tid, "agent": agent, "specialization": SPEC,
                                    "ok": ok, "status": expected_status})
    assert kw == {"tenant_id": tenant}


# ------------------------------------------------------------------ recording double

def test_chat_success_is_completed_in_memory_and_storage(tenant_auth, rec_storage, agent_outcome):
    agent_outcome(True)
    with TestClient(server.app) as c:
        body = _chat(c, tenant_auth.headers)
    _assert_task_status(body, rec_storage, tenant_auth.tenant, "completed", True)


def test_chat_success_event_carries_status_completed(tenant_auth, rec_storage, agent_outcome):
    """New field (BUG-02): ``status`` next to ``ok`` in chat_completed."""
    agent_outcome(True)
    with TestClient(server.app) as c:
        body = _chat(c, tenant_auth.headers)
    _assert_event(body, rec_storage, tenant_auth.tenant, "completed", True)


def test_chat_failure_is_failed_in_memory_storage_and_event(tenant_auth, rec_storage, agent_outcome):
    agent_outcome(False)
    with TestClient(server.app) as c:
        body = _chat(c, tenant_auth.headers)
    assert body["outcome"]["result"]["error"] == "BUG02 test failure: simulated tool crash"
    _assert_task_status(body, rec_storage, tenant_auth.tenant, "failed", False)
    _assert_event(body, rec_storage, tenant_auth.tenant, "failed", False)
    # the failure is recorded as a success nowhere
    statuses = [c[1][3] for c in rec_storage.calls if c[0] == "insert_task"] + \
               [c[1][1] for c in rec_storage.calls if c[0] == "update_task"] + \
               [c[1][1]["status"] for c in rec_storage.calls if c[0] == "insert_event"] + \
               [server.TASKS[body["task_id"]]["status"]]
    assert statuses == ["failed"] * 4


def test_real_agent_failure_without_any_agent_double(tenant_auth, rec_storage):
    """The real test_runner, given a message with no code, fails for a real
    reason ("no code to run") — no agent double."""
    with TestClient(server.app) as c:
        body = _chat(c, tenant_auth.headers)
    assert body["outcome"]["ok"] is False and body["outcome"]["result"]["error"]
    _assert_task_status(body, rec_storage, tenant_auth.tenant, "failed", False)
    _assert_event(body, rec_storage, tenant_auth.tenant, "failed", False)


def test_unauthenticated_chat_still_401_and_writes_nothing(tenant_auth, rec_storage, agent_outcome):
    agent_outcome(False)
    with TestClient(server.app) as c:
        assert c.post("/api/chat", json={"message": MESSAGE}).status_code == 401
        assert c.post("/api/chat", json={"message": MESSAGE}, headers={"X-API-Key": "wrong"}).status_code == 401
    assert rec_storage.calls == []


# ------------------------------------------------------------------ real SupabaseStorage + local PostgREST

class _PostgREST(BaseHTTPRequestHandler):
    """Minimal PostgREST: POST rows, PATCH ?task_id=eq., GET with eq. filters."""
    rows = {"tasks": [], "events": [], "agent_stats": []}
    log = []  # (method, table, body) of every write, as received over HTTP
    seq = [0]

    def log_message(self, *a):
        pass

    def _table_q(self):
        u = urllib.parse.urlsplit(self.path)
        return u.path.rsplit("/", 1)[-1], urllib.parse.parse_qs(u.query)

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
        self.log.append(("POST", table, json.loads(json.dumps(row))))
        self.seq[0] += 1
        row.setdefault("created_at", f"2026-10-16T00:00:{self.seq[0]:02d}")
        self.rows[table].append(row)
        self._send(201)

    def do_PATCH(self):  # noqa: N802
        table, q = self._table_q()
        patch = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.log.append(("PATCH", table, json.loads(json.dumps(patch))))
        key = "task_id" if "task_id" in q else next(iter(q))
        want = q[key][0].removeprefix("eq.")
        for r in self.rows[table]:
            if str(r.get(key)) == want:
                r.update(patch)
        self._send(204)

    def do_GET(self):  # noqa: N802
        table, q = self._table_q()
        out = list(self.rows[table])
        for col, vals in q.items():
            if col in ("order", "limit", "select"):
                continue
            want = vals[0].removeprefix("eq.")
            out = [r for r in out if r.get(col) is not None and str(r.get(col)) == want]
        out.sort(key=lambda r: r["created_at"], reverse=True)
        self._send(200, out)


@pytest.fixture
def db(monkeypatch):
    _PostgREST.rows = {"tasks": [], "events": [], "agent_stats": []}
    _PostgREST.log = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _PostgREST)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    from agent_civilization.storage import supabase_store as ss
    monkeypatch.setattr(ss, "SUPABASE_URL", f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.setattr(ss, "SUPABASE_KEY", "bug02-test-not-a-real-key")
    monkeypatch.setattr(ss, "_storage", None)
    yield _PostgREST.rows
    httpd.shutdown()
    monkeypatch.setattr(ss, "_storage", None)


@pytest.mark.parametrize("ok,expected", [(True, "completed"), (False, "failed")])
def test_stored_task_row_matches_memory(tenant_auth, db, agent_outcome, ok, expected):
    agent_outcome(ok)
    with TestClient(server.app) as c:
        body = _chat(c, tenant_auth.headers)
        tid = body["task_id"]
        task_writes = [(m, b) for m, t, b in _PostgREST.log if t == "tasks"]
        assert [m for m, _ in task_writes] == ["POST", "PATCH"]
        assert task_writes[0][1]["status"] == expected and task_writes[0][1]["task_id"] == tid
        assert task_writes[1][1]["status"] == expected and task_writes[1][1]["result"]["ok"] is ok
        (row,) = [r for r in db["tasks"] if r["task_id"] == tid]
        assert row["status"] == expected and row["tenant_id"] == tenant_auth.tenant
        assert server.TASKS[tid]["status"] == expected
        listed = c.get("/api/tasks", headers=tenant_auth.headers).json()
        assert listed["source"] == "database"
        assert [t["status"] for t in listed["tasks"] if t["task_id"] == tid] == [expected]


@pytest.mark.parametrize("ok,expected", [(True, "completed"), (False, "failed")])
def test_stored_task_and_stored_event_match_memory(tenant_auth, db, agent_outcome, ok, expected):
    agent_outcome(ok)
    with TestClient(server.app) as c:
        body = _chat(c, tenant_auth.headers)
        tid = body["task_id"]
        # the two task writes exactly as sent to Supabase
        task_writes = [(m, b) for m, t, b in _PostgREST.log if t == "tasks"]
        assert [m for m, _ in task_writes] == ["POST", "PATCH"]
        assert task_writes[0][1]["status"] == expected and task_writes[0][1]["task_id"] == tid
        assert task_writes[1][1]["status"] == expected and task_writes[1][1]["result"]["ok"] is ok
        (row,) = [r for r in db["tasks"] if r["task_id"] == tid]
        assert row["status"] == expected and row["tenant_id"] == tenant_auth.tenant
        assert json.loads(json.dumps(row["result"]))["ok"] is ok
        (event,) = [e for e in db["events"] if e["event_type"] == "chat_completed"]
        assert event["event_data"] == {"task_id": tid, "agent": body["agent"], "specialization": SPEC,
                                       "ok": ok, "status": expected}
        assert event["tenant_id"] == tenant_auth.tenant
        assert server.TASKS[tid]["status"] == expected
        # read back through the API: the database path reports the same status
        listed = c.get("/api/tasks", headers=tenant_auth.headers).json()
        assert listed["source"] == "database"
        assert [t["status"] for t in listed["tasks"] if t["task_id"] == tid] == [expected]
        events = c.get("/api/events", headers=tenant_auth.headers).json()["events"]
        assert [e["event_data"]["status"] for e in events if e["event_type"] == "chat_completed"] == [expected]
