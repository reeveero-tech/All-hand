"""R-163 (D-082): durable task queue — state machine, fenced storage writes,
dispatcher/reaper, idempotency, cancellation, recovery and the SEC-06
fail-closed property of the durable path.

Everything runs against the real ``SupabaseStorage`` and the real FastAPI app.
The PostgREST backend is a minimal local fake (threaded HTTP) that implements
exactly the semantics the queue relies on: ``eq./in./lt.`` filters,
``Prefer: return=representation``, and the unique ``(tenant_id,
idempotency_key)`` index (409 on a duplicate insert).

Honest limits: this file proves the queue logic against the FAKE backend
(the storage CI job runs the real-PostgREST live suite — see
``test_r163_live_queue.py``); the restart test uses a real server subprocess
but no real Supabase (documented in EVIDENCE).
"""
from __future__ import annotations

import asyncio
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
from agent_civilization.core import task_queue
from agent_civilization.core.tenancy import Principal


# --------------------------------------------------------------- fake backend

class _PostgREST(BaseHTTPRequestHandler):
    """Just enough PostgREST for the R-163 queue operations."""

    rows: dict = {"tasks": [], "events": [], "agent_stats": []}
    gets: list = []
    mode: list = ["ok"]          # "ok" | "reject" (store down) | "reject_insert"
                                 # | "reject_insert_hold" (task insert blocks on
                                 # `gate`, then fails — see below)
    gate: list = [None]           # a threading.Event the held inserts wait on
    gate_waiting: list = [0]      # how many inserts are currently held

    def log_message(self, *a):
        pass

    # -- filter semantics --------------------------------------------------
    @staticmethod
    def _keep(row: dict, col: str, val: str) -> bool:
        if val.startswith("eq."):
            return str(row.get(col)) == val[3:]
        if val.startswith("not.in.("):          # R-164 fairness scan
            return str(row.get(col)) not in val[8:-1].split(",")
        if val.startswith("in.("):
            allowed = val[4:-1].split(",")
            return str(row.get(col)) in allowed
        if val.startswith("lt."):
            got = row.get(col)
            if got is None:
                return False
            return str(got) < val[3:]     # ISO strings compare chronologically
        return True

    def _table_q(self):
        u = urllib.parse.urlsplit(self.path)
        return u.path.rsplit("/", 1)[-1], {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}

    def _matched(self, table, q):
        out = self.rows[table]
        for col, val in q.items():
            if col in ("order", "limit", "select"):
                continue
            out = [r for r in out if self._keep(r, col, val)]
        return list(out)

    def _send(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- verbs ---------------------------------------------------------------
    def do_POST(self):  # noqa: N802
        table, _ = self._table_q()
        row = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        # "reject_insert" (R-164 review): reads answer, task inserts fail
        if self.mode[0] == "reject" or (self.mode[0] == "reject_insert" and table == "tasks"):
            self._send(500)
            return
        # "reject_insert_hold" (log 299): the task insert BLOCKS on the gate —
        # a request that is genuinely IN FLIGHT (holding its accept slot)
        # while the test asserts the rate refusals, and only then fails and
        # gives the slot back. An instant 500 cannot prove the hold: the
        # release raced the next reserve once the client rate limiter
        # became async (shared-store to_thread yield before the reserve).
        if self.mode[0] == "reject_insert_hold" and table == "tasks":
            self.gate_waiting[0] += 1
            self.gate[0].wait(timeout=30)
            self._send(500)
            return
        idem = row.get("idempotency_key")
        if idem is not None and any(
                r.get("idempotency_key") == idem and r.get("tenant_id") == row.get("tenant_id")
                for r in self.rows["tasks"]):
            self._send(409)     # the unique (tenant_id, idempotency_key) index
            return
        row.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "+00:00")
        row.setdefault("attempts", 0)
        row.setdefault("max_attempts", 3)
        self.rows[table].append(row)
        self._send(201)

    def do_PATCH(self):  # noqa: N802
        table, q = self._table_q()
        if self.mode[0] == "reject":
            self._send(500)
            return
        # PostgREST 12 (PGRST109): restricting an UPDATE with `limit` requires
        # an explicit `order` on a unique column — emulate it so the unit suite
        # catches this class of mistake without a live PostgREST.
        if "limit" in q and not q.get("order", "").startswith("task_id"):
            self._send(400, {"code": "PGRST109", "message":
                             "limit on PATCH requires an order on a unique column"})
            return
        patch = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        matched = self._matched(table, q)
        for r in matched:
            r.update(patch)
        if "return=representation" in self.headers.get("Prefer", ""):
            self._send(200, matched)   # PostgREST: updated rows, [] if none
        else:
            self._send(204)

    def do_GET(self):  # noqa: N802
        table, q = self._table_q()
        self.gets.append((table, dict(q)))
        if self.mode[0] == "reject":
            self._send(500)
            return
        out = self._matched(table, q)
        if q.get("order"):
            col, _, direction = q["order"].partition(".")
            out.sort(key=lambda r: str(r.get(col) or ""), reverse=direction == "desc")
        if "limit" in q:
            out = out[: int(q["limit"])]
        self._send(200, out)


