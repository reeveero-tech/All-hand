"""SSE shared admission: one lease per subscription, host-local SQLite.

WHY this module exists (log 299 residual, reproduced in log 304 / لاحق 62):
SSE admission state — the EventBus connection caps and the open-rate window —
lived in PROCESS-LOCAL memory. Under ``--workers N`` every process admitted
its own full caps, so the effective caps multiplied (``cap * N``): the same
defect class the request rate limiter had (log 299, fixed in log 303 with a
shared counter). The red reproduction that proves it lives in
``tests/test_sse_multiprocess.py`` (branch ``m8/sse-multiprocess``, merged
only WITH this fix).

THE FIX: one LEASE ROW per admitted subscription in a shared SQLite file
(WAL mode) — the same shared-store pattern as log 303, extended with
liveness:

* ``acquire`` is ONE ``BEGIN IMMEDIATE`` transaction: purge expired leases,
  prune the open-rate window, check the global/tenant/client caps and the
  shared open-rate window, and insert the lease + the open-rate hit only if
  every check passes. The timestamp is sampled AFTER the write lock is held
  (the log-303 regression: a timestamp taken before waiting on the lock can
  be born already expired).
* ``renew`` is CONDITIONAL (``... AND expires_at > now``): it extends a
  still-valid lease and can never resurrect an expired or released one.
* ``release`` is idempotent and used on disconnect/cancellation (the
  generator's ``finally``).
* A worker that dies without releasing (SIGKILL) holds its leases only
  until they EXPIRE (TTL) — its capacity frees by itself, no permanent leak.

The renewal is run by ``renewal_loop``: an INDEPENDENT periodic schedule
(it must not depend on heartbeat output — heartbeats may not fire under a
continuous stream of events or a stalled reader). On a confirmed lease loss
it closes the stream. On a store outage it does NOT fail open: it retries,
bounded, only while the last confirmed lease could still be valid; past
that it releases (the stream ends) rather than hold capacity it cannot
prove it holds.

HONEST limits of this design (documented, not hidden):
- HOST-LOCAL: sharing happens between processes that can open the same FILE
  (one host / a shared volume). Two hosts behind a load balancer still
  multiply the caps — the same bound as log 303, and the same decision is
  owed (Redis/Supabase) when a multi-host deployment exists.
- LEASED ADMISSION, NOT A SOCKET CAP: admission grants a lease; a process
  suspended past its TTL can find its lease expired while its socket is
  still open — the guarantee is "leased active-stream admission with
  liveness", not an absolute count of live sockets. Renewal narrows the
  window to the renewal interval, it cannot close it.
- EVENT DELIVERY IS NOT SHARED: only the ADMISSION limits are shared. Each
  process's EventBus still delivers events to its own local subscribers
  only (the pre-existing, documented behavior).
- FAILURE MODE: a broken store REFUSES admission (fail-closed, 503 at the
  route) — never a silent fallback to process-local caps. There is no
  opt-out flag, matching the log-303 decision.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import secrets
import sqlite3
import tempfile
import time
from typing import Callable, Optional

logger = logging.getLogger("agos.sse.admission")

CAPACITY_RETRY_AFTER = 30         # seconds; when a slot frees is unknown


class SSEAdmissionError(RuntimeError):
    """The shared admission store is unavailable. Callers must REFUSE
    admission (fail-closed) — never fall back to process-local caps or
    unlimited admission."""


class SSEAdmissionRefused(Exception):
    """Admission refused (rate or capacity). ``reason`` is ``open_rate`` or
    ``capacity``; ``retry_after_s`` is whole seconds (>= 1)."""

    def __init__(self, reason: str, retry_after_s: int):
        super().__init__(reason)
        self.reason = reason
        self.retry_after_s = max(1, int(retry_after_s))


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sse_leases (
    lease_id TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    client TEXT NOT NULL,
    opened_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sse_leases_tenant ON sse_leases (tenant);
CREATE INDEX IF NOT EXISTS sse_leases_client ON sse_leases (client);
CREATE TABLE IF NOT EXISTS sse_open_hits (
    client TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sse_open_hits_client_ts ON sse_open_hits (client, ts);
"""


def sse_admission_default_db_path() -> str:
    """The default store location — deliberately OUTSIDE the tracked source
    tree (runtime state, not repo content; a read-only source checkout must
    not break admission). Override: ``AGOS_SSE_ADMISSION_DB``."""
    return os.environ.get("AGOS_SSE_ADMISSION_DB") or os.path.join(
        tempfile.gettempdir(), "agos_sse_admission.sqlite3")


