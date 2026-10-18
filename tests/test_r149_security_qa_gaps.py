"""R-149 (Security QA): storage-failure and leak gaps of the real request paths.

What was already proven elsewhere (not repeated here):

* the storage LAYER's failure contract — R-082 (test_r082_storage_outcomes):
  HTTP errors and transport exceptions become ``False``/``None``/``[]``, reads
  stay tenant-scoped, and the failure stats keep no keys, bodies or URLs;
* the success-path consistency of /api/chat — BUG-02;
* the tenant scoping of stored reads — BUG-04 and SEC-02;
* the outbound controls — SEC-01 and SEC-05.

What none of them proved, and this file closes:

* G1: a storage failure injected at the HTTP the REAL ``SupabaseStorage``
  speaks, driven THROUGH ``/api/chat`` and the background task path — the
  response contract, the in-memory truth, the attempted write payloads, what
  actually saved, the published events and the failure stats, recorded
  separately, for a successful and a failed execution, with selective
  per-payload failures (the final update rejected after the insert and the
  running update were accepted, the final event rejected after the creation
  event was accepted) and a deterministic transport timeout;
* G2: ``GET /api/tasks/{id}`` when the stored read itself fails (status
  error, transport cut, partial response, timeout) — the current degraded
  contract (404, indistinguishable from a missing row) with every read
  attempt carrying the caller's tenant scope and no unscoped fallback;
* G3: a secret marker that really passes through the error paths (a
  rejected-write body, an external backend echoing the adapter's key, an
  internal exception) must not reach responses, results, events or logs;
* G4: an exception raised INSIDE a real agent's execution (the handler the
  agent calls — ``execute()`` itself untouched) must end the task failed,
  never leave it pending.

All deterministic and local: a controllable PostgREST double on 127.0.0.1
behind the REAL storage layer, real test credentials, no external service.
"""
from __future__ import annotations

import copy
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

import server
from agent_civilization.storage import supabase_store as ss

SPEC = "test_runner"                       # keyword router: "run unit tests"
MESSAGE = "run unit tests"
MARKER = "R149-SECRET-MARKER-7f3a9c"
CHAT_KEYS = {"task_id", "routed_to", "routing", "agent", "outcome"}


# ------------------------------------------------------------------ doubles

class _R149PostgREST(BaseHTTPRequestHandler):
    """Controllable PostgREST: per-(method, table) verdicts over real HTTP.

    ``policy[(method, table)]`` is one of:

    * an int — the backend rejects the call with that HTTP status;
    * ``"cut"`` — the transport dies after the full request, before any
      byte of the response;
    * ``"partial"`` — response headers plus PART of the declared body, then
      the transport dies: a failure mid-read of the response body;
    * ``"slow"`` — the backend sleeps past the (patched, short) client
      timeout: a deterministic transport timeout;
    * a dict — payload discrimination: the call is rejected only when every
      key/value pair of the dict matches the request payload (e.g. reject
      the final ``completed`` update but accept the ``running`` one);
    * absent — the call is accepted and the row stored.

    Every attempt — accepted or not — is recorded with a DEEP COPY of its
    payload and its query parameters (the tenant scope of each request is
    evidence, not an assumption), and the stored rows are independent
    copies (a later PATCH must not rewrite the recorded payloads).
    """
    policy: dict = {}
    error_body: str = ""
    requests_log: list = []                # (method, table, query-dict, body-copy)
    rows = {"tasks": [], "events": [], "agent_stats": []}
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

    def _verdict(self, method, table, body):
        """Record the attempt, then apply the policy. True = accepted."""
        self.requests_log.append((method, table, dict(self._table_q()[1]),
                                   copy.deepcopy(body)))
        verdict = self.policy.get((method, table))
        if verdict == "cut":
            self.connection.close()        # transport dies, no HTTP answer
            return False
        if verdict == "partial":
            data = json.dumps({"message": self.error_body}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data) + 64))
            self.end_headers()
            self.wfile.write(data[:8])      # part of the declared body...
            self.connection.close()         # ...then the transport dies
            return False
        if verdict == "slow":
            time.sleep(1.0)                 # past the patched client timeout
            return False
        if isinstance(verdict, int):
            self._send(verdict, {"message": self.error_body})
            return False
        if isinstance(verdict, dict):       # payload discrimination
            if isinstance(body, dict) and all(body.get(k) == v
                                              for k, v in verdict.items()):
                self._send(403, {"message": self.error_body})
                return False
            return True
        return True

    def do_POST(self):  # noqa: N802
        table, _ = self._table_q()
        row = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if not self._verdict("POST", table, row):
            return
        self.seq[0] += 1
        row.setdefault("created_at", f"2026-10-16T00:00:{self.seq[0]:02d}")
        self.rows[table].append(copy.deepcopy(row))
        self._send(201)

    def do_PATCH(self):  # noqa: N802
        table, q = self._table_q()
        patch = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if not self._verdict("PATCH", table, patch):
            return
        key = "task_id" if "task_id" in q else next(iter(q))
        want = q[key][0].removeprefix("eq.")
        matched = []
        for r in self.rows[table]:
            if str(r.get(key)) == want:
                r.update(copy.deepcopy(patch))
                matched.append(r)
        # R-161c: like PostgREST — return=representation answers 200 with the
        # updated rows (projected by select=), [] when nothing matched;
        # without it, 204 whether or not a row matched
        if "return=representation" in (self.headers.get("Prefer") or ""):
            cols = q["select"][0].split(",") if "select" in q else None
            self._send(200, [{c: r.get(c) for c in cols} if cols else r for r in matched])
        else:
            self._send(204)

    def do_GET(self):  # noqa: N802
        table, q = self._table_q()
        self.requests_log.append(("GET", table, dict(q), None))
        verdict = self.policy.get(("GET", table))
        if verdict == "cut":
            self.connection.close()
            return
        if verdict == "partial":
            data = json.dumps([{"message": self.error_body}]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data) + 64))
            self.end_headers()
            self.wfile.write(data[:8])
            self.connection.close()
            return
        if verdict == "slow":
            time.sleep(1.0)
            return
        if isinstance(verdict, int):
            self._send(verdict, {"message": self.error_body})
            return
        out = list(self.rows[table])
        for col, vals in q.items():
            if col in ("order", "limit", "select"):
                continue
            want = vals[0].removeprefix("eq.")
            out = [r for r in out if r.get(col) is not None and str(r.get(col)) == want]
        out.sort(key=lambda r: r["created_at"], reverse=True)
        self._send(200, out)