@pytest.fixture
def db(monkeypatch):
    _PostgREST.rows = {"tasks": [], "events": [], "agent_stats": []}
    _PostgREST.gets = []
    _PostgREST.mode[0] = "ok"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _PostgREST)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    from agent_civilization.storage import supabase_store as ss
    monkeypatch.setattr(ss, "SUPABASE_URL", f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.setattr(ss, "SUPABASE_KEY", "r163-test-not-a-real-key")
    monkeypatch.setattr(ss, "_storage", None)
    yield _PostgREST.rows
    httpd.shutdown()
    monkeypatch.setattr(ss, "_storage", None)


@pytest.fixture
def tenants(tenant_auth, monkeypatch):
    """test_tenant (A, user), other_tenant (B, user)."""
    key_b = "r163-" + secrets.token_urlsafe(16)
    creds = dict(server.CREDENTIALS)
    creds[hashlib.sha256(key_b.encode()).hexdigest()] = Principal("other_tenant", "user")
    monkeypatch.setattr(server, "CREDENTIALS", creds)
    tenant_auth.headers_b = {"X-API-Key": key_b}
    return tenant_auth


@pytest.fixture(autouse=True)
def _clean_tasks():
    saved_tasks, saved_state, saved_runs = dict(server.TASKS), dict(server._QUEUE_STATE), dict(server._RUNNING_RUNS)
    server.TASKS.clear()
    server._QUEUE_STATE.clear()
    server._RUNNING_RUNS.clear()
    server._VOLATILE_IDEMPOTENT.clear()
    server._QUEUE_BACKOFF.clear()
    yield
    server._stop_queue_loops()
    server.TASKS.clear()
    server._QUEUE_STATE.clear()
    server._RUNNING_RUNS.clear()
    server._VOLATILE_IDEMPOTENT.clear()
    server._QUEUE_BACKOFF.clear()
    server.TASKS.update(saved_tasks)
    server._QUEUE_STATE.update(saved_state)
    server._RUNNING_RUNS.update(saved_runs)


def _row(task_id, tenant, status="queued", **extra):
    row = {"task_id": task_id, "specialization": "analyst",
           "prompt": "R-163: summarise 1 2 3", "status": status, "tenant_id": tenant,
           "result": None, "agent_name": None, "elapsed_s": None,
           "created_at": "2026-10-18T10:00:00+00:00", "finished_at": None,
           "attempts": 0, "max_attempts": 3, "claim_token": None,
           "lease_expires_at": None, "idempotency_key": None, "payload": None}
    row.update(extra)
    return row


def _wait_for(predicate, timeout=15.0, step=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


def _client():
    return TestClient(server.app)


# ------------------------------------------------------- state machine units

def test_r163_valid_transitions():
    assert task_queue.transition("queued", "claimed") == task_queue.TaskState.CLAIMED
    assert task_queue.transition("claimed", "running") == task_queue.TaskState.RUNNING
    assert task_queue.transition("running", "completed") == task_queue.TaskState.COMPLETED
    assert task_queue.transition("running", "retrying") == task_queue.TaskState.RETRYING
    assert task_queue.transition("retrying", "queued") == task_queue.TaskState.QUEUED
    assert task_queue.transition("retrying", "dead_letter") == task_queue.TaskState.DEAD_LETTER
    assert task_queue.transition("accepted", "queued") == task_queue.TaskState.QUEUED
    assert task_queue.transition("queued", "waiting") == task_queue.TaskState.WAITING
    assert task_queue.transition("waiting", "queued") == task_queue.TaskState.QUEUED
    assert task_queue.transition("running", "cancelled") == task_queue.TaskState.CANCELLED


@pytest.mark.parametrize("a,b", [
    ("queued", "completed"), ("queued", "running"), ("completed", "queued"),
    ("failed", "queued"), ("cancelled", "queued"), ("dead_letter", "queued"),
    ("claimed", "completed"), ("running", "claimed"), ("retrying", "completed"),
])
def test_r163_invalid_transitions_rejected(a, b):
    with pytest.raises(task_queue.InvalidTransition):
        task_queue.transition(a, b)


def test_r163_terminal_states_are_final():
    for t in ("completed", "failed", "cancelled", "dead_letter"):
        assert task_queue.is_terminal(t)
        assert not task_queue.ALLOWED[task_queue.TaskState(t)]


def test_r163_public_status_mapping():
    for internal, public in [("accepted", "queued"), ("queued", "queued"), ("waiting", "queued"),
                             ("retrying", "queued"), ("claimed", "queued"), ("running", "running"),
                             ("completed", "completed"), ("failed", "failed"),
                             ("cancelled", "failed"), ("dead_letter", "failed")]:
        assert task_queue.public_status(internal) == public


def test_r163_outcome_after_expiry():
    assert task_queue.outcome_after_expiry(1, 3) is task_queue.TaskState.RETRYING
    assert task_queue.outcome_after_expiry(3, 3) is task_queue.TaskState.DEAD_LETTER


def test_r163_unknown_state_raises():
    with pytest.raises(ValueError):
        task_queue.state("exploded")


def test_r163_cancelled_and_dead_letter_outcomes_are_fixed():
    cancelled = task_queue.cancelled_outcome("analyst_007", "analyst")
    assert cancelled["ok"] is False and cancelled["result"]["error"] == "cancelled"
    dead = task_queue.dead_letter_outcome("analyst_007", "analyst", 3, 3)
    assert dead["ok"] is False and "dead-letter" in dead["result"]["error"]


# ------------------------------------------------ storage semantics (the fake)

async def _storage():
    from agent_civilization.storage import get_storage
    return get_storage()


@pytest.mark.asyncio
async def test_r163_claim_is_atomic_and_fenced(db):
    s = await _storage()
    db["tasks"].append(_row("fence1", "t1", status="queued"))
    row = await s.claim_task("fence1", "token-a", "2026-10-18T10:01:00+00:00")
    assert row is not None and row["claim_token"] == "token-a" and row["status"] == "claimed"
    # a second claim of the same row must not win (it is not queued anymore)
    assert await s.claim_task("fence1", "token-b", "2026-10-18T10:01:00+00:00") is None
    # the stale owner's writes match 0 rows and are discarded
    assert await s.start_task("fence1", "token-b", 1) is False
    assert db["tasks"][0]["claim_token"] == "token-a"


@pytest.mark.asyncio
async def test_r163_start_counts_attempt_and_finish_is_fenced(db):
    s = await _storage()
    db["tasks"].append(_row("run1", "t1", status="queued", attempts=0))
    await s.claim_task("run1", "tok", "2026-10-18T10:01:00+00:00")
    assert await s.start_task("run1", "tok", 1) is True
    assert db["tasks"][0]["status"] == "running" and db["tasks"][0]["attempts"] == 1
    assert await s.finish_task("run1", "tok", "completed", agent_name="analyst_007",
                              result={"ok": True}, elapsed_s=0.1) is True
    assert db["tasks"][0]["status"] == "completed"
    # a stale worker's late result is discarded
    assert await s.finish_task("run1", "tok", "failed", result={"ok": False}) is False
    assert db["tasks"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_r163_finish_task_writes_finished_at(db):
    # R-042's live-storage CI run caught a real parity gap (PR #261 storage
    # job): the volatile path's update_task writes finished_at="now()" on a
    # terminal status, but the durable fenced path's finish_task didn't —
    # the row completed with finished_at NULL and the task lifecycle test
    # (test_task_lifecycle_persisted) failed on live Postgres/PostgREST.
    # Parity here is a storage-write requirement, not an API nicety: the
    # task list reads the column.
    s = await _storage()
    db["tasks"].append(_row("fin1", "t1", status="queued"))
    await s.claim_task("fin1", "tok", "2026-10-18T10:01:00+00:00")
    assert await s.finish_task("fin1", "tok", "completed",
                              agent_name="analyst_007", result={"ok": True}) is True
    assert db["tasks"][0]["status"] == "completed"
    assert db["tasks"][0].get("finished_at") is not None


@pytest.mark.asyncio
async def test_r163_release_gives_claim_back_without_attempt(db):
    s = await _storage()
    db["tasks"].append(_row("rel1", "t1", status="queued", attempts=0))
    await s.claim_task("rel1", "tok", "2026-10-18T10:01:00+00:00")
    assert await s.release_task("rel1", "tok", "queued") is True
    assert db["tasks"][0]["status"] == "queued" and db["tasks"][0]["attempts"] == 0


@pytest.mark.asyncio
async def test_r163_recovery_never_steals_a_live_lease(db):
    s = await _storage()
    db["tasks"].append(_row("live1", "t1", status="running", attempts=1,
                            claim_token="tok", lease_expires_at="2099-01-01T00:00:00+00:00"))
    db["tasks"].append(_row("dead1", "t1", status="running", attempts=1,
                            claim_token="tok2", lease_expires_at="2020-01-01T00:00:00+00:00"))
    expired = await s.list_expired_lease_tasks()
    assert [r["task_id"] for r in expired] == ["dead1"]
    assert await s.transition_task("dead1", ("claimed", "running"), "retrying",
                                   lease_expired_before="2099-01-01T00:00:00+00:00") is True
    assert await s.transition_task("dead1", ("retrying",), "queued") is True
    # the live lease was never touched
    assert db["tasks"][0]["status"] == "running" and db["tasks"][0]["claim_token"] == "tok"


@pytest.mark.asyncio
async def test_r163_idempotency_lookup_is_tenant_scoped(db):
    s = await _storage()
    db["tasks"].append(_row("idem1", "tenant-a", status="completed", idempotency_key="k1"))
    found = await s.find_task_by_idempotency_key("k1", scope="tenant-a")
    assert found is not None and found["task_id"] == "idem1"
    # another tenant's key is invisible — like a missing one
    assert await s.find_task_by_idempotency_key("k1", scope="tenant-b") is None


@pytest.mark.asyncio
async def test_r163_unique_index_rejects_duplicate_key(db):
    s = await _storage()
    ok = await s.insert_task("idem1", "analyst", "p", "queued", tenant_id="t1", idempotency_key="k1")
    assert ok is True
    ok2 = await s.insert_task("idem2", "analyst", "p", "queued", tenant_id="t1", idempotency_key="k1")
    assert ok2 is False     # 409: the unique (tenant, key) index


# --------------------------------------------------- server: durable mode

def _enable_queue(monkeypatch):
    monkeypatch.setenv("AGOS_TASK_QUEUE", "1")


def test_r163_durable_task_runs_and_completes(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "analyst",
                                       "prompt": "R-163: summarise 1 2 3"},
                   headers=tenants.headers)
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "queued" and "duplicate" not in body
        tid = body["task_id"]
        assert _wait_for(lambda: any(
            x["task_id"] == tid and x["status"] == "completed" for x in db["tasks"]))
        row = next(x for x in db["tasks"] if x["task_id"] == tid)
        assert row["attempts"] == 1 and row["claim_token"] and row["result"] is not None
        mem = c.get(f"/api/tasks/{tid}", headers=tenants.headers).json()
        assert mem["status"] in ("completed", "failed")
        # the memory record and the row agree (no contradictory transitions)
        assert mem["status"] == row["status"]


def test_r163_duplicate_key_replays_the_same_task(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    with _client() as c:
        r1 = c.post("/api/tasks", json={"specialization": "analyst",
                                        "prompt": "R-163: summarise 4 5 6",
                                        "idempotency_key": "order-42"},
                    headers=tenants.headers)
        assert r1.status_code == 202
        tid = r1.json()["task_id"]
        assert _wait_for(lambda: len(db["tasks"]) == 1 and db["tasks"][0]["status"] == "completed")
        # duplicate delivery: the SAME task, no second run
        r2 = c.post("/api/tasks", json={"specialization": "analyst",
                                        "prompt": "R-163: summarise 4 5 6",
                                        "idempotency_key": "order-42"},
                    headers=tenants.headers)
        assert r2.status_code == 202
        assert r2.json()["task_id"] == tid and r2.json()["duplicate"] is True
        assert len([x for x in db["tasks"] if x.get("idempotency_key") == "order-42"]) == 1


def test_r163_volatile_idempotent_replay(tenants, monkeypatch):
    monkeypatch.delenv("AGOS_TASK_QUEUE", raising=False)
    with _client() as c:
        r1 = c.post("/api/tasks", json={"specialization": "analyst",
                                        "prompt": "R-163 volatile idem",
                                        "idempotency_key": "v-1"}, headers=tenants.headers)
        assert r1.status_code == 202
        tid = r1.json()["task_id"]
        r2 = c.post("/api/tasks", json={"specialization": "analyst",
                                        "prompt": "R-163 volatile idem",
                                        "idempotency_key": "v-1"}, headers=tenants.headers)
        assert r2.json()["task_id"] == tid and r2.json()["duplicate"] is True
        # the key of another tenant is a different namespace
        r3 = c.post("/api/tasks", json={"specialization": "analyst",
                                        "prompt": "R-163 volatile idem",
                                        "idempotency_key": "v-1"}, headers=tenants.headers_b)
        assert r3.json()["task_id"] != tid


def test_r163_cancel_queued_task(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "analyst",
                                       "prompt": "R-163 cancel me"}, headers=tenants.headers)
        tid = r.json()["task_id"]
        # cancel while queued (race with the dispatcher is fine: both paths end cancelled)
        rc = c.post(f"/api/tasks/{tid}/cancel", headers=tenants.headers)
        assert rc.status_code == 200, rc.text
        assert rc.json()["cancelled"] is True
        mem = c.get(f"/api/tasks/{tid}", headers=tenants.headers).json()
        assert mem["status"] == "failed"
        assert mem["result"]["result"]["error"] == "cancelled"
        # a terminal task refuses a second cancel
        assert c.post(f"/api/tasks/{tid}/cancel", headers=tenants.headers).status_code == 409


def test_r163_cancel_cross_tenant_is_404(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "analyst",
                                       "prompt": "R-163 other tenant"},
                   headers=tenants.headers_b)
        tid = r.json()["task_id"]
        assert c.post(f"/api/tasks/{tid}/cancel", headers=tenants.headers).status_code == 404


def test_r163_store_down_create_is_refused_not_lost(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    _PostgREST.mode[0] = "reject"
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "analyst",
                                       "prompt": "R-163 store down"},
                   headers=tenants.headers)
        assert r.status_code == 503
        assert "not accepted" in r.json()["detail"]
        # no task_id was handed out and nothing was queued
        assert db["tasks"] == []


