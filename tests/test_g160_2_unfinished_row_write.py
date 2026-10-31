"""G-160-2 (R-170 batch 21): a VOLATILE task that ends unfinished — its run
cancelled, its run crashed, or its worker stopped at shutdown — must not
leave its storage row ``running`` forever.

Before this batch ``server._end_unfinished`` moved only the in-memory record
and the in-process SSE bus to ``failed``; the storage row that the run had
already written as ``running`` was never rewritten (documented gap G-160-2:
API.md, OPERATIONS.md). A fresh process — or any reader of the store —
saw a task that would never finish.

The contract pinned here:

1. crash / cancellation / shutdown orphan of a VOLATILE run rewrites the
   row to ``failed`` with the SAME fixed outcome as the memory record
   (no exception text);
2. the write is GUARDED (``transition_task`` from the volatile live
   statuses only): a row that someone else already finished or cancelled is
   never overwritten, and the unconditional ``update_task`` is never used
   for this path;
3. a DURABLE run's row is never touched by the shutdown path — its
   recovery belongs to the lease/reaper (R-163), and writing ``failed``
   there would defeat the no-loss guarantee;
4. a record already terminal in memory (the cancel endpoint already did the
   guarded row write) produces no second write;
5. the lifespan awaits these writes (bounded) BEFORE closing storage —
   otherwise the shutdown orphans' writes die with the session;
6. a store that fails the request is retried in-line and, past the budget,
   the GUARDED write is deferred to the background drainer, which lands it
   with the same guard.

Honest limits (not claimed here): the deferral is in memory — a restart
still loses it (the volatile mode's documented durability bound); the
shutdown flush is bounded by ``AGOS_TASK_SHUTDOWN_WRITE_FLUSH_S`` and a
write still pending past it is CRITICAL-logged, not guaranteed; a run
cancelled by the event loop's own teardown after the lifespan has ended
cannot schedule anything. Fake storage only — no live PostgREST here (the
storage CI job covers the transition primitive itself).
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from types import SimpleNamespace

import pytest

import server


class _RowStore:
    """In-memory rows + the two write primitives the server may use."""

    def __init__(self, rows, *, fail=0, delay=0.0, order=None):
        self.rows = dict(rows)
        self.calls = []
        self.fail = fail            # number of transition REQUESTS that fail (None)
        self.delay = delay
        self.order = order if order is not None else []

    async def transition_task(self, task_id, from_statuses, to_status, **fields):
        if self.delay:
            await asyncio.sleep(self.delay)
        self.calls.append(("transition", task_id, tuple(from_statuses), to_status, fields))
        if self.fail > 0:
            self.fail -= 1
            return None
        if self.rows.get(task_id) not in from_statuses:
            return False
        self.rows[task_id] = to_status
        self.order.append(("landed", task_id))
        return True

    async def update_task(self, task_id, status, **kw):
        self.calls.append(("update", task_id, status, kw))
        self.rows[task_id] = status
        return True

    async def insert_event(self, *a, **kw):
        return True

    async def close(self):
        self.order.append(("close",))


@pytest.fixture(autouse=True)
async def _isolate(monkeypatch):
    server.TERMINAL_WRITE_PENDING.clear()
    yield
    for t in list(getattr(server, "_UNFINISHED_WRITES", ())):
        t.cancel()
    task = server._TERMINAL_WRITE_DRAIN_TASK
    if task is not None and not task.done():
        task.cancel()
        if task.get_loop() is asyncio.get_running_loop():
            with contextlib.suppress(asyncio.CancelledError):
                await task
    server.TERMINAL_WRITE_PENDING.clear()
    server._TERMINAL_WRITE_DRAIN_TASK = None


def _volatile(monkeypatch, tid, status="running"):
    rec = {"id": tid, "specialization": "analyst", "prompt": "p", "status": status,
           "created_at": time.time(), "result": None, "tenant_id": None, "agent": "a1"}
    monkeypatch.setitem(server.TASKS, tid, rec)
    return rec


async def _until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return pred()


def _transitions(store, tid):
    return [c for c in store.calls if c[0] == "transition" and c[1] == tid]


async def test_crashed_volatile_run_rewrites_running_row(monkeypatch):
    tid = "g1602-crash"
    _volatile(monkeypatch, tid)
    store = _RowStore({tid: "running"})
    monkeypatch.setattr(server, "get_storage", lambda: store)

    async def boom(*a, **kw):
        raise RuntimeError("secret-ish adapter text must not travel")
    monkeypatch.setattr(server, "_run_task_traced", boom)

    await server._run_task(tid, "analyst", "p", None)
    assert server.TASKS[tid]["status"] == "failed"
    assert await _until(lambda: store.rows[tid] == "failed"), \
        f"the storage row stayed {store.rows[tid]!r} (G-160-2): calls={store.calls}"
    (_, _, frm, to, fields), = _transitions(store, tid)
    assert to == "failed" and "running" in frm and "queued" in frm
    assert fields["result"]["result"] == {"error": "task failed: internal error"}
    assert fields["result"] == server.TASKS[tid]["result"]       # same outcome as memory
    assert "secret-ish" not in repr(store.calls)
    assert not [c for c in store.calls if c[0] == "update"], \
        "the unfinished path must never use the unconditional update_task"


async def test_cancelled_volatile_run_rewrites_running_row(monkeypatch):
    tid = "g1602-cancel"
    _volatile(monkeypatch, tid)
    store = _RowStore({tid: "running"})
    monkeypatch.setattr(server, "get_storage", lambda: store)
    hold = asyncio.Event()

    async def wait_forever(*a, **kw):
        await hold.wait()
    monkeypatch.setattr(server, "_run_task_traced", wait_forever)

    run = asyncio.create_task(server._run_task(tid, "analyst", "p", None))
    await asyncio.sleep(0.05)
    run.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await run
    assert await _until(lambda: store.rows[tid] == "failed"), \
        f"the storage row stayed {store.rows[tid]!r} (G-160-2): calls={store.calls}"
    (_, _, _, _, fields), = _transitions(store, tid)
    assert fields["result"]["result"] == {"error": "task cancelled before completion"}


async def test_shutdown_orphan_rewrites_volatile_row_never_a_durable_one(monkeypatch):
    vol, dur = "g1602-orphan-vol", "g1602-orphan-dur"
    _volatile(monkeypatch, vol)
    _volatile(monkeypatch, dur)
    monkeypatch.setitem(server._QUEUE_STATE, dur, {"task_id": dur, "state": "running",
                                                   "claim_token": "t", "attempts": 1})
    store = _RowStore({vol: "running", dur: "running"})
    monkeypatch.setattr(server, "get_storage", lambda: store)
    monkeypatch.setattr(server.AGENT_REGISTRY, "stop",
                        lambda wid, reason="": SimpleNamespace(task_ids=[vol, dur]))

    server._stop_registry_worker()
    assert server.TASKS[vol]["status"] == "failed"
    assert await _until(lambda: store.rows[vol] == "failed"), \
        f"the orphan's row stayed {store.rows[vol]!r} (G-160-2): calls={store.calls}"
    (_, _, _, _, fields), = _transitions(store, vol)
    assert fields["result"]["result"] == {
        "error": "task cancelled before completion: worker stopped"}
    await asyncio.sleep(0.1)
    assert store.rows[dur] == "running" and not [c for c in store.calls if c[1] == dur], \
        "a durable row belongs to the lease/reaper recovery — shutdown must not finish it"


async def test_guard_never_overwrites_a_row_finished_elsewhere(monkeypatch):
    tid = "g1602-guard"
    _volatile(monkeypatch, tid)
    store = _RowStore({tid: "cancelled"})        # another process already cancelled it
    monkeypatch.setattr(server, "get_storage", lambda: store)

    async def boom(*a, **kw):
        raise RuntimeError("x")
    monkeypatch.setattr(server, "_run_task_traced", boom)

    await server._run_task(tid, "analyst", "p", None)
    assert await _until(lambda: bool(_transitions(store, tid))), \
        f"no guarded write was attempted at all (G-160-2): calls={store.calls}"
    await asyncio.sleep(0.1)
    assert store.rows[tid] == "cancelled"
    assert not [c for c in store.calls if c[0] == "update"]
    assert tid not in server.TERMINAL_WRITE_PENDING, \
        "a row finished elsewhere is a settled outcome — never deferred for retry"


async def test_record_already_terminal_writes_nothing(monkeypatch):
    """Control (passes before and after): the cancel endpoint moves the
    record (and, with storage, the row via its own guarded transition)
    BEFORE cancelling the run — the run's cancellation adds no write."""
    tid = "g1602-already"
    _volatile(monkeypatch, tid, status="failed")
    store = _RowStore({tid: "cancelled"})
    monkeypatch.setattr(server, "get_storage", lambda: store)
    hold = asyncio.Event()

    async def wait_forever(*a, **kw):
        await hold.wait()
    monkeypatch.setattr(server, "_run_task_traced", wait_forever)
    run = asyncio.create_task(server._run_task(tid, "analyst", "p", None))
    await asyncio.sleep(0.05)
    run.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await run
    await asyncio.sleep(0.2)
    assert store.calls == []


