"""Route-level SSE lease lifecycle (advisor review, round 2 of PR #275).

These tests exercise the REAL route wiring — not hand-built pieces:

* the admission acquire runs OFF the event loop (``to_thread``) while the
  registry add stays ON it — proven by the loop staying responsive while
  the acquire is parked on the store's write lock;
* the LeaseKeeper is created by the ROUTE (before the first ASGI send), so
  a response whose FIRST send stalls is still kept alive and still ended
  at its lease deadline;
* the PRODUCTION ``_lease_lost`` ends a stalled response (abort FIRST, the
  registry detach on-loop, the lease release off-loop) — removing
  ``response.abort()`` from production turns these red (mutation-verified);
* an OUTER cancellation of the response leaves nothing behind: no body
  task, no renewal task, no lease, no subscription.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import server as srv
from agent_civilization.core.sse import EventBus, SSELimits
from agent_civilization.core.sse_admission import SSELeaseStore


def _setup(monkeypatch, tmp_path: Path, ttl: int = 4, renewal: int = 1):
    """Point the server's globals at a small real store/bus (small TTLs so
    the deadline paths are testable) and a fixed tenant."""
    db = str(tmp_path / "adm.sqlite3")
    store = SSELeaseStore(db)
    limits = SSELimits(max_per_tenant=10, max_per_client=5, max_connections=20,
                      lease_ttl_s=ttl, lease_renewal_s=renewal,
                      heartbeat_s=1, max_lifetime_s=60)
    bus = EventBus(limits, admission=store)
    monkeypatch.setattr(srv, "SSE_ADMISSION", store)
    monkeypatch.setattr(srv, "SSE_LIMITS", limits)
    monkeypatch.setattr(srv, "EVENT_BUS", bus)
    monkeypatch.setattr(srv, "require_principal",
                       lambda api_key: SimpleNamespace(tenant_id="t1"))
    return db, store, bus


def _lease_expiry(db: str):
    con = sqlite3.connect(db)
    try:
        row = con.execute("SELECT expires_at FROM sse_leases").fetchone()
        return row[0] if row else None
    finally:
        con.close()


def _lease_count(db: str) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT COUNT(*) FROM sse_leases").fetchone()[0]
    finally:
        con.close()


_SCOPE = {"type": "http", "http_version": "1.1", "method": "GET",
          "path": "/api/events/stream", "headers": [], "query_string": b""}


def _request():
    return SimpleNamespace(client=SimpleNamespace(host="route-test"))


async def _no_receive():
    await asyncio.Event().wait()
    return {"type": "http.request"}


def test_route_stays_responsive_while_admission_waits_on_the_store(
        monkeypatch, tmp_path):
    """The REAL route under a held write lock: the admission transaction
    waits on the store's write lock OFF the loop — the loop itself keeps
    serving (a heartbeat task on the SAME loop keeps making progress
    BEFORE the admission completes), and the admission eventually fails
    CLOSED (503), never an open stream."""
    db, store, bus = _setup(monkeypatch, tmp_path)
    con = sqlite3.connect(db)
    con.execute("BEGIN IMMEDIATE")          # hold the write lock
    try:
        async def scenario():
            beats = []

            async def beat():
                while True:
                    beats.append(time.monotonic())
                    await asyncio.sleep(0.005)

            hb = asyncio.create_task(beat())
            route = asyncio.create_task(srv.event_stream(_request(), "k"))
            try:
                # the acquire is parked on the lock — the loop must keep
                # serving (progress BEFORE the admission completes)
                await asyncio.sleep(0.4)
                assert len(beats) >= 40, (
                    "the event loop stalled while the route's admission "
                    "waited on the store's write lock")
                assert not route.done()
                # the store's busy timeout (~2s) expires: fail-closed 503
                with pytest.raises(srv.HTTPException) as ei:
                    await asyncio.wait_for(route, timeout=10)
                assert ei.value.status_code == 503
            finally:
                hb.cancel()
                try:
                    await hb
                except asyncio.CancelledError:
                    pass

        asyncio.run(scenario())
    finally:
        con.rollback()


def test_production_loss_wiring_ends_a_stalled_response(monkeypatch, tmp_path):
    """The PRODUCTION wiring end to end: a body parked inside send()
    (a stalled reader) is still RENEWED (the row's expiry keeps moving —
    the keeper does not depend on the body), and a FORCED loss (the lease
    row deleted) ends the response through the production ``_lease_lost``
    — abort FIRST, then the on-loop detach, then the off-loop release.
    Mutation check: removing ``response.abort()`` from ``_lease_lost``
    turns this red (the parked body would never end)."""
    db, store, bus = _setup(monkeypatch, tmp_path, ttl=4, renewal=1)

    async def scenario():
        resp = await srv.event_stream(_request(), "k")
        assert resp._keeper is not None and resp._keeper.task is not None

        stalled = asyncio.Event()

        async def send(message):          # the ASGI send: a stalled reader
            if message["type"] == "http.response.body":
                stalled.set()            # the body ENTERED send()
                await asyncio.Event().wait()   # ... and never returns

        call = asyncio.create_task(resp(_SCOPE, _no_receive, send))
        await asyncio.wait_for(stalled.wait(), timeout=5)
        # renewals continue while the body is stalled
        e1 = _lease_expiry(db)
        await asyncio.sleep(1.6)          # > one renewal interval
        e2 = _lease_expiry(db)
        assert e2 is not None and e1 is not None and e2 > e1
        # FORCE the loss: the lease row disappears (a killed worker's row
        # reclaimed elsewhere / any confirmed loss)
        con = sqlite3.connect(db)
        con.execute("DELETE FROM sse_leases")
        con.commit()
        con.close()
        # the next renewal (<= 1s) confirms the loss -> _lease_lost ->
        # abort -> the PARKED body is cancelled -> the response ends
        await asyncio.wait_for(call, timeout=6)
        assert bus.total == 0             # the subscription was detached

    asyncio.run(scenario())


def test_keeper_is_independent_of_a_stalled_first_send(monkeypatch, tmp_path):
    """DELAYED RESPONSE START (advisor review, round 2): a response whose
    FIRST ASGI send stalls never runs its generator — the keeper (created
    by the ROUTE, before the first send) still renews the lease, and the
    forced loss still ends the response at its deadline."""
    db, store, bus = _setup(monkeypatch, tmp_path, ttl=4, renewal=1)

    async def scenario():
        resp = await srv.event_stream(_request(), "k")
        stalled = asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.start":
                stalled.set()            # the FIRST send stalls — the
                await asyncio.Event().wait()   # generator never runs

        call = asyncio.create_task(resp(_SCOPE, _no_receive, send))
        await asyncio.wait_for(stalled.wait(), timeout=5)
        # the keeper renews although the body never started
        e1 = _lease_expiry(db)
        await asyncio.sleep(1.6)
        e2 = _lease_expiry(db)
        assert e2 is not None and e1 is not None and e2 > e1
        # forced loss ends the (never-started-body) response
        con = sqlite3.connect(db)
        con.execute("DELETE FROM sse_leases")
        con.commit()
        con.close()
        await asyncio.wait_for(call, timeout=6)
        assert bus.total == 0

    asyncio.run(scenario())


def test_outer_cancellation_leaves_no_body_or_renewal_task(monkeypatch, tmp_path):
    """An OUTER cancellation of the response must not orphan the body: with
    the body parked inside ``send()`` (the hardest case — the generator's
    finally would otherwise wait for GC), the ``__call__`` finally cancels
    BOTH child tasks, closes the body iterator DETERMINISTICALLY (aclose
    runs the generator's finally NOW), stops the keeper and releases the
    lease off-loop. Nothing survives: no pending task, no lease, no
    subscription. (Mutation-verified: removing the body-cancel from the
    finally turns this red — the parked body task stays pending.)"""
    db, store, bus = _setup(monkeypatch, tmp_path, ttl=4, renewal=1)

    async def scenario():
        resp = await srv.event_stream(_request(), "k")
        assert resp._keeper is not None

        stalled = asyncio.Event()

        async def send(message):          # the send STALLS on the body
            if message["type"] == "http.response.body":
                stalled.set()
                await asyncio.Event().wait()   # parked forever

        call = asyncio.create_task(resp(_SCOPE, _no_receive, send))
        await asyncio.wait_for(stalled.wait(), timeout=5)  # parked in send()
        call.cancel()                     # the OUTER cancellation
        with pytest.raises(asyncio.CancelledError):
            await call
        # nothing survives: no pending task at all (the parked body task
        # was cancelled — not left to GC), the keeper's run task ends,
        # the lease is released (off-loop), the subscription detached
        await asyncio.sleep(0.1)
        pending = [t for t in asyncio.all_tasks()
                   if t is not asyncio.current_task() and not t.done()]
        assert not pending, pending
        assert resp._keeper.task is not None
        await asyncio.wait_for(resp._keeper.task, timeout=5)
        assert bus.total == 0
        for _ in range(20):               # the off-loop release lands
            if _lease_count(db) == 0:
                break
            await asyncio.sleep(0.05)
        assert _lease_count(db) == 0

    asyncio.run(scenario())
