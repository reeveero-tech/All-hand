"""G-160-2 remainder (R-170 batch 22): the intermediate ``running`` write of
a VOLATILE run must never overwrite a terminal row.

Batch 21 (D-106) made the unfinished path write ``failed`` GUARDED, but the
intermediate write in ``server._run_task_traced`` stayed an unconditional
``update_task(task_id, "running")``. Its HTTP request can already be on the
wire when the run is cancelled (cancel endpoint, shutdown orphan): the
guarded ``failed`` lands first (row ``queued`` -> ``failed``), then the late
``running`` lands and the row is ``running`` forever — the exact symptom
G-160-2 was opened for, through a narrower door (declared limit of D-106).

Contract pinned here: the intermediate write is a GUARDED transition from
``queued`` only. Whatever order the two requests reach the store in, the
row ends terminal:

* failed first, running late  -> running matches nothing, row stays failed;
* running first, failed late  -> failed's guard (queued|running) matches.

Honest limits: fake storage that models an in-flight request as a write that
lands after the caller was cancelled; the real PostgREST conditional PATCH
is covered by the storage job (R-163 transition primitive), not here.
"""
from __future__ import annotations

import asyncio
import contextlib
import time

import pytest

import server


class _InFlightStore:
    """Rows + a ``running`` write whose request is "sent" immediately but
    lands only when ``release`` is set — even if the caller is cancelled."""

    def __init__(self, tid):
        self.rows = {tid: "queued"}
        self.calls = []
        self.release = asyncio.Event()
        self.sent = asyncio.Event()
        self._landers = []

    def _apply(self, tid, frm, to):
        if frm is None or self.rows.get(tid) in frm:
            self.rows[tid] = to
            return True
        return False

    async def _deliver(self, kind, tid, frm, to):
        self.calls.append((kind, tid, frm, to))
        if to == "running":
            async def land():
                await self.release.wait()
                return self._apply(tid, frm, to)
            lander = asyncio.get_running_loop().create_task(land())
            self._landers.append(lander)
            self.sent.set()
            return await asyncio.shield(lander)
        return self._apply(tid, frm, to)

    async def transition_task(self, task_id, from_statuses, to_status, **fields):
        return await self._deliver("transition", task_id, tuple(from_statuses), to_status)

    async def update_task(self, task_id, status, **kw):
        return await self._deliver("update", task_id, None, status)

    def persists(self):
        return True

    async def insert_event(self, *a, **kw):
        return True

    async def settle(self):
        for t in self._landers:
            with contextlib.suppress(asyncio.CancelledError):
                await t


class _FastAgent:
    name = "fast-agent"
    stats = {"tasks_completed": 0, "tasks_failed": 0, "total_time_s": 0.0}

    async def execute(self, payload):
        return {"ok": True, "result": "done", "elapsed_s": 0.01}


@pytest.fixture(autouse=True)
async def _isolate(monkeypatch):
    server.TERMINAL_WRITE_PENDING.clear()
    yield
    for t in list(getattr(server, "_UNFINISHED_WRITES", ())):
        t.cancel()
    server.TERMINAL_WRITE_PENDING.clear()


def _record(monkeypatch, tid):
    rec = {"id": tid, "specialization": "analyst", "prompt": "p", "status": "queued",
           "created_at": time.time(), "result": None, "tenant_id": None, "agent": None}
    monkeypatch.setitem(server.TASKS, tid, rec)


async def _until(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return pred()


async def test_late_running_write_never_overwrites_failed(monkeypatch):
    tid = "g1602-race-failed-first"
    _record(monkeypatch, tid)
    store = _InFlightStore(tid)
    monkeypatch.setattr(server, "get_storage", lambda: store)
    monkeypatch.setattr(server, "_pick_agent", lambda spec: _FastAgent())

    run = asyncio.get_running_loop().create_task(server._run_task(tid, "analyst", "p", None))
    await asyncio.wait_for(store.sent.wait(), 5)       # running request is on the wire
    run.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await run
    assert server.TASKS[tid]["status"] == "failed"
    assert await _until(lambda: store.rows[tid] == "failed"), store.calls
    store.release.set()                                # the late running request lands
    await store.settle()
    assert store.rows[tid] == "failed", (
        f"a late intermediate write overwrote the terminal row: {store.rows[tid]!r} "
        f"calls={store.calls}")
    running = [c for c in store.calls if c[3] == "running"]
    assert running and all(c[0] == "transition" and c[2] == ("queued",) for c in running), \
        f"the intermediate write must be guarded from queued only: {running}"


async def test_running_first_then_failed_still_terminal(monkeypatch):
    tid = "g1602-race-running-first"
    _record(monkeypatch, tid)
    store = _InFlightStore(tid)
    store.release.set()                                # running lands immediately
    monkeypatch.setattr(server, "get_storage", lambda: store)

    gate = asyncio.Event()

    class _Slow(_FastAgent):
        async def execute(self, payload):
            await gate.wait()
            return await super().execute(payload)
    monkeypatch.setattr(server, "_pick_agent", lambda spec: _Slow())

    run = asyncio.get_running_loop().create_task(server._run_task(tid, "analyst", "p", None))
    assert await _until(lambda: store.rows[tid] == "running"), store.calls
    run.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await run
    assert await _until(lambda: store.rows[tid] == "failed"), store.calls


async def test_normal_run_still_goes_running_then_completed(monkeypatch):
    tid = "g1602-race-normal"
    _record(monkeypatch, tid)
    store = _InFlightStore(tid)
    store.release.set()
    monkeypatch.setattr(server, "get_storage", lambda: store)
    monkeypatch.setattr(server, "_pick_agent", lambda spec: _FastAgent())

    await server._run_task(tid, "analyst", "p", None)
    assert server.TASKS[tid]["status"] == "completed"
    seq = [c[3] for c in store.calls]
    assert seq[0] == "running" and seq[-1] == "completed", store.calls
    assert store.rows[tid] == "completed"