@pytest.fixture
def postgrest(monkeypatch):
    _R149PostgREST.policy = {}
    _R149PostgREST.error_body = ""
    _R149PostgREST.requests_log = []
    _R149PostgREST.rows = {"tasks": [], "events": [], "agent_stats": []}
    _R149PostgREST.seq = [0]
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _R149PostgREST)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(ss, "SUPABASE_URL", f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.setattr(ss, "SUPABASE_KEY", "r149-test-storage-key")
    monkeypatch.setattr(ss, "SUPABASE_KEY_SOURCE", "SUPABASE_SERVICE_KEY")
    monkeypatch.setattr(ss, "_storage", None)
    yield _R149PostgREST
    httpd.shutdown()
    monkeypatch.setattr(ss, "_storage", None)


@pytest.fixture
def agent_outcome(monkeypatch):
    """Every test_runner agent returns the outcome chosen by the test."""
    def _set(ok: bool):
        for agent in server.CIVILIZATION[SPEC]:
            async def execute(task, _agent=agent, _ok=ok):
                result = ({"answer": "all good"} if _ok
                          else {"error": "R-149 controlled execution failure"})
                return {"ok": _ok, "agent": _agent.name, "specialization": SPEC,
                        "elapsed_s": 0.01, "result": result}
            monkeypatch.setattr(agent, "execute", execute)
    return _set


@pytest.fixture
def published(monkeypatch):
    """Record every published SSE event (tenant, event)."""
    out = []
    async def _pub(tenant_id, event):
        out.append((tenant_id, dict(event)))
    monkeypatch.setattr(server, "_publish", _pub)
    return out


@pytest.fixture
def clean_tasks(monkeypatch):
    monkeypatch.setattr(server, "TASKS", {})
    return server.TASKS


def _wait_final(task_id, published_events, timeout=10.0):
    """Wait until the background run is REALLY done: the final status in
    TASKS AND the task_completed event published for this task id (the
    status is set before the final storage write, the event after it —
    waiting for both leaves nothing pending behind). A task left pending
    fails here with its status: that is the assertion. (This polls
    server.TASKS directly, not through the HTTP route.)"""
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        status = server.TASKS[task_id]["status"]
        done = status in ("completed", "failed") and any(
            e["type"] == "task_completed" and e.get("task_id") == task_id
            for _, e in published_events)
        if done:
            return status
        time.sleep(0.05)
    raise AssertionError(f"task {task_id} never became final: {status}")


def _chat(c, headers):
    r = c.post("/api/chat", json={"message": MESSAGE}, headers=headers)
    assert r.status_code == 200, r.text
    return r


def _writes(method=None, table=None):
    return [w for w in _R149PostgREST.requests_log
            if (method is None or w[0] == method) and (table is None or w[1] == table)]


# ------------------------------------------------------------------ G1: /api/chat

def test_chat_with_every_write_rejected_401_stays_true_and_attempts_all_writes(
        tenant_auth, postgrest, agent_outcome):
    agent_outcome(True)
    postgrest.policy = {("POST", "tasks"): 401, ("PATCH", "tasks"): 401,
                        ("POST", "agent_stats"): 401, ("POST", "events"): 401}
    postgrest.error_body = f"rejected: {MARKER}"
    with TestClient(server.app) as c:
        r = _chat(c, tenant_auth.headers)
        body = r.json()
        assert set(body) == CHAT_KEYS and body["outcome"]["ok"] is True
        assert MARKER not in r.text                        # no leak to the response
        tid = body["task_id"]
    # the in-memory truth is untouched by the storage outage
    rec = server.TASKS[tid]
    assert rec["status"] == "completed" and rec["result"]["ok"] is True
    assert rec["tenant_id"] == tenant_auth.tenant
    # all four writes were ATTEMPTED (not skipped) and all were rejected
    assert [w[0:2] for w in _writes()] == [("POST", "tasks"), ("PATCH", "tasks"),
                                           ("POST", "agent_stats"), ("POST", "events")]
    assert _R149PostgREST.rows["tasks"] == [] and _R149PostgREST.rows["events"] == []
    # the layer's own record: the failure is visible, never reported as success
    stats = ss.storage_stats()
    assert stats["last_ok"] is False
    assert stats["operations"]["insert_task"]["failed"] >= 1
    assert stats["last_error"]["reason"] == "HTTP 401"


def test_chat_with_initial_save_rejected_403_attempts_the_true_update(
        tenant_auth, postgrest, agent_outcome):
    agent_outcome(True)
    postgrest.policy = {("POST", "tasks"): 403}
    postgrest.error_body = f"rls: {MARKER}"
    with TestClient(server.app) as c:
        body = _chat(c, tenant_auth.headers).json()
        tid = body["task_id"]
        task_text = c.get(f"/api/tasks/{tid}", headers=tenant_auth.headers).text
    assert server.TASKS[tid]["status"] == "completed"
    assert MARKER not in task_text
    # the final update was still attempted, carrying the true status and result
    (patch,) = [w for w in _writes("PATCH", "tasks")]
    assert patch[3]["status"] == "completed" and patch[3]["result"]["ok"] is True
    # nothing was saved (the only accepted row would have been the insert)
    assert _R149PostgREST.rows["tasks"] == []
    # R-161c: the insert's 403 is recorded, and the update is no longer
    # counted as a success — it matched no row, so nothing was updated
    stats = ss.storage_stats()
    assert stats["operations"]["insert_task"] == {"ok": 0, "failed": 1}
    assert stats["operations"]["update_task"] == {"ok": 0, "failed": 1}
    assert stats["last_error"]["operation"] == "update_task"
    assert stats["last_error"]["reason"] == "no row updated"
    assert MARKER not in json.dumps(stats)


def test_chat_with_final_update_rejected_500_keeps_memory_truth(
        tenant_auth, postgrest, agent_outcome):
    agent_outcome(False)                                   # a FAILED execution
    postgrest.policy = {("PATCH", "tasks"): 500}
    postgrest.error_body = f"boom: {MARKER}"
    with TestClient(server.app) as c:
        body = _chat(c, tenant_auth.headers).json()
        tid = body["task_id"]
    # memory and the attempted payload say failed; the row exists but is stale
    assert server.TASKS[tid]["status"] == "failed"
    (patch,) = [w for w in _writes("PATCH", "tasks")]
    assert patch[3]["status"] == "failed" and patch[3]["result"]["ok"] is False
    (row,) = _R149PostgREST.rows["tasks"]
    assert row["status"] == "failed"                      # the insert already said it
    assert ss.storage_stats()["last_error"]["reason"] == "HTTP 500"


def test_chat_with_transport_cut_before_response(tenant_auth, postgrest, agent_outcome):
    agent_outcome(True)
    postgrest.policy = {("POST", "tasks"): "cut"}
    with TestClient(server.app) as c:
        body = _chat(c, tenant_auth.headers).json()
        tid = body["task_id"]
    assert server.TASKS[tid]["status"] == "completed"
    # the cut is the recorded failure (kept for diagnosis); the later writes
    # recovered, so last_ok reflects the last operation, not the cut
    stats = ss.storage_stats()
    assert stats["operations"]["insert_task"]["failed"] >= 1
    # R-161c: the row was never created, so the update matched nothing and is
    # a failure too (it used to be counted ok on a bare 204)
    assert stats["operations"]["update_task"] == {"ok": 0, "failed": 1}
    assert stats["last_error"]["operation"] == "update_task"
    # the later writes still happened (the cut did not abort the request)
    assert [w[0:2] for w in _writes()] == [("POST", "tasks"), ("PATCH", "tasks"),
                                           ("POST", "agent_stats"), ("POST", "events")]


def test_chat_response_and_events_never_carry_the_backend_error_marker(
        tenant_auth, postgrest, agent_outcome):
    """G3: the marker really passes through the layer's error path (the
    rejected body) — it must not surface anywhere the tenant can see: the
    chat response, the task record, the events route."""
    agent_outcome(True)
    postgrest.policy = {("POST", "tasks"): 401, ("PATCH", "tasks"): 401,
                        ("POST", "agent_stats"): 401, ("POST", "events"): 401}
    postgrest.error_body = f"row-level detail leaked: {MARKER}"
    with TestClient(server.app) as c:
        chat_text = _chat(c, tenant_auth.headers).text
        events_text = c.get("/api/events", headers=tenant_auth.headers).text
        tasks_text = c.get("/api/tasks", headers=tenant_auth.headers).text
    assert MARKER not in chat_text and MARKER not in events_text and MARKER not in tasks_text


# ------------------------------------------------------------------ G1: background task path

def test_background_task_with_every_write_rejected_ends_final_not_pending(
        tenant_auth, postgrest, agent_outcome, published, clean_tasks):
    agent_outcome(True)
    postgrest.policy = {("POST", "tasks"): 401, ("PATCH", "tasks"): 401,
                        ("POST", "agent_stats"): 401, ("POST", "events"): 401}
    with TestClient(server.app) as c:
        r = c.post("/api/tasks", json={"specialization": SPEC, "prompt": MESSAGE},
                   headers=tenant_auth.headers)
        assert r.status_code == 202 and r.json() == {"task_id": r.json()["task_id"],
                                                    "status": "queued"}
        tid = r.json()["task_id"]
        # the background run is the one create_task itself scheduled (its own
        # loop, its own storage session) — we only wait for it to fully end
        assert _wait_final(tid, published) == "completed"
    # the task ends FINAL — a storage outage must not leave it pending
    assert server.TASKS[tid]["status"] == "completed"
    assert server.TASKS[tid]["result"]["ok"] is True
    # every write was attempted with the true payloads
    patches = [w[3] for w in _writes("PATCH", "tasks")]
    assert [p["status"] for p in patches] == ["running", "completed"]
    assert _R149PostgREST.rows["tasks"] == [] and _R149PostgREST.rows["events"] == []
    # the published events tell the truth: started, then completed ok
    kinds = [(t, e["type"], e.get("ok")) for t, e in published]
    assert ("test_tenant", "task_started", None) in kinds
    assert ("test_tenant", "task_completed", True) in kinds
    assert ss.storage_stats()["last_ok"] is False


def test_background_task_final_update_rejected_after_running_saved(
        tenant_auth, postgrest, agent_outcome, published, clean_tasks):
    """The case a blanket policy cannot prove: the insert and the `running`
    update are ACCEPTED, only the FINAL update is rejected. The durable row
    stays stale at `running` (never reported as a saved success) while the
    memory record and the published event carry the true final status."""
    agent_outcome(True)
    postgrest.policy = {("PATCH", "tasks"): {"status": "completed"}}
    with TestClient(server.app) as c:
        r = c.post("/api/tasks", json={"specialization": SPEC, "prompt": MESSAGE},
                   headers=tenant_auth.headers)
        tid = r.json()["task_id"]
        assert _wait_final(tid, published) == "completed"
    # the attempted payloads: running (accepted), completed (rejected)
    patches = [w[3] for w in _writes("PATCH", "tasks")]
    assert [p["status"] for p in patches] == ["running", "completed"]
    (row,) = _R149PostgREST.rows["tasks"]
    assert row["status"] == "running"                    # stale, not a saved success
    assert server.TASKS[tid]["status"] == "completed"     # the memory truth
    assert ("test_tenant", "task_completed", True) in [(t, e["type"], e.get("ok"))
                                                       for t, e in published]
    assert ss.storage_stats()["last_error"]["reason"] == "HTTP 403"


def test_background_task_final_event_rejected_after_creation_saved(
        tenant_auth, postgrest, agent_outcome, published, clean_tasks):
    """Only the task_completed event is rejected; the task_created event was
    accepted. The SSE bus still tells the tenant the truth."""
    agent_outcome(False)                                  # a FAILED execution
    postgrest.policy = {("POST", "events"): {"event_type": "task_completed"}}
    with TestClient(server.app) as c:
        r = c.post("/api/tasks", json={"specialization": SPEC, "prompt": MESSAGE},
                   headers=tenant_auth.headers)
        tid = r.json()["task_id"]
        assert _wait_final(tid, published) == "failed"
    assert server.TASKS[tid]["status"] == "failed"
    saved_types = [e["event_type"] for e in _R149PostgREST.rows["events"]]
    assert saved_types == ["task_created"]                # created saved, completed not
    assert ("test_tenant", "task_completed", False) in [(t, e["type"], e.get("ok"))
                                                       for t, e in published]
    assert ss.storage_stats()["last_error"]["operation"] == "insert_event"


# ------------------------------------------------------------------ G4: exception inside a real agent

def _patch_handler_dispatch(monkeypatch, fn):
    """R-158 (G-157-5): inject ``fn`` as the shared handler dispatcher on BOTH
    runtime bindings. ``real_agents`` binds ``_dispatch`` at import time; the
    ``specialists`` templates import ``real_dispatcher.dispatch`` at call time.
    Patching only the first left the injected crash inert when the server is
    built with AGOS_AGENT_IMPL=specialists (the 3 tests below then failed for
    a fixture reason, not a behaviour one — docs/audit/M8/logs/raw/092_*)."""
    import agent_civilization.agents.real.real_agents as ra
    from agent_civilization.core.agents import real_dispatcher
    monkeypatch.setattr(ra, "_dispatch", fn)
    monkeypatch.setattr(real_dispatcher, "dispatch", fn)


def test_handler_exception_inside_real_agent_ends_task_failed(
        tenant_auth, postgrest, published, clean_tasks, monkeypatch):
    """The agent's own exception handling is the contract under test:
    ``execute()`` stays REAL; the handler it calls raises. The task must end
    failed with the error in its result — never stay pending."""
    def crashing_dispatch(specialization, work):
        raise RuntimeError("R-149 controlled handler crash")
    _patch_handler_dispatch(monkeypatch, crashing_dispatch)
    with TestClient(server.app) as c:
        r = c.post("/api/tasks", json={"specialization": SPEC, "prompt": MESSAGE},
                   headers=tenant_auth.headers)
        assert r.status_code == 202
        tid = r.json()["task_id"]
        assert _wait_final(tid, published) == "failed"
    rec = server.TASKS[tid]
    assert rec["status"] == "failed"
    assert rec["result"]["ok"] is False
    # only the exception TYPE is reported (R-149: raw text is untrusted)
    assert rec["result"]["result"]["error"] == "RuntimeError"
    assert "R-149 controlled handler crash" not in str(rec["result"]["result"])
    assert ("test_tenant", "task_completed", False) in [(t, e["type"], e.get("ok"))
                                                       for t, e in published]
    # the stored attempt and the stored event (accepted here) carry the same truth
    (patch,) = [w for w in _writes("PATCH", "tasks") if w[3].get("status") == "failed"]
    assert patch[3]["result"]["ok"] is False
    (event,) = [e for e in _R149PostgREST.rows["events"] if e["event_type"] == "task_completed"]
    assert event["event_data"]["ok"] is False and event["tenant_id"] == "test_tenant"


def test_internal_exception_marker_reaches_nowhere(
        tenant_auth, postgrest, published, clean_tasks, monkeypatch, caplog):
    """G3 (internal exception): a marker travels inside an exception raised
    by a handler the real agent calls. An unexpected exception's raw text is
    not trusted — it can carry internal details — so only the exception TYPE
    may be reported; the marker must not surface anywhere: the response, the
    task result, the events, the logs. The task still ends failed."""
    def crashing_dispatch(specialization, work):
        raise RuntimeError(f"handler crashed: {MARKER}")
    _patch_handler_dispatch(monkeypatch, crashing_dispatch)
    with TestClient(server.app) as c:
        r = c.post("/api/tasks", json={"specialization": SPEC, "prompt": MESSAGE},
                   headers=tenant_auth.headers)
        tid = r.json()["task_id"]
        assert _wait_final(tid, published) == "failed"
        task_text = c.get(f"/api/tasks/{tid}", headers=tenant_auth.headers).text
    rec = server.TASKS[tid]
    assert rec["status"] == "failed" and rec["result"]["ok"] is False
    assert rec["result"]["result"]["error"] == "RuntimeError"   # the type only
    assert MARKER not in task_text                       # never tenant-visible
    assert MARKER not in caplog.text                     # never in the logs
    for _, e in published:
        assert MARKER not in json.dumps(e)               # never in the events
    (event,) = [e for e in _R149PostgREST.rows["events"] if e["event_type"] == "task_completed"]
    assert MARKER not in json.dumps(event)               # nor in the stored event


def test_internal_dependency_timeout_ends_failed_without_leak(
        tenant_auth, postgrest, published, clean_tasks, monkeypatch, caplog):
    """Deterministic execution-timeout case (no live waiting): an internal
    dependency raises TimeoutError carrying a marker; ``execute()`` stays
    real. The task ends failed with the TYPE only — the marker reaches
    nowhere."""
    def timing_out_dispatch(specialization, work):
        raise TimeoutError(f"internal dependency timed out: {MARKER}")
    _patch_handler_dispatch(monkeypatch, timing_out_dispatch)
    with TestClient(server.app) as c:
        r = c.post("/api/tasks", json={"specialization": SPEC, "prompt": MESSAGE},
                   headers=tenant_auth.headers)
        tid = r.json()["task_id"]
        assert _wait_final(tid, published) == "failed"
        task_text = c.get(f"/api/tasks/{tid}", headers=tenant_auth.headers).text
    rec = server.TASKS[tid]
    assert rec["status"] == "failed" and rec["result"]["ok"] is False
    assert rec["result"]["result"]["error"] == "TimeoutError"    # the type only
    assert MARKER not in task_text and MARKER not in caplog.text
    for _, e in published:
        assert MARKER not in json.dumps(e)


def test_connection_error_family_text_is_not_trusted(
        tenant_auth, published, clean_tasks, monkeypatch, caplog):
    """R-149 review round 4: the ClientConnectionError FAMILY is not trusted
    by class — any code can raise it with arbitrary text (e.g. a wrapped
    backend body echoing the adapter's credential). A dependency of the real
    adapter's execute() raising aiohttp.ClientConnectionError(marker) must
    land only the TYPE in the tenant-visible result and the log. Only reasons
    this module formulates itself (the crafted DNS texts, the redirect-loop
    bound) keep their text — proven by the SEC-05 tests."""
    import aiohttp
    from agent_civilization.agents.external_adapter import ExternalAgentAdapter

    def _marker_request(*a, **kw):
        raise aiohttp.ClientConnectionError(f"connector said: {MARKER}")

    monkeypatch.setattr(aiohttp.ClientSession, "request", _marker_request)
    monkeypatch.setattr(server, "EXTERNAL_AGENTS", [
        ExternalAgentAdapter("r149-conn", "http://127.0.0.1:1/v1/chat",
                             api_key="unused", allow_private=True)])
    with TestClient(server.app) as c:
        r = c.post("/api/tasks", json={"specialization": "external", "prompt": "hi"},
                   headers=tenant_auth.headers)
        assert r.status_code == 202
        tid = r.json()["task_id"]
        assert _wait_final(tid, published) == "failed"
        task_text = c.get(f"/api/tasks/{tid}", headers=tenant_auth.headers).text
    rec = server.TASKS[tid]
    assert rec["result"]["result"]["error"] == "ClientConnectionError"  # type only
    assert MARKER not in task_text and MARKER not in caplog.text
    for _, e in published:
        assert MARKER not in json.dumps(e)


def test_tls_certificate_error_text_is_not_trusted(
        tenant_auth, published, clean_tasks, monkeypatch, caplog):
    """R-149 review round 4: a TLS certificate verification failure is
    reported with a FIXED reason this module formulates (mismatch keeps its
    distinct text — see the SEC-05 TLS test) — never the ssl verification
    error's raw text, which is not ours to forward."""
    import aiohttp
    import ssl as _ssl
    from aiohttp.client_reqrep import ConnectionKey
    from aiohttp.helpers import sentinel
    from agent_civilization.agents.external_adapter import ExternalAgentAdapter

    key = ConnectionKey(host="example.test", port=443, is_ssl=True, ssl=sentinel,
                       proxy=None, proxy_auth=None, proxy_headers_hash=None)

    def _marker_request(*a, **kw):
        raise aiohttp.ClientConnectorCertificateError(
            key, _ssl.SSLCertVerificationError(f"verify failed: {MARKER}"))

    monkeypatch.setattr(aiohttp.ClientSession, "request", _marker_request)
    monkeypatch.setattr(server, "EXTERNAL_AGENTS", [
        ExternalAgentAdapter("r149-tls", "https://example.test/v1/chat",
                             api_key="unused", allow_private=True)])
    with TestClient(server.app) as c:
        r = c.post("/api/tasks", json={"specialization": "external", "prompt": "hi"},
                   headers=tenant_auth.headers)
        assert r.status_code == 202
        tid = r.json()["task_id"]
        assert _wait_final(tid, published) == "failed"
        task_text = c.get(f"/api/tasks/{tid}", headers=tenant_auth.headers).text
    rec = server.TASKS[tid]
    assert rec["result"]["result"]["error"] == "TLS certificate verification failed"
    assert MARKER not in task_text and MARKER not in caplog.text
    for _, e in published:
        assert MARKER not in json.dumps(e)


def test_storage_exception_marker_stays_out_of_responses(
        tenant_auth, postgrest, agent_outcome, caplog, monkeypatch):
    """G3 (storage exception): a marker travels inside an exception raised
    below the storage layer (injected at the transport boundary the layer's
    except handles). The responses and the tenant-visible data must stay
    clean; the layer's own failure log is the operator-side diagnostic —
    the marker may appear THERE only, and nowhere else."""
    agent_outcome(True)

    async def raising_session(self):
        raise RuntimeError(f"transport failed: {MARKER}")
    monkeypatch.setattr(ss.SupabaseStorage, "_get_session", raising_session)
    with TestClient(server.app) as c:
        r = _chat(c, tenant_auth.headers)
        tasks_text = c.get("/api/tasks", headers=tenant_auth.headers).text
        events_text = c.get("/api/events", headers=tenant_auth.headers).text
    assert r.status_code == 200
    assert MARKER not in r.text and MARKER not in tasks_text and MARKER not in events_text
    assert ss.storage_stats()["last_ok"] is False
    # the failure stats keep only the exception TYPE (never the raw text)
    assert ss.storage_stats()["last_error"]["reason"] == "exception: RuntimeError"
    # the layer's log keeps the operation + the exception TYPE only — the
    # raw text (which can carry internal details) must not be logged either
    assert MARKER not in caplog.text
    assert "insert_task failed: RuntimeError" in caplog.text   # the diagnosis stays


def test_storage_read_timeout_is_degraded_404(tenant_auth, postgrest, clean_tasks,
                                             monkeypatch):
    """A deterministic transport timeout (no live waiting): the client
    timeout is patched to 0.2 s, the backend sleeps past it. The read fails
    inside the REAL layer and the route degrades exactly like any other
    failed read: 404, with every attempt still tenant-scoped."""
    tid = "r149stored04"
    _seed_row(tid, tenant_auth.tenant)
    postgrest.policy = {("GET", "tasks"): "slow"}
    import aiohttp
    real_timeout = aiohttp.ClientTimeout
    monkeypatch.setattr(aiohttp, "ClientTimeout", lambda **kw: real_timeout(total=0.2))
    with TestClient(server.app) as c:
        r = c.get(f"/api/tasks/{tid}", headers=tenant_auth.headers)
        assert r.status_code == 404 and r.json() == {"detail": "task not found"}
    for _, _, query, _ in _writes("GET", "tasks"):
        assert query.get("task_id") == [f"eq.{tid}"]
        assert query.get("tenant_id") == [f"eq.{tenant_auth.tenant}"]
    assert ss.storage_stats()["last_error"]["reason"].startswith("exception: ")


# ------------------------------------------------------------------ G3: leaks

def test_external_agent_error_must_not_echo_its_key_to_tenants(
        tenant_auth, published, clean_tasks, monkeypatch, caplog):
    """A failing external agent must not reflect its own credential in the
    tenant-visible result. The (admin-configured) key is sent in the
    Authorization header; a backend that echoes it in its error body used to
    land in str(e) — the task result (GET /api/tasks/{id}) and the server log."""
    from agent_civilization.agents.external_adapter import ExternalAgentAdapter
    secret = "r149-external-key-9d2e5a"

    class _EchoKey(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            body = json.dumps(
                {"error": f"unauthorized {self.headers.get('Authorization', '')}"}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _EchoKey)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(server, "EXTERNAL_AGENTS", [
        ExternalAgentAdapter("r149-echo", f"http://127.0.0.1:{srv.server_port}/v1/chat",
                             api_key=secret, allow_private=True)])
    try:
        with TestClient(server.app) as c:
            r = c.post("/api/tasks", json={"specialization": "external", "prompt": "hi"},
                       headers=tenant_auth.headers)
            assert r.status_code == 202
            tid = r.json()["task_id"]
            # wait for the full end: final status AND the task_completed
            # event for this id (the status is set before the trailing
            # storage write and publish)
            deadline = time.time() + 10.0
            while time.time() < deadline:
                if (server.TASKS[tid]["status"] in ("completed", "failed")
                        and any(e["type"] == "task_completed" and e.get("task_id") == tid
                                for _, e in published)):
                    break
                time.sleep(0.05)
            task_text = c.get(f"/api/tasks/{tid}", headers=tenant_auth.headers).text
    finally:
        srv.shutdown()
    assert server.TASKS[tid]["status"] == "failed"
    assert secret not in task_text          # never tenant-visible
    assert secret not in caplog.text         # never in the server log


def test_rejected_storage_body_must_not_log_the_storage_key(
        tenant_auth, postgrest, agent_outcome, caplog):
    """The layer used to log a rejected write's backend body verbatim
    (RISK-013). If that body echoed the storage service key, the key landed
    in the server logs. The diagnosis keeps the operation and the status —
    the credential never enters the log."""
    agent_outcome(True)
    postgrest.policy = {("POST", "tasks"): 401, ("PATCH", "tasks"): 401,
                        ("POST", "agent_stats"): 401, ("POST", "events"): 401}
    postgrest.error_body = f"invalid apikey: {ss.SUPABASE_KEY}"
    with TestClient(server.app) as c:
        r = _chat(c, tenant_auth.headers)
    assert r.status_code == 200
    assert ss.SUPABASE_KEY not in r.text            # response stays clean
    assert ss.SUPABASE_KEY not in caplog.text       # and so does the log
    assert "insert_task rejected: HTTP 401" in caplog.text   # the diagnosis stays


# ------------------------------------------------------------------ G2: failed stored read

def _seed_row(task_id, tenant):
    _R149PostgREST.rows["tasks"].append({
        "task_id": task_id, "specialization": SPEC, "prompt": "stored",
        "status": "completed", "tenant_id": tenant, "created_at": "2026-10-16T00:00:01"})


@pytest.mark.parametrize("verdict", [500, "cut", "partial"])
def test_failed_stored_read_is_404_and_every_attempt_stays_scoped(
        tenant_auth, postgrest, clean_tasks, verdict):
    """A task absent from memory, whose stored read FAILS (status error,
    transport cut before the response, partial response cut mid-read), answers
    like a missing one (the current degraded contract) — and the failure never
    widens the scope: every attempt carries the caller's tenant, no unscoped
    fallback read is ever issued."""
    tid = "r149stored01"
    _seed_row(tid, tenant_auth.tenant)
    postgrest.policy = {("GET", "tasks"): verdict}
    postgrest.error_body = f"read failed: {MARKER}"
    with TestClient(server.app) as c:
        r = c.get(f"/api/tasks/{tid}", headers=tenant_auth.headers)
        assert r.status_code == 404 and r.json() == {"detail": "task not found"}
        assert MARKER not in r.text
    attempts = _writes("GET", "tasks")
    assert attempts, "the stored read must be attempted"
    for _, _, query, _ in attempts:
        assert query.get("task_id") == [f"eq.{tid}"]
        assert query.get("tenant_id") == [f"eq.{tenant_auth.tenant}"]
    # no attempt without the tenant filter — no unscoped fallback
    assert all("tenant_id" in q for _, _, q, _ in _writes("GET", "tasks"))


def test_failed_stored_read_for_another_tenant_never_leaks_the_row(
        tenant_auth, postgrest, clean_tasks):
    tid = "r149stored02"
    _seed_row(tid, "some_other_tenant")
    postgrest.policy = {("GET", "tasks"): 500}
    postgrest.error_body = f"read failed: {MARKER}"
    with TestClient(server.app) as c:
        r = c.get(f"/api/tasks/{tid}", headers=tenant_auth.headers)
        assert r.status_code == 404 and MARKER not in r.text
    for _, _, query, _ in _writes("GET", "tasks"):
        assert query.get("tenant_id") == [f"eq.{tenant_auth.tenant}"]


def test_control_read_success_returns_the_scoped_row(tenant_auth, postgrest, clean_tasks):
    """Control: with the read accepted, the same route returns the stored row
    — the 404s above are the failure, not a broken route."""
    tid = "r149stored03"
    _seed_row(tid, tenant_auth.tenant)
    with TestClient(server.app) as c:
        r = c.get(f"/api/tasks/{tid}", headers=tenant_auth.headers)
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == tid and body["source"] == "database"
    assert body["tenant_id"] == tenant_auth.tenant