def test_r163_internal_states_are_served_through_the_public_mapping(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    # a row mid-queue (claimed by a worker that then died) must not leak the
    # internal vocabulary to the API
    db["tasks"].append(_row("claimed1", "test_tenant", status="claimed"))
    with _client() as c:
        mem = c.get("/api/tasks/claimed1", headers=tenants.headers).json()
        assert mem["status"] == "queued"
        listed = c.get("/api/tasks", headers=tenants.headers).json()
        row = next(x for x in listed["tasks"] if x["task_id"] == "claimed1")
        assert row["status"] == "queued"


def test_r163_reaper_recovers_expired_lease_and_requeues(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    monkeypatch.setattr(server, "TASK_REAP_S", 0.2)
    monkeypatch.setattr(server, "TASK_LEASE_S", 2.0)
    # a task whose worker died mid-run: leased, deadline long past
    db["tasks"].append(_row("orphan1", "test_tenant", status="running", attempts=1,
                           claim_token="dead-worker", lease_expires_at="2020-01-01T00:00:00+00:00",
                           payload={"n": [1, 2, 3]}))
    with _client():
        assert _wait_for(lambda: next(
            (x["status"] == "completed" for x in db["tasks"] if x["task_id"] == "orphan1"), False))
        row = next(x for x in db["tasks"] if x["task_id"] == "orphan1")
        # recovered (retrying -> queued) and re-executed by the dispatcher
        assert row["attempts"] == 2 and row["result"] is not None


def test_r163_dead_letter_after_max_attempts(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    monkeypatch.setattr(server, "TASK_REAP_S", 0.2)
    # attempts already at the budget: recovery ends it in dead_letter
    db["tasks"].append(_row("dl1", "test_tenant", status="running", attempts=3,
                            claim_token="dead-worker", lease_expires_at="2020-01-01T00:00:00+00:00"))
    with _client() as c:
        assert _wait_for(lambda: next(
            (x["status"] == "dead_letter" for x in db["tasks"] if x["task_id"] == "dl1"), False))
        mem = c.get("/api/tasks/dl1", headers=tenants.headers).json()
        assert mem["status"] == "failed"
        assert "dead-letter" in mem["result"]["result"]["error"]


def test_r163_full_prompt_survives_the_500_char_storage_bound(tenants, db, monkeypatch):
    """insert_task stores only prompt[:500]; the durable row is the execution
    source — so the full prompt travels inside the payload (_agos_prompt) and
    the dispatcher executes on it, never on the truncated column."""
    _enable_queue(monkeypatch)
    seen = {}

    class _StubAgent:
        name = "analyst_996"
        stats = {"tasks_completed": 1, "tasks_failed": 0, "total_time_s": 0.1}

        async def execute(self, payload):
            seen["prompt"] = payload["prompt"]
            return {"ok": True, "agent": self.name, "specialization": "analyst",
                    "elapsed_s": 0.01, "result": {"summary": "stub"}}

    monkeypatch.setattr(server, "_pick_agent", lambda specialization: _StubAgent())
    long_prompt = "R-163: " + "x" * 1200          # far above the 500-char column bound
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "analyst",
                                       "prompt": long_prompt, "data": {"k": 1}},
                   headers=tenants.headers)
        assert r.status_code == 202
        tid = r.json()["task_id"]
        assert _wait_for(lambda: any(
            x["task_id"] == tid and x["status"] == "completed" for x in db["tasks"]))
        # the agent executed on the FULL prompt, not the truncated column
        assert seen["prompt"] == long_prompt and len(seen["prompt"]) == 1207
        row = next(x for x in db["tasks"] if x["task_id"] == tid)
        # the column really is truncated (the bound the wrapping works around)
        assert len(row["prompt"]) == 500
        # the data round-trips unchanged alongside the reserved prompt key
        assert row["payload"]["data"] == {"k": 1}


def test_r163_cancel_with_store_down_answers_503_not_409(tenants, db, monkeypatch):
    """A failed cancel WRITE is not a finish: the endpoint must answer 503
    (cancel not confirmed) instead of a false 409 'already finished'."""
    _enable_queue(monkeypatch)
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "analyst",
                                       "prompt": "R-163: 503 probe"},
                   headers=tenants.headers)
        assert r.status_code == 202
        tid = r.json()["task_id"]
        _PostgREST.mode[0] = "reject"           # the store goes down
        try:
            rc = c.post(f"/api/tasks/{tid}/cancel", headers=tenants.headers)
            assert rc.status_code == 503
            assert "not confirmed" in rc.json()["detail"]
            # nothing was cancelled: the record is still pending
            mem = c.get(f"/api/tasks/{tid}", headers=tenants.headers).json()
            assert mem["status"] in ("queued", "running")
        finally:
            _PostgREST.mode[0] = "ok"


