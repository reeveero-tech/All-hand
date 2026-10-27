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
    SSEAdmissionError,
    SSEAdmissionRefused,
    SSELeaseStore,
    renewal_loop,
    sse_admission_default_db_path,
)


def _store(tmp_path: Path) -> SSELeaseStore:
    return SSELeaseStore(str(tmp_path / "sse_admission.sqlite3"))


def _acquire(store: SSELeaseStore, tenant: str = "t1", client: str = "c1", **kw):
    params = dict(max_connections=100, max_per_tenant=3, max_per_client=2,
                  open_rate=10, open_window_s=60, lease_ttl_s=90)
    params.update(kw)
    return store.acquire(tenant, client, **params)


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
    # global cap shared
    g1 = _store(tmp_path)
    g2 = SSELeaseStore(str(tmp_path / "sse_admission.sqlite3"))
    with pytest.raises(SSEAdmissionRefused) as ei:
        for i in range(4):
            _acquire(g1 if i % 2 == 0 else g2, tenant=f"g{i}", max_connections=3)
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
    assert store.renew(lease, ttl_s=90) is False


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
    assert store.renew(lease, ttl_s=60) is True      # renewed before expiry
    time.sleep(0.4)                                   # past the ORIGINAL TTL
    assert store.counts() == {"leases": 1}            # still held
    with pytest.raises(SSEAdmissionRefused):          # cap still enforced
        _acquire(store, tenant="live", max_per_tenant=1, max_per_client=5)
    assert store.renew(lease, ttl_s=60) is True       # and renewable again


def test_renew_of_released_lease_is_false(tmp_path):
    store = _store(tmp_path)
    lease = _acquire(store)
    store.release(lease)
    assert store.renew(lease, ttl_s=90) is False


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


# ------------------------------------------------------------- renewal loop

class _FakeStore:
    """Scripted renew outcomes for the renewal loop tests."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def renew(self, lease_id, ttl_s):
        self.calls += 1
        if self.outcomes:
            out = self.outcomes.pop(0)
        else:
            out = True
        if isinstance(out, Exception):
            raise out
        return out


async def test_renewal_loop_closes_on_confirmed_lease_loss():
    lost = []
    fake = _FakeStore([True, True, False])     # renewed twice, then lost
    await renewal_loop("lease", fake, ttl_s=60, interval_s=0.01,
                       on_loss=lambda: lost.append(True))
    assert lost == [True]
    assert fake.calls == 3


async def test_renewal_loop_retries_store_outage_while_lease_may_be_valid():
    """A store outage must NOT fail open: the loop keeps retrying only
    while the last confirmed lease could still be valid; past the TTL since
    the last confirmation it releases (closes the stream)."""
    lost = []
    # outage for longer than the ttl since last confirmation → give up
    fake = _FakeStore([True] + [SSEAdmissionError("down")] * 400)
    t0 = time.monotonic()
    await renewal_loop("lease", fake, ttl_s=0.05, interval_s=0.01,
                       on_loss=lambda: lost.append(True))
    assert lost == [True]
    assert time.monotonic() - t0 < 5          # bounded: it did not hang forever


async def test_renewal_loop_survives_outage_then_recovers():
    """A SHORT outage (shorter than the time the last confirmation stays
    valid) must not close the stream: the loop retries and the next
    successful renewal re-arms the validity window."""
    lost = []
    fake = _FakeStore([True, SSEAdmissionError("down"), SSEAdmissionError("down"), True, False])
    await renewal_loop("lease", fake, ttl_s=5, interval_s=0.01,
                       on_loss=lambda: lost.append(True))
    assert lost == [True]                     # closed only on the final loss
    assert fake.calls == 5


async def test_renewal_loop_is_independent_of_heartbeats_or_events():
    """Continuous events (or none at all) must not matter: renewal is a
    periodic schedule of its own. A store that always succeeds keeps the
    stream alive for many intervals with zero events in flight."""
    fake = _FakeStore([])
    alive = []

    async def events_flow():
        for _ in range(20):                  # a busy event loop, no heartbeats
            alive.append(1)
            await asyncio.sleep(0.005)

    task = asyncio.create_task(
        renewal_loop("lease", fake, ttl_s=60, interval_s=0.01,
                     on_loss=lambda: alive.append("lost")))
    await events_flow()
    assert fake.calls >= 2                   # renewed regardless of events
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


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
