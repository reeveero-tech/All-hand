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


def test_loss_before_call_is_not_forgotten(monkeypatch, tmp_path):
    """LOSS BEFORE ``__call__`` (advisor review, round 3): the keeper starts
    in the ROUTE — before the first ASGI send — so a lease loss can fire
    BEFORE the response is ever invoked. The abort event is PERSISTENT from
    construction: an early loss must still cancel the body the moment the
    response is invoked (a stale event re-created inside ``__call__`` would
    forget it and the stalled send would never end). Mutation check:
    re-creating the event in ``__call__`` turns this red (hang)."""
    db, store, bus = _setup(monkeypatch, tmp_path, ttl=4, renewal=1)

    async def scenario():
        resp = await srv.event_stream(_request(), "k")
        assert resp._keeper is not None
        # FORCE the loss BEFORE any __call__ — and let the keeper finish
        con = sqlite3.connect(db)
        con.execute("DELETE FROM sse_leases")
        con.commit()
        con.close()
        await asyncio.wait_for(resp._keeper.task, timeout=6)  # loss fired
        # NOW invoke the response with a stalled FIRST send: the persistent
        # abort must cancel the body immediately (no keeper is left)
        stalled = asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.start":
                stalled.set()
                await asyncio.Event().wait()   # stalls forever

        call = asyncio.create_task(resp(_SCOPE, _no_receive, send))
        await asyncio.wait_for(stalled.wait(), timeout=5)
        await asyncio.wait_for(call, timeout=5)   # ended by the EARLY abort
        assert bus.total == 0

    asyncio.run(scenario())


def test_deadline_cancels_production_send_while_renewal_is_blocked(
        monkeypatch, tmp_path):
    """DEADLINE-DRIVEN CANCELLATION of the PRODUCTION response (advisor
    review, round 3): acquire normally, stall the ASGI send, then HOLD the
    store's write lock so every renewal is blocked INSIDE SQLite across
    the expiry — the WATCHDOG must still cancel the production send task
    NEAR the last confirmed deadline, while the lock is still held (no
    renewal ever succeeded after the stall). Proven result (stated
    honestly): the body is signalled/cancelled at the deadline subject to
    event-loop scheduling; response cleanup and socket closure complete
    afterwards (bounded by the store's busy timeout)."""
    db, store, bus = _setup(monkeypatch, tmp_path, ttl=2, renewal=1)

    async def scenario():
        t0 = time.monotonic()
        resp = await srv.event_stream(_request(), "k")
        # HOLD the write lock IMMEDIATELY — BEFORE the first renewal
        # interval (1s) can elapse: no renewal ever succeeds after this
        # point, so the last confirmed deadline stays t0+ttl. (Holding it
        # only after the send stalled let the first renewal race past and
        # legitimately push the deadline — a flake, not a defect.)
        con = sqlite3.connect(db)
        con.execute("BEGIN IMMEDIATE")
        cancelled_at = []
        stalled = asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.body":
                stalled.set()
                try:
                    await asyncio.Event().wait()   # parked forever
                except asyncio.CancelledError:      # the deadline watchdog
                    cancelled_at.append(time.monotonic())
                    raise

        call = asyncio.create_task(resp(_SCOPE, _no_receive, send))
        await asyncio.wait_for(stalled.wait(), timeout=5)
        try:
            # the production send task is cancelled NEAR the deadline
            # (ttl=2 + scheduling margin) — while the lock is STILL held
            for _ in range(200):
                if cancelled_at:
                    break
                await asyncio.sleep(0.05)
            assert cancelled_at, "the stalled send was never cancelled"
            assert cancelled_at[0] - t0 <= 2 + 1.0, (   # at the deadline
                "the stalled send was not cancelled near the lease "
                "deadline", cancelled_at[0] - t0)
        finally:
            con.rollback()                        # release the lock
        # cleanup completes afterwards (bounded by the store busy timeout)
        await asyncio.wait_for(call, timeout=15)
        assert bus.total == 0

    asyncio.run(scenario())