class SSELeaseStore:
    """A host-local shared SSE admission store. One instance per process;
    the SQLite file is the shared state. Safe across threads (a connection
    per call) and across processes (``BEGIN IMMEDIATE`` serializes
    admission)."""

    def __init__(self, db_path: str, busy_timeout_s: float = 2.0,
                 init_retries: int = 10, init_retry_sleep_s: float = 0.2):
        self._db_path = db_path
        self._busy_timeout_ms = max(1, int(busy_timeout_s * 1000))
        self._init_retries = max(1, init_retries)
        self._init_retry_sleep_s = init_retry_sleep_s
        self._init_db()  # loud: a broken store must stop the server

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._db_path, timeout=self._busy_timeout_ms / 1000.0,
            isolation_level=None)
        conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        return conn

    def _init_db(self) -> None:
        """Create the schema, retrying on locked/busy (N workers importing
        against one shared file at the same moment must not crash the one
        that loses the init race — the log-303 observation). Bounded: a
        store that stays busy past the budget is reported loudly."""
        last: Optional[sqlite3.Error] = None
        self._init_attempts = 0
        self._init_busy_failures = 0
        for _attempt in range(self._init_retries):
            self._init_attempts += 1
            try:
                conn = self._connect()
                try:
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.executescript(_SCHEMA)
                finally:
                    conn.close()
                return
            except sqlite3.Error as e:
                msg = str(e).lower()
                if "locked" not in msg and "busy" not in msg:
                    raise SSEAdmissionError(
                        f"SSE admission store unusable at {self._db_path!r} "
                        f"(fix the path or the filesystem — admission does "
                        f"not degrade to process-local caps): {e}") from e
                last = e
                self._init_busy_failures += 1
                time.sleep(self._init_retry_sleep_s)
        raise SSEAdmissionError(
            f"SSE admission store unusable at {self._db_path!r} (still busy "
            f"after {self._init_attempts} init attempts, "
            f"{self._init_busy_failures} of them busy — admission does not "
            f"degrade to process-local caps): {last}") from last

    # ------------------------------------------------------------ admission
    def acquire(self, tenant: str, client: str, *, max_connections: int,
                max_per_tenant: int, max_per_client: int, open_rate: int,
                open_window_s: float, lease_ttl_s: float) -> str:
        """ONE atomic admission decision: purge expired leases, prune the
        open-rate window, check the shared open-rate window and the
        global/tenant/client caps, and insert ONE lease row (plus the
        open-rate hit) only if every check passes — all inside a single
        ``BEGIN IMMEDIATE`` transaction, so concurrent processes can never
        admit past a cap together.

        The open-rate hit is recorded even when the open is then refused
        for capacity (parity with the in-process semantics: the rate check
        runs first and counts the attempt). Returns ``(lease_id,
        expires_at)`` — the id is unpredictable and the expiry is the
        expiry written by THIS transaction (the caller's renewal deadline
        is derived from it, never from a local clock guess). Raises
        ``SSEAdmissionRefused`` (no lease inserted) or
        ``SSEAdmissionError`` (store unusable — the caller must refuse,
        fail-closed)."""
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                now = time.time()  # AFTER the write lock (log-303 regression)
                conn.execute("DELETE FROM sse_leases WHERE expires_at<=?",
                             (now,))
                conn.execute("DELETE FROM sse_open_hits WHERE ts<=?",
                             (now - open_window_s,))
                n, oldest = conn.execute(
                    "SELECT COUNT(*), MIN(ts) FROM sse_open_hits WHERE client=?",
                    (client,)).fetchone()
                if n >= open_rate:
                    conn.execute("COMMIT")
                    wait = 1 if oldest is None else max(
                        1, math.ceil(oldest + open_window_s - now))
                    raise SSEAdmissionRefused("open_rate", int(wait))
                total = conn.execute("SELECT COUNT(*) FROM sse_leases").fetchone()[0]
                tcount = conn.execute(
                    "SELECT COUNT(*) FROM sse_leases WHERE tenant=?",
                    (tenant,)).fetchone()[0]
                ccount = conn.execute(
                    "SELECT COUNT(*) FROM sse_leases WHERE client=?",
                    (client,)).fetchone()[0]
                if (total >= max_connections or tcount >= max_per_tenant
                        or ccount >= max_per_client):
                    conn.execute("INSERT INTO sse_open_hits (client, ts) VALUES (?, ?)",
                                 (client, now))
                    conn.execute("COMMIT")
                    raise SSEAdmissionRefused("capacity", CAPACITY_RETRY_AFTER)
                lease_id = secrets.token_urlsafe(16)
                expires_at = now + lease_ttl_s
                conn.execute(
                    "INSERT INTO sse_leases (lease_id, tenant, client, opened_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (lease_id, tenant, client, now, expires_at))
                conn.execute("INSERT INTO sse_open_hits (client, ts) VALUES (?, ?)",
                             (client, now))
                conn.execute("COMMIT")
                return lease_id, expires_at
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise SSEAdmissionError(
                f"SSE admission store acquire failed for tenant/client "
                f"(refusing — never unlimited): {e}") from e

    # ------------------------------------------------------------- liveness
    def renew(self, lease_id: str, ttl_s: float) -> Optional[float]:
        """CONDITIONAL extension: only a still-valid lease is extended
        (``expires_at > now``); an expired or released lease is never
        resurrected. Returns the NEW expiry written by this transaction
        (the renewal deadline is derived from it), or ``None`` = the lease
        is gone (confirmed loss). A store outage raises
        ``SSEAdmissionError`` — distinct from a loss, so the caller can
        retry in a bounded way."""
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                now = time.time()
                expires_at = now + ttl_s
                cur = conn.execute(
                    "UPDATE sse_leases SET expires_at=? "
                    "WHERE lease_id=? AND expires_at>?",
                    (expires_at, lease_id, now))
                ok = cur.rowcount == 1
                conn.execute("COMMIT")
                return expires_at if ok else None
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise SSEAdmissionError(
                f"SSE admission store renew failed (type only): {e}") from e

    def release(self, lease_id: str) -> bool:
        """Release a lease (disconnect/cancellation). Idempotent: ``True``
        only the first time."""
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute("DELETE FROM sse_leases WHERE lease_id=?",
                                   (lease_id,))
                ok = cur.rowcount == 1
                conn.execute("COMMIT")
                return ok
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise SSEAdmissionError(
                f"SSE admission store release failed (type only): {e}") from e

    # -------------------------------------------------------------- tests
    def counts(self) -> dict:
        """Live lease count (diagnostics/tests — not an admission path)."""
        try:
            conn = self._connect()
            try:
                n = conn.execute("SELECT COUNT(*) FROM sse_leases").fetchone()[0]
                return {"leases": n}
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise SSEAdmissionError(f"count failed: {e}") from e

    def reset(self) -> None:
        """Drop every lease and open-rate hit (tests: a clean state)."""
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM sse_leases")
                conn.execute("DELETE FROM sse_open_hits")
                conn.execute("COMMIT")
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise SSEAdmissionError(f"reset failed: {e}") from e


async def renewal_loop(lease_id: str, store: SSELeaseStore, ttl_s: float,
                       interval_s: float, on_loss: Callable[[], None],
                       valid_until: float) -> None:
    """The INDEPENDENT periodic lease renewal (it must not depend on
    heartbeat output: heartbeats may not fire under a continuous stream of
    events or a stalled reader).

    ``valid_until`` is the expiry written by the LAST CONFIRMED transaction
    (acquisition or renewal — never a local clock guess); the loop must
    not let the stream run past it while unconfirmed:

    * the next wake is scheduled at ``min(now + interval, valid_until)`` —
      an outage is decided BEFORE the deadline passes, never discovered
      after waking late past it (the plain-interval sleep could overshoot:
      fail at t=interval with ttl < 2*interval, wake at 2*interval — past
      the deadline);
    * a successful renewal returns the NEW expiry and re-arms the deadline;
    * a confirmed loss (``renew`` → None) closes the stream;
    * a store OUTAGE does not fail open: the loop retries, bounded by the
      deadline — the wake is capped at ``valid_until``, so the retry budget
      is consumed inside the validity window and the close decision is made
      no later than it.

    The store calls run OFF the event loop (``asyncio.to_thread``): a lock
    wait inside SQLite must never block the loop that serves the stream.
    ``on_loss`` runs off-loop too (it may touch the store).

    The interval must be much shorter than the TTL (a startup-time
    validation in ``SSELimits``); the numbers are operational defaults, not
    a verified safety bound."""
    while True:
        wake_at = min(time.time() + interval_s, valid_until)
        await asyncio.sleep(max(0.0, wake_at - time.time()))
        try:
            expiry = await asyncio.to_thread(store.renew, lease_id, ttl_s)
        except SSEAdmissionError as e:
            logger.warning("sse: lease renewal store unavailable (%s) — "
                           "deciding before the last confirmed expiry "
                           "passes", type(e).__name__)
            if time.time() >= valid_until:
                await asyncio.to_thread(on_loss)
                return
            continue  # bounded: the next wake is capped at valid_until
        if expiry is None:
            await asyncio.to_thread(on_loss)
            return
        valid_until = expiry
