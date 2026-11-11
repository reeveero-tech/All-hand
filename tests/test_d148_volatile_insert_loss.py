"""D-148 (لاحق 146): the VOLATILE accept path no longer hides a failed
``storage.insert_task`` — and a task whose row was never created no longer
keeps the terminal-write drainer retrying FOREVER.

The defect (log 401 §5, recorded open and NOT claimed to be the proven
cause of the r188 CI flake): ``insert_task`` fails SOFT (False on any
transport / HTTP / breaker failure) and the volatile ``create_task``
ignored its result. The task was answered 202 like any other, ran in
memory, and its terminal ``update_task`` matched NO row ("no row
updated") — the in-line retry budget was spent, the write was DEFERRED,
and the drainer retried a write that could never land, forever.

The contract pinned here:

1. normal path unchanged — no ``persisted`` field, one row, completed;
2. a failed insert is VISIBLE: 202 body and task record carry
   ``"persisted": false``, ERROR log, ``task_row_missing`` counter;
3. RECOVERY: the terminal write re-creates the row (same task_id) once
   storage answers — exactly one row, ``persisted`` flips to true;
4. an AMBIGUOUS insert (failed answer, row actually stored) is updated,
   never duplicated;
5. a LATE original insert racing the re-create never yields two rows (the
   primary key — emulated here — rejects the loser);
6. BOUNDED: a row that cannot be re-created is abandoned after
   ``TASK_MISSING_ROW_DRAIN_ROUNDS`` drain rounds (CRITICAL,
   ``task_row_abandoned``) — the drainer ends; the memory record keeps the
   result and ``persisted: false``. (Red on the pre-fix code: the entry
   stays pending forever.)
7. CONCURRENCY: 30 concurrent tasks with every other insert failing all
   converge to exactly one completed row each;
8. IDEMPOTENCY: a volatile replay of a task whose insert failed returns
   the same task and creates no second row;
9. CANCEL of a task whose row was never created is confirmed (not a false
   409 "already finished") and the cancelled row is re-created;
10. no storage configured: nothing is marked missing (nothing to persist);
11. an EXISTING row's deferral keeps the unchanged R-188 contract (no
    round bound — an outage is not a missing row).

Honest limits: the backend is the in-repo fake PostgREST (plus a primary
key on ``task_id`` emulated in this file) and an in-process double — not a
live Supabase. Nothing here proves or disproves the r188 flake's cause.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
import json
from http.server import ThreadingHTTPServer

import pytest

import server
from tests.test_r163_durable_queue import _client, _wait_for

sys.path.insert(0, "tests")
import test_r163_durable_queue as r163  # noqa: E402

tenants = r163.tenants          # re-export: the fixture requests


# ----------------------------------------------------------------- fixtures

@pytest.fixture(autouse=True)
async def _clean_state(monkeypatch):
    server.TERMINAL_WRITE_PENDING.clear()
    getattr(server, "_ROW_MISSING", {}).clear()   # absent on the pre-fix code (red run)
    yield
    task = server._TERMINAL_WRITE_DRAIN_TASK
    if task is not None and not task.done():
        task.cancel()
        if task.get_loop() is asyncio.get_running_loop() and not task.get_loop().is_closed():
            try:
                await task
            except asyncio.CancelledError:
                pass
    server.TERMINAL_WRITE_PENDING.clear()
    getattr(server, "_ROW_MISSING", {}).clear()
    server._TERMINAL_WRITE_DRAIN_TASK = None


@pytest.fixture
def fast(monkeypatch):
    """Shrink the sleeps — the SHAPE of the budget is under test."""
    monkeypatch.setattr(server, "TASK_TERMINAL_WRITE_MAX_WAIT_S", 0.01)
    monkeypatch.setattr(server, "TASK_TERMINAL_WRITE_ATTEMPTS", 2)
    monkeypatch.setattr(server, "TERMINAL_WRITE_DRAIN_INTERVAL_S", 0.02)
    monkeypatch.setattr(server, "TASK_MISSING_ROW_DRAIN_ROUNDS", 3, raising=False)


class _PK(r163._PostgREST):
    """The r163 fake PostgREST + the real schema's ``task_id`` PRIMARY KEY
    (docs/sql/001_init.sql) and a failure injector for task inserts:
    ``fail_inserts[0]`` > 0 fails that many task inserts with 500 (row not
    stored); ``fail_every[0]`` = k fails every k-th task insert."""

    fail_inserts: list = [0]
    fail_every: list = [0]
    inserts_seen: list = [0]
    lock = threading.Lock()

    def do_POST(self):  # noqa: N802
        table, _ = self._table_q()
        if table == "tasks":
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            row = json.loads(raw)
            with self.lock:
                self.inserts_seen[0] += 1
                n = self.inserts_seen[0]
                if self.fail_inserts[0] > 0:
                    self.fail_inserts[0] -= 1
                    self.ops.append(("POST", table, "500-injected"))
                    self._send(500)
                    return
                if self.fail_every[0] and n % self.fail_every[0] == 1:
                    self.ops.append(("POST", table, "500-injected"))
                    self._send(500)
                    return
                if any(r.get("task_id") == row.get("task_id") for r in self.rows["tasks"]):
                    self.ops.append(("POST", table, "409-pk"))
                    self._send(409)
                    return
            # hand the already-read body to the parent through a buffer
            import io
            self.rfile = io.BytesIO(raw)
        super().do_POST()


@pytest.fixture
def pkdb(monkeypatch):
    _PK.rows = {"tasks": [], "events": [], "agent_stats": []}
    _PK.gets, _PK.ops = [], []
    _PK.mode[0] = "ok"
    _PK.fail_inserts[0] = 0
    _PK.fail_every[0] = 0
    _PK.inserts_seen[0] = 0
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _PK)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    from agent_civilization.storage import supabase_store as ss
    monkeypatch.setattr(ss, "SUPABASE_URL", f"http://127.0.0.1:{httpd.server_address[1]}")
    monkeypatch.setattr(ss, "SUPABASE_KEY", "d148-test-not-a-real-key")
    monkeypatch.setattr(ss, "_storage", None)
    monkeypatch.setenv("AGOS_TASK_QUEUE", "0")          # the VOLATILE path, storage configured
    yield _PK.rows
    httpd.shutdown()
    monkeypatch.setattr(ss, "_storage", None)


def _post(c, headers, prompt="D-148: summarise 1 2 3", **extra):
    return c.post("/api/tasks", json={"specialization": "analyst", "prompt": prompt, **extra},
                  headers=headers)


def _rows_for(db, tid):
    return [r for r in db["tasks"] if r.get("task_id") == tid]


def _counter(name):
    from agos_kernel.infrastructure.metrics import get_metrics
    return sum(v for k, v in get_metrics()._counters.items() if name in str(k))


# --------------------------------------------------- app-level (real storage)

def test_d148_normal_path_unchanged(tenants, pkdb, fast):
    with _client() as c:
        r = _post(c, tenants.headers)
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "queued" and "persisted" not in body
        tid = body["task_id"]
        assert _wait_for(lambda: [x["status"] for x in _rows_for(pkdb, tid)] == ["completed"])
        assert tid not in server._ROW_MISSING
        assert "persisted" not in c.get(f"/api/tasks/{tid}", headers=tenants.headers).json()


def test_d148_failed_insert_is_visible_then_row_recreated(tenants, pkdb, fast, caplog):
    _PK.fail_inserts[0] = 1                    # the accept insert fails, storage then answers
    before = _counter("task_row_missing")
    with caplog.at_level(logging.ERROR), _client() as c:
        r = _post(c, tenants.headers)
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["persisted"] is False      # never claims a row it does not have
        from agent_civilization.core.contracts import TaskAcceptedV2, TaskRecord
        TaskAcceptedV2.model_validate(body)    # additive field, contract v2 intact
        tid = body["task_id"]
        assert "storage row was NOT created" in caplog.text
        assert _counter("task_row_missing") == before + 1
        # recovery: the terminal write re-creates the row — exactly ONE row
        assert _wait_for(lambda: [x["status"] for x in _rows_for(pkdb, tid)] == ["completed"]), pkdb["tasks"]
        row = _rows_for(pkdb, tid)[0]
        assert row["result"] is not None and row["tenant_id"] == "test_tenant"
        assert _wait_for(lambda: tid not in server._ROW_MISSING)
        rec = c.get(f"/api/tasks/{tid}", headers=tenants.headers).json()
        assert rec["persisted"] is True
        TaskRecord.model_validate(rec)
        assert tid not in server.TERMINAL_WRITE_PENDING


def test_d148_unrecoverable_missing_row_is_bounded_not_infinite(tenants, pkdb, fast, caplog):
    """RED on the pre-fix code: the deferred write of a row that never
    existed stayed in TERMINAL_WRITE_PENDING forever."""
    _PK.fail_inserts[0] = 10_000               # every task insert fails, updates answer [] (no row)
    before = _counter("task_row_abandoned")
    with caplog.at_level(logging.WARNING), _client() as c:
        r = _post(c, tenants.headers)
        assert r.status_code == 202
        tid = r.json()["task_id"]
        assert r.json().get("persisted") is False
        # the drainer gives up after the bounded number of rounds
        assert _wait_for(lambda: tid not in server.TERMINAL_WRITE_PENDING
                         and server.TASKS.get(tid, {}).get("status") in ("completed", "failed"),
                         timeout=20), (server.TERMINAL_WRITE_PENDING.get(tid), _PK.ops[-6:])
        assert _wait_for(lambda: "ABANDONED" in caplog.text, timeout=10)
        assert _counter("task_row_abandoned") == before + 1
        assert tid not in server._ROW_MISSING
        mem = c.get(f"/api/tasks/{tid}", headers=tenants.headers).json()
        assert mem["persisted"] is False and mem["result"] is not None
        assert _rows_for(pkdb, tid) == []
        drainer = server._TERMINAL_WRITE_DRAIN_TASK
        assert _wait_for(lambda: drainer is None or drainer.done(), timeout=5)


def test_d148_concurrent_tasks_with_failing_inserts_converge_once(tenants, pkdb, fast):
    _PK.fail_every[0] = 2                      # every other task insert fails
    with _client() as c:
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(10) as ex:
            resp = list(ex.map(lambda i: _post(c, tenants.headers, prompt=f"D-148 burst {i}"), range(30)))
        assert all(r.status_code == 202 for r in resp), [r.text for r in resp if r.status_code != 202]
        tids = [r.json()["task_id"] for r in resp]
        assert any(r.json().get("persisted") is False for r in resp)
        assert _wait_for(lambda: all([x["status"] for x in _rows_for(pkdb, t)] == ["completed"]
                                     for t in tids), timeout=60), \
            {t: [x["status"] for x in _rows_for(pkdb, t)] for t in tids}
        assert len(pkdb["tasks"]) == 30                      # no duplicate row
        assert _wait_for(lambda: not any(t in server._ROW_MISSING for t in tids))


def test_d148_volatile_idempotent_replay_after_failed_insert(tenants, pkdb, fast):
    _PK.fail_inserts[0] = 1
    with _client() as c:
        r1 = _post(c, tenants.headers, idempotency_key="d148-k1")
        assert r1.status_code == 202 and r1.json()["persisted"] is False
        tid = r1.json()["task_id"]
        r2 = _post(c, tenants.headers, idempotency_key="d148-k1")
        assert r2.json()["task_id"] == tid and r2.json()["duplicate"] is True
        assert _wait_for(lambda: [x["status"] for x in _rows_for(pkdb, tid)] == ["completed"])
        assert len(pkdb["tasks"]) == 1


def test_d148_cancel_of_never_created_row_is_confirmed_not_409(tenants, pkdb, fast, monkeypatch):
    _PK.fail_inserts[0] = 1
    gate = asyncio.Event()

    async def _slow(*a, **k):                  # keep the run in flight until cancelled
        await gate.wait()
    monkeypatch.setattr(server, "_run_task", _slow)
    with _client() as c:
        r = _post(c, tenants.headers)
        tid = r.json()["task_id"]
        assert r.json()["persisted"] is False
        cr = c.post(f"/api/tasks/{tid}/cancel", headers=tenants.headers)
        assert cr.status_code == 200, cr.text
        assert cr.json()["cancelled"] is True
        assert _wait_for(lambda: [x["status"] for x in _rows_for(pkdb, tid)] == ["cancelled"]), pkdb["tasks"]


def test_d148_no_storage_configured_marks_nothing(tenants, monkeypatch):
    from agent_civilization.storage import supabase_store as ss
    monkeypatch.setattr(ss, "SUPABASE_URL", "")
    monkeypatch.setattr(ss, "SUPABASE_KEY", "")
    monkeypatch.setattr(ss, "_storage", None)
    with _client() as c:
        r = _post(c, tenants.headers)
        assert r.status_code == 202 and "persisted" not in r.json()
        assert r.json()["task_id"] not in server._ROW_MISSING


# ------------------------------------------------- unit (in-process double)

class _Double:
    """In-process storage with the PK semantics; knobs per scenario."""

    def __init__(self):
        self.rows = {}
        self.calls = []

    def persists(self):
        return True

    async def insert_task(self, task_id, specialization, prompt, status="queued", *, tenant_id=None, **kw):
        self.calls.append(("insert", task_id, status))
        if task_id in self.rows:
            return False                       # unique violation (409)
        self.rows[task_id] = {"status": status, "tenant_id": tenant_id}
        return True

    async def update_task(self, task_id, status, **kw):
        self.calls.append(("update", task_id, status))
        if task_id not in self.rows:
            return False                       # "no row updated"
        self.rows[task_id].update(status=status, **{k: v for k, v in kw.items() if v is not None})
        return True

    async def transition_task(self, task_id, from_statuses, to_status, **fields):
        self.calls.append(("transition", task_id, to_status))
        row = self.rows.get(task_id)
        if row is None or row["status"] not in from_statuses:
            return False
        row["status"] = to_status
        return True


async def test_d148_ambiguous_insert_that_landed_is_updated_not_duplicated(fast, monkeypatch):
    d = _Double()
    d.rows["amb-1"] = {"status": "queued", "tenant_id": "t"}   # stored although the answer said False
    monkeypatch.setattr(server, "get_storage", lambda: d)
    server._mark_row_missing("amb-1", "analyst", "p", "t")
    assert await server._write_task_terminal("amb-1", "completed", result={"ok": True}) is True
    assert [c[0] for c in d.calls] == ["update"]                # no re-create insert at all
    assert d.rows["amb-1"]["status"] == "completed" and "amb-1" not in server._ROW_MISSING


async def test_d148_late_original_insert_never_duplicates(fast, monkeypatch):
    d = _Double()
    monkeypatch.setattr(server, "get_storage", lambda: d)
    server._mark_row_missing("late-1", "analyst", "p", "t")
    assert await server._write_task_terminal("late-1", "completed", result={"ok": True}) is True
    # the original (delayed) insert finally reaches the store: PK rejects it
    assert await d.insert_task("late-1", "analyst", "p", "queued", tenant_id="t") is False
    assert d.rows["late-1"]["status"] == "completed" and len(d.rows) == 1


async def test_d148_guarded_write_on_missing_row_recreates_not_settles(fast, monkeypatch):
    """Before: transition matched 0 rows -> logged "finished elsewhere" and
    returned True although no row existed."""
    d = _Double()
    monkeypatch.setattr(server, "get_storage", lambda: d)
    server._mark_row_missing("g-1", "analyst", "p", "t")
    ok = await server._write_task_terminal("g-1", "failed", result={"ok": False},
                                           guard_from=server.VOLATILE_LIVE_ROW_STATUSES)
    assert ok is True and d.rows["g-1"]["status"] == "failed"
    assert ("insert", "g-1", "failed") in d.calls


async def test_d148_existing_row_deferral_keeps_unbounded_r188_contract(fast, monkeypatch):
    class _Down(_Double):
        async def update_task(self, task_id, status, **kw):
            return False
    d = _Down()
    monkeypatch.setattr(server, "get_storage", lambda: d)
    assert await server._write_task_terminal("ex-1", "completed", result={"ok": True}) is False
    assert "missing_rounds" not in server.TERMINAL_WRITE_PENDING["ex-1"]
    await asyncio.sleep(0.02 * 6)              # well past the missing-row bound
    assert "ex-1" in server.TERMINAL_WRITE_PENDING          # still retried (outage contract)
