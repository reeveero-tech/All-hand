"""R-164 rate limiting: a SHARED admission counter (host-local SQLite).

WHY this module exists (log 299): ``check_rate_limit`` used to count hits in
process-local memory (``request_counts``). Under ``--workers N`` (or any
multi-process deployment) every process counted its OWN window, so the real
limit was ``RATE_LIMIT_REQUESTS * N`` — a real deployment limit, silently
multiplied. The red test that proved it lives in
``tests/test_rate_limit_multiprocess.py`` (branch ``m8/rate-limit-multiprocess``,
commit ``b061663e``; reproduced in log 293/299).

THE FIX: one counter per HOST, shared by every process on that host through
a SQLite file (WAL mode). Admission is a single transaction
(``BEGIN IMMEDIATE``: prune + count + insert), so the check-then-admit step
is atomic across processes — N processes admit EXACTLY the configured limit
in a shared window, never ``limit * N``.

HONEST limits of this design (documented, not hidden):
- HOST-LOCAL: two hosts behind one load balancer still multiply the limit
  (each host counts its own window). The contract entity stays the CLIENT
  ADDRESS as the server sees it — behind a reverse proxy that is the proxy,
  not the end client (X-Forwarded-For is NOT trusted unless a future batch
  explicitly configures it). When a multi-HOST deployment exists, the store
  needs a decision (Redis/Supabase) — that is a deployment change, not a
  code change, and is NOT made silently here.
- PERSISTENCE: the file survives restarts (the window is short; stale hits
  are pruned on the next check). The file is NOT in the tracked source
  tree (default: the OS temp dir; override: ``AGOS_RATE_LIMIT_DB``).
- FAILURE MODE: if the store is unavailable (lock timeout, I/O error) the
  check REFUSES the request (fail-closed, 503 + Retry-After) — the limiter
  never silently degrades to unlimited. There is NO opt-out flag: a
  deployment that cannot keep the store up must fix the store.
"""
from __future__ import annotations

import math
import os
import sqlite3
import tempfile
import time
from typing import Tuple


class RateLimitStoreError(RuntimeError):
    """The shared rate-limit counter is unavailable. Callers must refuse the
    request (fail-closed) — never fall back to process-local counting or
    unlimited admission."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS rate_limit_hits (
    client_id TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS rate_limit_hits_client_ts
    ON rate_limit_hits (client_id, ts);
"""


def default_db_path() -> str:
    """The default store location — deliberately OUTSIDE the tracked source
    tree (a rate-limit DB is runtime state, not repo content; a read-only
    source checkout must not break the limiter)."""
    return os.environ.get("AGOS_RATE_LIMIT_DB") or os.path.join(
        tempfile.gettempdir(), "agos_rate_limit.sqlite3")


class RateLimitStore:
    """A host-local shared admission counter. One instance per process; the
    SQLite file is the shared state. Safe across threads (a connection per
    call) and across processes (BEGIN IMMEDIATE serializes admission)."""

    def __init__(self, db_path: str, busy_timeout_s: float = 2.0,
                 init_retries: int = 10, init_retry_sleep_s: float = 0.2):
        self._db_path = db_path
        self._busy_timeout_ms = max(1, int(busy_timeout_s * 1000))
        # init retry budget: an ATTEMPT LIMIT plus the sleep between attempts
        # (NOT a wall-clock promise — each attempt can also spend its whole
        # busy_timeout waiting on the lock before failing). Test knobs: small
        # budgets keep the persistent-busy regression fast.
        self._init_retries = max(1, init_retries)
        self._init_retry_sleep_s = init_retry_sleep_s
        self._init_db()  # loud: a broken store must stop the server, not run unlimited

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self._db_path, timeout=self._busy_timeout_ms / 1000.0,
            isolation_level=None)
        conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        return conn

    def _init_db(self) -> None:
        """Create the schema, RETRYING on `database is locked`/`busy`: N workers
        importing this module against one shared file at the same moment must
        not crash whichever of them loses the init race (observed in the log:
        concurrent startup hit `database is locked` during init). Bounded: at
        most ``init_retries`` attempts, ``init_retry_sleep_s`` apart — a store
        that stays busy past that budget is reported loudly, never silently
        run without a limit."""
        last: sqlite3.Error | None = None
        for _attempt in range(self._init_retries):
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
                    raise RateLimitStoreError(
                        f"rate-limit store unusable at {self._db_path!r} "
                        f"(fix the path or the filesystem — the limiter does not "
                        f"degrade to unlimited): {e}") from e
                last = e
                time.sleep(self._init_retry_sleep_s)
        raise RateLimitStoreError(
            f"rate-limit store unusable at {self._db_path!r} (still busy after "
            f"{self._init_retries} init attempts — the limiter does not degrade "
            f"to unlimited): {last}") from last

    def check(self, client_id: str, limit: int, window_s: float) -> Tuple[bool, int]:
        """One atomic admission decision: prune the expired hits for this
        client, count the live ones, and admit only if the count is below
        the limit — all inside a single ``BEGIN IMMEDIATE`` transaction, so
        concurrent processes can never admit past the cap together.

        Returns ``(allowed, retry_after_seconds)``. ``retry_after_seconds``
        is the ceiling of (oldest hit + window - now), at least 1 — the same
        number the in-process counter used to send.

        The window timestamp is sampled **after** ``BEGIN IMMEDIATE``
        acquires the writer lock, never before: a check that WAITED for the
        lock must not insert a hit whose timestamp was taken before the wait
        (it could be born already expired — the next check would prune it and
        the limit would never hold). Regression-tested below.
        """
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                now = time.time()
                conn.execute(
                    "DELETE FROM rate_limit_hits WHERE client_id=? AND ts<=?",
                    (client_id, now - window_s))
                count, oldest = conn.execute(
                    "SELECT COUNT(*), MIN(ts) FROM rate_limit_hits WHERE client_id=?",
                    (client_id,)).fetchone()
                if count >= limit:
                    conn.execute("COMMIT")
                    wait = 1 if oldest is None else max(
                        1, math.ceil(oldest + window_s - now))
                    return False, int(wait)
                conn.execute(
                    "INSERT INTO rate_limit_hits (client_id, ts) VALUES (?, ?)",
                    (client_id, now))
                conn.execute("COMMIT")
                return True, 0
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise RateLimitStoreError(
                f"rate-limit store check failed for {client_id!r} "
                f"(refusing — never unlimited): {e}") from e

    def reset(self) -> None:
        """Drop every counted hit (tests: a clean window between cases)."""
        try:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("DELETE FROM rate_limit_hits")
                conn.execute("COMMIT")
            finally:
                conn.close()
        except sqlite3.Error as e:
            raise RateLimitStoreError(f"rate-limit store reset failed: {e}") from e