def test_r163_recovery_transitions_are_direct_no_retrying_strand(tenants, db, monkeypatch):
    """The reaper recovers an expired lease in ONE conditional transition
    (claimed/running -> queued|dead_letter directly): a crash between two
    writes can never strand a row in `retrying` — the machine allows the
    direct paths and the reaper uses them."""
    _enable_queue(monkeypatch)
    # the state machine allows the direct recovery transitions
    assert task_queue.transition("claimed", "queued") == task_queue.TaskState.QUEUED
    assert task_queue.transition("running", "queued") == task_queue.TaskState.QUEUED
    assert task_queue.transition("claimed", "dead_letter") == task_queue.TaskState.DEAD_LETTER
    assert task_queue.transition("running", "dead_letter") == task_queue.TaskState.DEAD_LETTER
    # end-to-end: a claimed row whose lease expired in the past (a dead
    # worker) is recovered straight to queued — retrying is never observed
    from datetime import datetime, timedelta, timezone
    past = (datetime.now(timezone.utc) - timedelta(seconds=3600)).isoformat(timespec="seconds")
    db["tasks"].append({"task_id": "r163-strand", "specialization": "analyst",
                        "prompt": "strand probe", "status": "claimed",
                        "claim_token": "dead-token", "lease_expires_at": past,
                        "attempts": 1, "max_attempts": 3, "tenant_id": tenants.tenant,
                        "created_at": past, "result": None})
    observed = set()
    with _client():  # the lifespan starts the reaper/dispatcher loops
        assert _wait_for(lambda: (observed.add(next(
            x["status"] for x in db["tasks"] if x["task_id"] == "r163-strand")) or
            next(x["status"] for x in db["tasks"] if x["task_id"] == "r163-strand") != "claimed"))
    assert "retrying" not in observed
    row = next(x for x in db["tasks"] if x["task_id"] == "r163-strand")
    # recovered — possibly already re-claimed by the dispatcher, never stranded
    assert row["status"] in ("queued", "claimed", "running", "completed", "failed")