def test_outer_cancel_during_blocked_renewal(monkeypatch, tmp_path):
    """OUTER CANCELLATION while a renewal is BLOCKED inside the store
    (advisor reviews, rounds 3-4 — deterministic): a barrier around the
    production store's ``renew`` proves the renewal ENTERED the blocked
    operation before the cancellation. Then:

    1. the ASGI send receives the cancellation (bounded — the response
       cleanup never waits on the blocked database work);
    2. the keeper's asyncio tasks FINISH while the renewal barrier is
       STILL HELD (shutdown cancels and joins them; the worker thread's
       own operation may outlive them, harmlessly);
    3. releasing the barrier afterwards produces no revival and no
       further loss notification (the late result is discarded).
    The barrier is released in the test cleanup so no worker thread is
    left hanging on the default executor."""
    import threading

    db, store, bus = _setup(monkeypatch, tmp_path, ttl=8, renewal=1)

    class _RenewBarrierStore:
        """Delegates everything to the real store; ``renew`` signals that
        it ENTERED and then blocks on the gate (a worker-thread barrier —
        deterministic, unlike a write lock whose blocking is timing)."""

        def __init__(self, inner):
            self._inner = inner
            self.entered = threading.Event()
            self.gate = threading.Event()

        def acquire(self, *a, **kw):
            return self._inner.acquire(*a, **kw)     # normal (unblocked)

        def release(self, *a, **kw):
            return self._inner.release(*a, **kw)

        def renew(self, lease_id, ttl_s):
            self.entered.set()                       # ENTERED the op
            self.gate.wait(timeout=30)               # held by the test
            return self._inner.renew(lease_id, ttl_s)

        def counts(self):
            return self._inner.counts()

        def reset(self):
            return self._inner.reset()

    barrier = _RenewBarrierStore(store)
    monkeypatch.setattr(srv, "SSE_ADMISSION", barrier)
    monkeypatch.setattr(bus, "_admission", barrier)

    async def wait_entered(evt, timeout=5):
        loop = asyncio.get_event_loop()
        end = loop.time() + timeout
        while not evt.is_set():
            if loop.time() > end:
                return False
            await asyncio.sleep(0.02)
        return True

    async def scenario():
        resp = await srv.event_stream(_request(), "k")
        assert resp._keeper is not None
        stalled = asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.body":
                stalled.set()
                await asyncio.Event().wait()

        call = asyncio.create_task(resp(_SCOPE, _no_receive, send))
        await asyncio.wait_for(stalled.wait(), timeout=5)     # body parked
        # DETERMINISTIC: a renewal has ENTERED the blocked store operation
        assert await wait_entered(barrier.entered), "renewal never entered"
        try:
            call.cancel()                        # the OUTER cancellation
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(call, timeout=5)  # bounded cleanup
            # (2) the keeper's asyncio tasks finished WHILE the barrier is
            # still held — no keeper task outlives the response
            await asyncio.wait_for(resp._keeper.task, timeout=5)
            assert resp._keeper.task.done()
            assert not barrier.gate.is_set()      # ...still held
            # the keeper is stopped: no on_loss can fire anymore
            assert resp._keeper._stopped
            # (3) release the barrier: the worker's renew completes, its
            # result is discarded — no revival, no further loss
            barrier.gate.set()
            await asyncio.sleep(0.3)
            assert resp._keeper.task.done()        # still done
            assert not resp._abort.is_set()        # no loss notification
        finally:
            barrier.gate.set()                     # never hang the executor
        assert bus.total == 0                      # the subscription detached

    asyncio.run(scenario())


def test_route_cancellation_releases_the_in_flight_acquisition(
        monkeypatch, tmp_path):
    """ACQUISITION CANCELLATION (advisor review, round 4): the caller can
    vanish while the store transaction is IN FLIGHT — the worker may still
    COMMIT a lease that no subscription, keeper, or response cleanup will
    ever see (the reservation would live until the TTL). The acquisition is
    SHIELDED and OWNED: a cancelled caller leaves a cleanup task that
    awaits the in-flight transaction and RELEASES whatever it acquires —
    without registering any subscription off-loop. Proven with a barrier:
    the acquisition ENTERS the transaction, the caller is cancelled, the
    gate opens, and the lease is RELEASED well before the TTL could expire
    (poll window < ttl — the release, not expiry, is what frees it)."""
    import threading

    db, store, bus = _setup(monkeypatch, tmp_path, ttl=8, renewal=2)

    class _AcquireBarrierStore:
        """Delegates everything; ``acquire`` signals ENTRY and blocks on the
        gate (deterministic in-flight transaction)."""

        def __init__(self, inner):
            self._inner = inner
            self.entered = threading.Event()
            self.gate = threading.Event()
            self.committed = threading.Event()
            self.failure = None

        def acquire(self, *a, **kw):
            self.entered.set()                      # ENTERED the transaction
            self.gate.wait(timeout=30)              # held by the test
            try:
                r = self._inner.acquire(*a, **kw)   # the worker COMMITS here
            except BaseException as e:              # pragma: no cover
                self.failure = e
                raise
            finally:
                self.committed.set()                # transaction finished
            return r

        def release(self, *a, **kw):
            return self._inner.release(*a, **kw)

        def renew(self, *a, **kw):
            return self._inner.renew(*a, **kw)

        def counts(self):
            return self._inner.counts()

        def reset(self):
            return self._inner.reset()

    barrier = _AcquireBarrierStore(store)
    monkeypatch.setattr(srv, "SSE_ADMISSION", barrier)
    monkeypatch.setattr(bus, "_admission", barrier)

    async def wait_entered(evt, timeout=5):
        loop = asyncio.get_event_loop()
        end = loop.time() + timeout
        while not evt.is_set():
            if loop.time() > end:
                return False
            await asyncio.sleep(0.02)
        return True

    async def scenario():
        route = asyncio.create_task(srv.event_stream(_request(), "k"))
        # DETERMINISTIC: the store transaction is IN FLIGHT
        assert await wait_entered(barrier.entered), "acquire never entered"
        route.cancel()                            # the caller vanishes
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(route, timeout=5)   # shielded: bounded
        # open the gate: the worker COMMITS the lease — the cleanup owns it.
        # FIRST wait for the commit to be observable (the transaction
        # finished — outcome in ``committed``/``failure``): a poll that
        # started BEFORE the commit could trivially see 0 rows and pass
        # without proving anything (advisor review, round 4).
        barrier.gate.set()
        assert await wait_entered(barrier.committed), "acquire never finished"
        assert barrier.failure is None, barrier.failure
        # the lease is RELEASED (poll window 4s << ttl 8s — the TTL could
        # not have expired yet; only the late-acquisition cleanup frees it)
        deadline = asyncio.get_event_loop().time() + 4
        released = False
        while asyncio.get_event_loop().time() < deadline:
            if _lease_count(db) == 0:
                released = True
                break
            await asyncio.sleep(0.05)
        assert released, (
            "the in-flight lease was not released after the caller was "
            "cancelled — it would live until the TTL")
        assert bus.total == 0                     # no subscription leaked

    asyncio.run(scenario())