async def test_lifespan_lands_orphan_writes_before_closing_storage(monkeypatch):
    tid = "g1602-lifespan"
    _volatile(monkeypatch, tid)
    order = []
    store = _RowStore({tid: "running"}, delay=0.3, order=order)
    monkeypatch.setattr(server, "get_storage", lambda: store)
    real_stop = server.AGENT_REGISTRY.stop

    def stop(wid, reason="worker stopped"):
        o = real_stop(wid, reason)
        return SimpleNamespace(task_ids=list(o.task_ids) + [tid])
    monkeypatch.setattr(server.AGENT_REGISTRY, "stop", stop)

    async with server._lifespan(server.app):
        pass
    assert ("landed", tid) in order and ("close",) in order, order
    assert order.index(("landed", tid)) < order.index(("close",)), \
        f"storage was closed before the orphan's row write landed: {order}"


async def test_failed_request_is_retried_then_deferred_with_the_same_guard(monkeypatch):
    monkeypatch.setattr(server, "TASK_TERMINAL_WRITE_ATTEMPTS", 2)
    monkeypatch.setattr(server, "TASK_TERMINAL_WRITE_MAX_WAIT_S", 0.01)
    monkeypatch.setattr(server, "TERMINAL_WRITE_DRAIN_INTERVAL_S", 0.05)
    tid = "g1602-defer"
    _volatile(monkeypatch, tid)
    store = _RowStore({tid: "running"}, fail=4)  # 2 in-line + 2 drainer rounds fail
    monkeypatch.setattr(server, "get_storage", lambda: store)

    async def boom(*a, **kw):
        raise RuntimeError("x")
    monkeypatch.setattr(server, "_run_task_traced", boom)

    await server._run_task(tid, "analyst", "p", None)
    assert await _until(lambda: store.rows[tid] == "failed", timeout=10), \
        f"the deferred guarded write never landed: calls={store.calls}"
    assert len(_transitions(store, tid)) == 5
    assert not [c for c in store.calls if c[0] == "update"], \
        "the drainer must land the GUARDED write, not an unconditional update"
    assert await _until(lambda: tid not in server.TERMINAL_WRITE_PENDING)


async def test_no_storage_configured_is_not_an_outage(monkeypatch, caplog):
    """R-188 finding (this batch): with NO storage configured every write
    primitive fails at once. That is nothing to persist — not an outage:
    the terminal write used to spend the whole in-line budget (~9s), log
    CRITICAL "NOT persisted" and defer forever to a drainer that could
    never land it (an unbounded pending map in a store-less deployment)."""
    import logging

    from agent_civilization.storage import supabase_store
    monkeypatch.setattr(supabase_store, "is_configured", lambda: False)
    monkeypatch.setattr(server, "get_storage", lambda: supabase_store.SupabaseStorage())
    started = time.time()
    with caplog.at_level(logging.CRITICAL):
        ok = await server._write_task_terminal("g1602-nostore", "completed", result={"ok": True})
    assert ok is True
    assert time.time() - started < 1.0
    assert "g1602-nostore" not in server.TERMINAL_WRITE_PENDING
    assert not [r for r in caplog.records if r.levelno >= logging.CRITICAL]