def test_r163_durable_run_carries_no_public_llm_consent(tenants, db, monkeypatch):
    """SEC-06 fail-closed on the durable path: the dispatcher's context has no
    request consent, so a run needing the public LLM can never send text."""
    _enable_queue(monkeypatch)
    from agent_civilization.core import llm
    seen = {}

    class _StubAgent:
        name = "analyst_999"
        stats = {"tasks_completed": 1, "tasks_failed": 0, "total_time_s": 0.1}

        async def execute(self, payload):
            seen["consent_during_run"] = llm.public_llm_consent()
            return {"ok": True, "agent": self.name, "specialization": "analyst",
                    "elapsed_s": 0.01, "result": {"summary": "stub"}}

    monkeypatch.setattr(server, "_pick_agent", lambda specialization: _StubAgent())
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "analyst",
                                       "prompt": "R-163 consent probe",
                                       "allow_public_llm": True},   # consent on the REQUEST
                   headers=tenants.headers)
        r.json()["task_id"]
        assert _wait_for(lambda: "consent_during_run" in seen)
        assert seen["consent_during_run"] is False    # not carried into the run


def test_r163_late_result_cannot_resurrect_a_cancelled_task(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    # deterministic mid-run cancellation: a stub agent that hangs until cancelled
    class _SlowAgent:
        name = "analyst_998"
        stats = {"tasks_completed": 0, "tasks_failed": 0, "total_time_s": 0}

        async def execute(self, payload):
            await asyncio.sleep(30)   # cancelled long before this ends
            return {"ok": True, "agent": self.name, "specialization": "analyst",
                    "elapsed_s": 0, "result": {"summary": "too late"}}

    monkeypatch.setattr(server, "_pick_agent", lambda specialization: _SlowAgent())
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "analyst",
                                       "prompt": "R-163 late result"}, headers=tenants.headers)
        tid = r.json()["task_id"]
        # the run hangs: the claim is stable and observable
        assert _wait_for(lambda: server._QUEUE_STATE.get(tid, {}).get("state") == "running")
        server._QUEUE_STATE[tid]["claim_token"]
        rc = c.post(f"/api/tasks/{tid}/cancel", headers=tenants.headers)
        assert rc.status_code == 200
        assert _wait_for(lambda: tid not in server._RUNNING_RUNS)
        row = next(x for x in db["tasks"] if x["task_id"] == tid)
        assert row["status"] == "cancelled"   # terminal: no late result revives it
        mem = c.get(f"/api/tasks/{tid}", headers=tenants.headers).json()
        assert mem["status"] == "failed"
        assert mem["result"]["result"]["error"] == "cancelled"
        # the fenced late-result write is proven separately at the storage
        # layer (test_r163_late_finish_write_is_fenced_out)


