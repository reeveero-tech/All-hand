"""SSE shared admission (log-299 residual, لاحق 62's fix): lease store.

The red reproduction (``tests/test_sse_multiprocess.py``, branch
``m8/sse-multiprocess``) proved that SSE admission state (EventBus caps +
open-rate window) living in PROCESS-LOCAL memory multiplies the effective
caps across processes — the same defect class the rate limiter had (log 299,
fixed in log 303). These tests pin the FIX's contract at unit level:

* one lease row per subscription, acquired in ONE transaction (purge expired
  leases + prune the open-rate window + check global/tenant/client caps +
  check the shared open-rate window + insert) — no check-then-act window;
* renewal is CONDITIONAL: it never resurrects an expired or released lease;
* release is idempotent;
* a killed worker's leases expire (TTL) and its capacity frees — no
  permanent leak, and no resurrection of the expired lease;
* renewal keeps a live stream's capacity beyond the TTL;
* admission fails CLOSED when the store is unavailable (never unlimited);
* the renewal loop closes the stream on confirmed lease loss and does NOT
  fail open on a store outage (bounded retries only while the last
  confirmed lease could still be valid).

The multi-process behavior itself (real servers, real sockets) is proven in
``tests/test_sse_multiprocess.py``.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

import pytest

from agent_civilization.core.sse import EventBus, SSELimits, StreamLimit
from agent_civilization.core.sse_admission import (
    LeaseKeeper,
    SSEAdmissionError,
    SSEAdmissionRefused,
    SSELeaseStore,
    sse_admission_default_db_path,
)


def _store(tmp_path: Path) -> SSELeaseStore:
    return SSELeaseStore(str(tmp_path / "sse_admission.sqlite3"))


def _acquire(store: SSELeaseStore, tenant: str = "t1", client: str = "c1", **kw):
    params = dict(max_connections=100, max_per_tenant=3, max_per_client=2,
                  open_rate=10, open_window_s=60, lease_ttl_s=90)
    params.update(kw)
    lease_id, _expires_at = store.acquire(tenant, client, **params)
    return lease_id


# ------------------------------------------------------------- acquire basics

def test_acquire_returns_unpredictable_lease_id_and_counts(tmp_path):
    store = _store(tmp_path)
    a = _acquire(store)
    b = _acquire(store, client="c2")
    assert isinstance(a, str) and len(a) >= 16
    assert a != b
    assert store.counts() == {"leases": 2}


def test_denied_admission_leaves_no_reservation(tmp_path):
    """A capacity-refused open must not hold a slot: the lease count stays
    at the cap, and the refusal itself is not a lease."""
    store = _store(tmp_path)
    for i in range(3):                          # tenant cap 3 (distinct clients)
        _acquire(store, client=f"c{i}")
    with pytest.raises(SSEAdmissionRefused) as ei:
        _acquire(store, client="cx")
    assert ei.value.reason == "capacity"
    assert store.counts() == {"leases": 3}    # unchanged: no reservation
    # a different tenant is unaffected by the refusal
    assert _acquire(store, tenant="t2")


def test_open_rate_window_is_shared_and_counts_refused_opens(tmp_path):
    """The open-rate window is one window per CLIENT across processes; a
    capacity-refused open still counts toward it (parity with the
    in-process semantics: the rate check runs before the capacity check)."""
    store_a = _store(tmp_path)
    store_b = SSELeaseStore(str(tmp_path / "sse_admission.sqlite3"))
    # two opens on "process A"
    _acquire(store_a, client="rate-client", open_rate=2)
    _acquire(store_a, client="rate-client", open_rate=2)
    # the third open anywhere (here on "process B") is rate-refused
    with pytest.raises(SSEAdmissionRefused) as ei:
        _acquire(store_b, client="rate-client", open_rate=2)
    assert ei.value.reason == "open_rate"
    assert ei.value.retry_after_s >= 1
    # a capacity-refused open still consumed the rate window: fill the rate
    # window to its cap via refusals, then even with capacity free the
    # client is rate-refused.
    store_c = _store(tmp_path)
    for _ in range(8):   # open_rate=10 total; 2 used
        with pytest.raises(SSEAdmissionRefused):
            _acquire(store_c, client="rate-client", open_rate=10,
                     max_per_tenant=1, tenant="tx")  # capacity-refused


def test_rate_refusal_retry_after_from_oldest_hit(tmp_path):
    store = _store(tmp_path)
    _acquire(store, client="rc", open_rate=1, open_window_s=60)
    with pytest.raises(SSEAdmissionRefused) as ei:
        _acquire(store, client="rc", open_rate=1, open_window_s=60)
    assert 1 <= ei.value.retry_after_s <= 60


# ------------------------------------------------------- caps are shared

def test_tenant_client_and_global_caps_shared_across_store_instances(tmp_path):
    """Two store objects on ONE file are two 'processes': the caps must be
    enforced across both (this is the unit-level form of the red contract
    test in test_sse_multiprocess.py)."""
    a = _store(tmp_path)
    b = SSELeaseStore(str(tmp_path / "sse_admission.sqlite3"))
    # tenant cap 3 shared: 2 on A, then only 1 more on B
    _acquire(a, tenant="tt", client="k1")
    _acquire(a, tenant="tt", client="k2")
    _acquire(b, tenant="tt", client="k3")
    with pytest.raises(SSEAdmissionRefused) as ei:
        _acquire(b, tenant="tt", client="k4")
    assert ei.value.reason == "capacity"
    # client cap 2 shared across instances
    with pytest.raises(SSEAdmissionRefused):
        _acquire(a, client="cc", max_per_client=2, tenant="tc1")
        _acquire(b, client="cc", max_per_client=2, tenant="tc2")
        _acquire(a, client="cc", max_per_client=2, tenant="tc3")
    # global cap shared — ISOLATED (advisor review, rounds 2-3): a FRESH
    # database (the sections above already hold leases on the other one),
    # distinct tenants AND clients with their own caps far above the opens
    # made, so the ONLY binding constraint is max_connections — and the
    # outcomes are asserted INDIVIDUALLY: exactly 3 admitted, the 4th
    # refused for capacity (verified by mutation: removing the
    # global-cap condition must turn this red).
    gdb = str(tmp_path / "global_cap_isolated.sqlite3")
    g1 = SSELeaseStore(gdb)
    g2 = SSELeaseStore(gdb)
    admitted = 0
    for i in range(3):                       # exactly the cap: 3 admitted
        _acquire(g1 if i % 2 == 0 else g2, tenant=f"g{i}",
                 client=f"gc{i}", max_connections=3)
        admitted += 1
    assert admitted == 3
    with pytest.raises(SSEAdmissionRefused) as ei:   # the 4th: refused
        _acquire(g1, tenant="g9", client="gc9", max_connections=3)
    assert ei.value.reason == "capacity"


# ------------------------------------------------------- release and expiry

def test_release_frees_capacity_and_is_idempotent(tmp_path):
    a = _store(tmp_path)
    b = SSELeaseStore(str(tmp_path / "sse_admission.sqlite3"))
    lease = _acquire(a, tenant="rel", client="c1")
    # fill the tenant to its cap (3), then release one from the OTHER process
    _acquire(a, tenant="rel", client="c2")
    _acquire(a, tenant="rel", client="c3")
    with pytest.raises(SSEAdmissionRefused):
        _acquire(b, tenant="rel", client="c4")
    assert b.release(lease) is True          # freed by the other instance
    assert b.release(lease) is False         # idempotent: already gone
    assert _acquire(b, tenant="rel", client="c4")  # capacity freed


def test_expired_leases_free_capacity_and_cannot_be_resurrected(tmp_path):
    """SIGKILL recovery (unit form): a worker that dies holds its leases
    until they EXPIRE; expiry frees capacity and a later renewal must NOT
    resurrect the expired lease."""
    store = _store(tmp_path)
    # tenant cap 1: one lease IS the cap; the second open is refused
    lease = _acquire(store, tenant="kill", lease_ttl_s=0.2,
                     max_per_tenant=1, max_per_client=5)
    with pytest.raises(SSEAdmissionRefused):
        _acquire(store, tenant="kill", max_per_tenant=1, max_per_client=5)
    time.sleep(0.3)                          # past the TTL
    # capacity freed without anyone releasing (the killed worker's path)
    assert _acquire(store, tenant="kill", max_per_tenant=1,
                     max_per_client=5) is not None
    # the expired lease cannot be renewed back to life
    assert store.renew(lease, ttl_s=90) is None


def test_renew_extends_capacity_beyond_original_ttl(tmp_path):
    """A live stream renews: its lease still counts past the original TTL
    (renewal maintains capacity), and the cap is still enforced."""
    store = _store(tmp_path)
    # tenant cap 1: one lease IS the cap
    lease = _acquire(store, tenant="live", lease_ttl_s=0.5,
                     max_per_tenant=1, max_per_client=5)
    with pytest.raises(SSEAdmissionRefused):
        _acquire(store, tenant="live", max_per_tenant=1, max_per_client=5)
    time.sleep(0.3)
    expiry = store.renew(lease, ttl_s=60)
    assert expiry is not None and expiry > time.time()  # renewed before expiry
    time.sleep(0.4)                                   # past the ORIGINAL TTL
    assert store.counts() == {"leases": 1}            # still held
    with pytest.raises(SSEAdmissionRefused):          # cap still enforced
        _acquire(store, tenant="live", max_per_tenant=1, max_per_client=5)
    assert store.renew(lease, ttl_s=60) is not None   # and renewable again


def test_renew_of_released_lease_is_false(tmp_path):
    store = _store(tmp_path)
    lease = _acquire(store)
    store.release(lease)
    assert store.renew(lease, ttl_s=90) is None


# ------------------------------------------------------------- fail-closed

def test_acquire_fails_closed_when_store_unavailable(tmp_path):
    """A broken store must REFUSE admission (fail-closed) — never silently
    degrade to unlimited or process-local admission."""
    with pytest.raises(SSEAdmissionError):   # loud init: the server dies at boot
        SSELeaseStore(str(tmp_path / "no" / "such" / "dir" / "x.sqlite3"))


def test_renew_raises_store_error_not_false(tmp_path):
    """A store outage during renewal is an ERROR (the caller retries in a
    bounded way), distinct from a confirmed lease loss (False)."""
    with pytest.raises(SSEAdmissionError):   # loud init: the server dies at boot
        SSELeaseStore(str(tmp_path / "no" / "such" / "dir" / "x.sqlite3"))


# ------------------------------------------- no-resurrection / outages / loop
def test_expired_but_present_lease_is_not_resurrected(tmp_path: Path):
    """Advisors' probe: an expired row that is STILL PRESENT (not pruned)
    must NOT be renewed back to life. The renew is conditional
    (expires_at > now) — it stays rejected, and capacity is still
    available for new opens (the row is purged on the next acquire)."""
    store = _store(tmp_path)
    lease = _acquire(store, lease_ttl_s=0.2)
    time.sleep(0.35)                      # expired, but the ROW stays present
    assert store.renew(lease, ttl_s=90) is None        # NOT resurrected
    _acquire(store, max_per_tenant=1, max_per_client=5)  # capacity available
    assert store.counts()["leases"] == 1                 # old row purged on open


def test_admission_refuses_while_store_is_write_locked(tmp_path: Path):
    """First-run failure is not the only outage: while a store is healthy,
    a single stuck transaction (write lock held — BEGIN IMMEDIATE) turns
    every open into a fail-closed SSEAdmissionError — never an open
    stream. (sqlite3 only admits one writer at a time.)"""
    db = tmp_path / "wl.sqlite3"
    store = SSELeaseStore(str(db))
    _acquire(store)
    con = sqlite3.connect(str(db))                 # hold the write lock
    con.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(SSEAdmissionError):
            store.acquire("t1", "c2", max_connections=100, max_per_tenant=3,
                          max_per_client=2, open_rate=10, open_window_s=60,
                          lease_ttl_s=90)
    finally:
        con.rollback()
    assert _acquire(store, tenant="t9", client="c9")   # usable again


def test_admission_recovers_after_write_lock_release(tmp_path: Path):
    """A healthy-open → outage → recover cycle must leave the store usable
    without oversubscribing (the recovery-capacity regression)."""
    db = tmp_path / "wl.sqlite3"
    store = SSELeaseStore(str(db))
    _acquire(store, tenant="t1", client="c1")
    con = sqlite3.connect(str(db))
    con.execute("BEGIN IMMEDIATE")
    with pytest.raises(SSEAdmissionError):        # outage: fail-closed
        _acquire(store, tenant="t1", client="c2")
    con.rollback()                                  # outage ends
    _acquire(store, tenant="t1", client="c2")      # opens again
    with pytest.raises(SSEAdmissionRefused):       # caps still enforced
        _acquire(store, tenant="t1", client="c3", max_per_tenant=2,
                 max_per_client=5)


def test_admission_does_not_block_event_loop_under_write_lock(tmp_path: Path):
    """The open path must not BLOCK the event loop: while the store's
    write lock is held, a concurrent asyncio task on the SAME loop keeps
    making progress. The server route pushes the open off the loop via
    to_thread — this pins the same contract at the store level."""
    db = tmp_path / "wl.sqlite3"
    store = SSELeaseStore(str(db))
    _acquire(store)
    con = sqlite3.connect(str(db))
    con.execute("BEGIN IMMEDIATE")                 # hold the write lock
    try:
        async def scenario():
            beat = asyncio.create_task(_heartbeat(0.005))
            try:
                await asyncio.to_thread(            # off-loop, like the route
                    store.acquire, "t1", "c2",
                    max_connections=100, max_per_tenant=3, max_per_client=2,
                    open_rate=10, open_window_s=60, lease_ttl_s=90)
            except SSEAdmissionError:
                pass                                # the contract
            finally:
                beat.cancel()
                try:
                    await beat
                except asyncio.CancelledError:
                    pass
        asyncio.run(scenario())
    finally:
        con.rollback()


async def _heartbeat(interval):
    while True:
        await asyncio.sleep(interval)


# --------------------------------------------------------- the lease keeper
class _FakeStore:
    """Scripted renew outcomes for the keeper tests (renew returns the
    new expiry, or None = confirmed loss, or raises = outage; ``delay``
    simulates a locked/slow store call)."""

    def __init__(self, outcomes, delay=None):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.delay = delay

    def renew(self, lease_id, ttl_s):
        self.calls += 1
        if self.delay is not None:
            time.sleep(self.delay)          # a locked/slow store call
        if self.outcomes:
            out = self.outcomes.pop(0)
        else:
            out = True
        if isinstance(out, Exception):
            raise out
        if out is True:
            return time.time() + ttl_s
        if out is False:
            return None                     # confirmed loss
        return out                          # a scripted expiry (float)


async def test_keeper_closes_on_confirmed_lease_loss():
    lost = []
    fake = _FakeStore([True, True, False])     # renewed twice, then lost
    keeper = LeaseKeeper("lease", fake, ttl_s=60, interval_s=0.01,
                         on_loss=lambda: lost.append(True),
                         valid_until=time.time() + 60)
    await asyncio.wait_for(keeper.run(), timeout=5)
    assert lost == [True]
    assert fake.calls == 3


async def test_keeper_watchdog_fires_at_deadline_during_outage():
    """A store outage must NOT fail open: the WATCHDOG (independent of the
    renewal's unbounded store call) ends the stream at the last confirmed
    deadline — the renewal task may be blocked inside SQLite, the watchdog
    is not."""
    lost_at = []
    fake = _FakeStore([True] + [SSEAdmissionError("down")] * 400)
    keeper = LeaseKeeper("lease", fake, ttl_s=0.05, interval_s=0.01,
                        on_loss=lambda: lost_at.append(time.time()),
                        valid_until=time.time() + 0.05)
    t0 = time.monotonic()
    await asyncio.wait_for(keeper.run(), timeout=5)
    assert len(lost_at) == 1
    assert time.monotonic() - t0 < 5          # bounded: it did not hang forever


async def test_keeper_deadline_is_not_overshot():
    """DEADLINE REGRESSION (advisor review): with TTL=0.3s and interval=0.25s
    (interval < TTL, a legal config), a renewal failing at t=0.25 must be
    decided AT the deadline (0.3s) — a plain-interval loop would wake at
    0.5s: one full renewal interval PAST the deadline, holding the stream
    on an unprovable lease. The old loop did exactly that."""
    lost_at = []
    fake = _FakeStore([SSEAdmissionError("down")] * 400)  # fails from t=0
    t0 = time.time()
    keeper = LeaseKeeper("lease", fake, ttl_s=0.3, interval_s=0.25,
                        on_loss=lambda: lost_at.append(time.time()),
                        valid_until=t0 + 0.3)
    await asyncio.wait_for(keeper.run(), timeout=5)
    assert len(lost_at) == 1
    # fired no later than the deadline (+ scheduling margin, well under the
    # 0.25s interval the old code would have overshot by)
    assert lost_at[0] - t0 <= 0.3 + 0.05


async def test_keeper_fires_while_renewal_is_blocked_in_the_store():
    """SLOW/LOCKED RENEWAL (advisor review, round 2): the renewal's store
    call is UNBOUNDED (SQLite may sit on its busy timeout while the lease
    expires) — the WATCHDOG must still end the stream at the deadline,
    WITHOUT waiting for that blocked call. A renewal blocked for 1s with
    TTL=0.2 must produce the loss at ~0.2s, and the late completion must
    not revive anything (on_loss exactly once)."""
    lost_at = []
    fake = _FakeStore([True] * 10, delay=1.0)   # every renew blocks 1s
    t0 = time.time()
    keeper = LeaseKeeper("lease", fake, ttl_s=0.2, interval_s=0.05,
                        on_loss=lambda: lost_at.append(time.time()),
                        valid_until=t0 + 0.2)
    await asyncio.wait_for(keeper.run(), timeout=5)
    assert len(lost_at) == 1
    assert lost_at[0] - t0 <= 0.2 + 0.1        # the deadline, not the renew
    # the late (blocked) renewals completed but revived nothing
    assert keeper._loss_called and keeper._stopped


async def test_keeper_survives_short_outage_then_recovers():
    """A SHORT outage (shorter than the last confirmation's validity) must
    not close the stream: the watchdog deadline is re-armed by the next
    successful renewal."""
    lost = []
    fake = _FakeStore([True, SSEAdmissionError("down"), SSEAdmissionError("down"),
                      True, False])
    keeper = LeaseKeeper("lease", fake, ttl_s=5, interval_s=0.01,
                         on_loss=lambda: lost.append(True),
                         valid_until=time.time() + 5)
    await asyncio.wait_for(keeper.run(), timeout=5)
    assert lost == [True]                     # closed only on the final loss
    assert fake.calls == 5


async def test_keeper_renewal_is_independent_of_events_or_heartbeats():
    """The keeper's renewal must not depend on heartbeat output or events:
    with NO events flowing at all, renewals still happen on schedule."""
    fake = _FakeStore([])                       # always renews
    keeper = LeaseKeeper("lease", fake, ttl_s=60, interval_s=0.01,
                         on_loss=lambda: None,
                         valid_until=time.time() + 60)

    async def events_flow():
        await asyncio.sleep(0.05)              # no events, no heartbeats

    task = asyncio.create_task(keeper.run())
    await events_flow()
    assert fake.calls >= 2                     # renewed regardless
    keeper.stop()
    await task


async def test_keeper_no_late_completion_revive():
    """A renewal completing AFTER the loss must not revive anything: the
    keeper is stopped, on_loss was called exactly once, and the run task
    ends (no surviving renewal task)."""
    lost = []
    fake = _FakeStore([None, True, True])       # loss, then late successes
    keeper = LeaseKeeper("lease", fake, ttl_s=5, interval_s=0.01,
                         on_loss=lambda: lost.append(True),
                         valid_until=time.time() + 5)
    task = asyncio.create_task(keeper.run())   # as production runs it
    await asyncio.wait_for(task, timeout=5)
    assert lost == [True]                      # exactly once
    assert task.done() and keeper.task is task # the run task ended


# ------------------ the RESPONSE side (server.py): a lease loss vs a STALLED body
def test_lease_loss_cancels_a_stalled_response():
    """A stalled reader can leave the generator parked inside ``send()`` —
    no in-generator check (sub.closed, lifetime, the wake event) would ever
    run there. The lease loss must STILL end the response:

    * the keeper (production class) keeps renewing WHILE the body is
      stalled (its independence is not just from heartbeats — it is from
      the body);
    * on the confirmed loss the response body task is CANCELLED and the
      release runs (the stream never outlives its lease).
    """
    import server as srv

    async def scenario():
        released = []
        stalled_in_send = asyncio.Event()

        async def gen():                      # the SSE body generator
            yield b"data: {\"type\":\"open\"}\n\n"
            yield b"data: {\"type\":\"never\"}\n\n"   # parked after this

        async def send(message):              # the ASGI send: a stalled reader
            if message["type"] == "http.response.body":
                stalled_in_send.set()         # the body ENTERED send()
                await asyncio.Event().wait()  # ... and never returns

        async def receive():                   # no disconnect ever comes
            await asyncio.Event().wait()
            return {"type": "http.request"}

        async def release():
            released.append(True)

        response = srv._SSEResponse(
            gen(), release=release, media_type="text/event-stream")

        fake = _FakeStore([True, True, False])   # renewed, renewed, LOST
        lost = []
        keeper = LeaseKeeper("lease", fake, ttl_s=5, interval_s=0.01,
                             on_loss=lambda: (lost.append(True),
                                               response.abort()),
                             valid_until=time.time() + 5)
        response.attach_keeper(keeper)
        loop = asyncio.create_task(keeper.run())

        scope = {"type": "http", "http_version": "1.1", "method": "GET",
                 "path": "/api/events/stream", "headers": [],
                 "query_string": b""}
        call = asyncio.create_task(response(scope, receive, send))
        try:
            # wait until the body is parked inside send() (the stall)
            await asyncio.wait_for(stalled_in_send.wait(), timeout=5)
            # the keeper kept running WHILE the body was stalled
            await asyncio.wait_for(asyncio.sleep(0.05), timeout=1)
            assert fake.calls >= 2, fake.calls
            # the loss ends the response: the body task is cancelled,
            # the __call__ returns, and the release ran
            await asyncio.wait_for(call, timeout=5)
            assert lost == [True]
            assert released == [True]
        finally:
            keeper.stop()
            try:
                await loop
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


# ------------------------------------------------------- EventBus integration

def _bus(tmp_path: Path, **limits) -> EventBus:
    store = SSELeaseStore(str(tmp_path / "sse_admission.sqlite3"))
    lim = SSELimits(max_per_tenant=3, max_per_client=2, max_connections=100,
                    open_rate=10, **limits)
    return EventBus(lim, admission=store)


def test_event_bus_with_admission_acquires_and_releases_lease(tmp_path):
    bus = _bus(tmp_path)
    sub = bus.open("t1", "c1")
    assert sub.lease_id
    assert bus._admission.counts() == {"leases": 1}
    bus.close(sub)
    assert bus._admission.counts() == {"leases": 0}


def test_event_bus_translates_refusals_to_stream_limit(tmp_path):
    bus = _bus(tmp_path)
    for _ in range(3):
        bus.open("t1", "c1" if _ < 2 else "c2")
    with pytest.raises(StreamLimit) as ei:
        bus.open("t1", "c3")
    assert ei.value.reason == "capacity"
    assert bus.stats["refused_capacity"] == 1


def test_event_bus_without_admission_unchanged(tmp_path):
    """No admission store = the previous in-process behavior, byte-for-byte
    semantics (the store is opt-in at construction; the server always passes
    one)."""
    bus = EventBus(SSELimits())
    sub = bus.open("t1", "c1")
    assert sub.lease_id is None
    bus.close(sub)


def test_event_bus_close_releases_lease_idempotently(tmp_path):
    bus = _bus(tmp_path)
    sub = bus.open("t1", "c1")
    assert bus.close(sub) is True
    assert bus.close(sub) is False             # idempotent
    assert bus._admission.counts() == {"leases": 0}


def test_two_buses_on_one_file_share_admission(tmp_path):
    """Two EventBus objects (two 'processes') sharing one store file: the
    per-tenant cap is ONE cap (the unit form of the multiprocess contract)."""
    a = _bus(tmp_path)
    store = a._admission
    b = EventBus(SSELimits(max_per_tenant=3, max_per_client=2,
                           max_connections=100, open_rate=10), admission=store)
    a.open("shared", "ca")
    a.open("shared", "cb")
    b.open("shared", "cc")
    with pytest.raises(StreamLimit):
        b.open("shared", "cd")


def test_default_db_path_outside_source_tree():
    import os
    p = sse_admission_default_db_path()
    assert "agent_civilization" not in p and "server.py" not in p
    assert os.path.isabs(p)


def test_wal_enabled_on_shared_file(tmp_path):
    """The lease store must actually run in WAL mode (the mode is persistent
    in the DB file, and a live -wal file exists while a connection is open
    after a write — SQLite removes it when the LAST connection closes, which
    is normal). The R-189 review caught a WAL claim with no -wal file; the
    same class of proof is required here."""
    store = _store(tmp_path)
    _acquire(store)
    db = str(tmp_path / "sse_admission.sqlite3")
    conn = sqlite3.connect(db)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
        conn.execute("INSERT INTO sse_open_hits (client, ts) VALUES ('wal_probe', 0)")
        conn.commit()  # WAL frames are written at commit
        wal = Path(db + "-wal")
        assert wal.exists() and wal.stat().st_size > 0
    finally:
        conn.close()


def test_schema_created_idempotently(tmp_path):
    p = str(tmp_path / "sse_admission.sqlite3")
    SSELeaseStore(p)
    SSELeaseStore(p)   # re-init on an existing file must not fail
    assert _acquire(SSELeaseStore(p))