def test_r163_volatile_cancel_mid_run_keeps_the_cancelled_outcome(tenants, monkeypatch):
    monkeypatch.delenv("AGOS_TASK_QUEUE", raising=False)
    import asyncio

    class _SlowAgent:
        name = "analyst_997"
        stats = {"tasks_completed": 0, "tasks_failed": 0, "total_time_s": 0}

        async def execute(self, payload):
            await asyncio.sleep(30)
            return {"ok": True, "agent": self.name, "specialization": "analyst",
                    "elapsed_s": 0, "result": {"summary": "too late"}}

    monkeypatch.setattr(server, "_pick_agent", lambda specialization: _SlowAgent())
    with _client() as c:
        r = c.post("/api/tasks", json={"specialization": "analyst",
                                       "prompt": "R-163 volatile cancel"}, headers=tenants.headers)
        tid = r.json()["task_id"]
        assert _wait_for(lambda: server.TASKS.get(tid, {}).get("status") == "running")
        rc = c.post(f"/api/tasks/{tid}/cancel", headers=tenants.headers)
        assert rc.status_code == 200
        assert _wait_for(lambda: server.TASKS.get(tid, {}).get("status") == "failed")
        mem = c.get(f"/api/tasks/{tid}", headers=tenants.headers).json()
        # the cancelled outcome set by the endpoint is what the record keeps —
        # the cancelled run must not overwrite it with a generic failure
        assert mem["result"]["result"]["error"] == "cancelled"


@pytest.mark.asyncio
async def test_r163_late_finish_write_is_fenced_out(db):
    s = await _storage()
    db["tasks"].append(_row("fenced1", "t1", status="cancelled", claim_token="old",
                            attempts=1, finished_at="2026-10-18T10:00:02+00:00"))
    # a late worker holding the OLD token cannot end a cancelled task
    assert await s.finish_task("fenced1", "old", "completed",
                                result={"ok": True}) is False
    assert db["tasks"][0]["status"] == "cancelled"
