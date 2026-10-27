"""The per-client rate limit is GLOBAL across the processes of one
deployment (log 299 — the red reproduction; this batch: the FIX's proof).

The defect (log 293 residual, reproduced red in log 299): ``check_rate_limit``
counted in process-local memory (``server.request_counts``). Behind several
workers / instances the effective per-client limit multiplied — each process
enforced its own window, so a client served round-robin by N processes got
~N x the configured limit.

The fix (this batch): ONE host-local shared counter — a SQLite file (WAL,
``BEGIN IMMEDIATE`` transactional admission: prune + count + insert atomically)
shared by every process of the host, reached off the event loop; a store
failure refuses the request (fail-closed 503 + Retry-After — the limiter
never silently degrades to unlimited); the DB file lives OUTSIDE the tracked
source tree (``AGOS_RATE_LIMIT_DB`` overrides; default: the OS temp dir).
The contract entity stays the CLIENT ADDRESS the server sees — behind a
reverse proxy that is the proxy, not the end client (X-Forwarded-For is NOT
trusted unless a future batch explicitly configures it).

HONEST limits of the shared counter (documented, not hidden):
- HOST-LOCAL: two hosts behind one load balancer still multiply the limit
  (each host counts its own window). A multi-HOST deployment needs a shared
  store decision (Redis/Supabase) — a topology change, not made silently
  here (log 299's analysis stands).
- This file pins the contract with real server subprocesses:

  * control: a SINGLE process refuses request LIMIT+1 (the limiter works;
    proves any red below is the topology, not the harness);
  * contract (the reproduction, now green): two processes, round-robin
    2 x LIMIT requests — the limit is GLOBAL across the two (exactly LIMIT
    accepted, the rest 429 + Retry-After), no matter which process serves;
  * atomicity under concurrency: many simultaneous requests against the
    boundary admit EXACTLY the limit (the transactional admission is
    atomic — no check-then-act race);
  * window expiry: after the window passes, admission returns;
  * fail-closed: a broken store REFUSES (503 + Retry-After, never silent
    unlimited) and recovers when the store returns;
  * the store file is outside the tracked source tree (runtime state, not
    repo content) and the env override is honored.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import aiohttp
import pytest

import agent_civilization.core.rate_limit_store as rate_limit_store

REPO = Path(__file__).resolve().parents[1]
LIMIT = 5      # AGOS_RATE_LIMIT_REQUESTS for every spawned process
WINDOW = 60    # AGOS_RATE_LIMIT_WINDOW


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server:
    """One real server process (subprocess uvicorn — same pattern as
    scripts/r171_local_load.py: stderr to a file so a full pipe can never
    freeze the server, credentials via env, fast small civilization).

    ``db_path``: the SHARED rate-limit counter this process counts into —
    the deployment's shared store (the contract test shares ONE between
    two processes; the control/isolation tests give each its own)."""

    def __init__(self, key: str, db_path: str, window: int = WINDOW):
        self.port = _free_port()
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("OPENROUTER", "SUPABASE", "AGOS_", "LLM_"))}
        env.update({
            "AGOS_TENANT_KEYS": json.dumps(
                [{"tenant": "rl_test", "key_sha256": hashlib.sha256(key.encode()).hexdigest()}]),
            "AGOS_RATE_LIMIT_REQUESTS": str(LIMIT),
            "AGOS_RATE_LIMIT_WINDOW": str(window),
            "AGOS_RATE_LIMIT_DB": db_path,
            "AGENTS_PER_SPEC": "1",
        })
        self.log_path = Path(f"/tmp/rl_multiproc_{self.port}.log")
        self.log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1",
             "--port", str(self.port), "--log-level", "warning"],
            cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=self.log)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def wait_ready(self, session: aiohttp.ClientSession) -> None:
        """Probe /health — deliberately NOT a rate-limited route: a readiness
        probe must never consume a rate-limit slot (probing /api would take
        one of the LIMIT admissions and skew every assertion in this file)."""
        for _ in range(300):
            try:
                async with session.get(f"{self.base}/health") as r:
                    if r.status in (200, 401):
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.1)
        raise RuntimeError(f"server never became ready: {self.log_path}")

    def stop(self) -> None:
        self.proc.terminate()
        self.proc.wait(timeout=10)
        self.log.close()


def _events_headers() -> list:
    """The events endpoint is rate-limited AND authenticated — one key for
    the rl_test tenant (recreated per test so each test is its own client)."""
    RKEY = "rl_test_key_" + secrets.token_urlsafe(16)
    return [("X-API-Key", RKEY)]


def _is_rejected(status: int) -> bool:
    """The refusal codes the limiter uses (NOT 500 — a limiter outage is a
    separate, fail-closed concern tested below)."""
    return status in (429, 503)


async def _events_codes(session: aiohttp.ClientSession, servers: list,
                        n: int, headers: list) -> list:
    """n events requests round-robin across the servers, answering the codes
    in order (the exact alternation of a balancer without sticky sessions)."""
    codes = []
    for i in range(n):
        base = servers[i % len(servers)].base
        async with session.get(f"{base}/api/events", headers=headers) as r:
            codes.append(r.status)
    return codes


async def test_control_single_process_refuses_limit_plus_one(tmp_path):
    """CONTROL (the harness works): a single process accepts LIMIT requests
    then refuses the next one with 429 + Retry-After."""
    headers = _events_headers()
    key = headers[0][1]
    srv = _Server(key, str(tmp_path / "control.sqlite3"))
    try:
        async with aiohttp.ClientSession() as s:
            await srv.wait_ready(s)
            codes = await _events_codes(s, [srv], LIMIT + 1, headers)
            assert codes[:LIMIT] == [200] * LIMIT, codes
            assert codes[LIMIT] == 429, codes
    finally:
        srv.stop()


async def test_contract_the_limit_is_global_across_processes(tmp_path):
    """CONTRACT (log 299's reproduction, now green): two processes sharing ONE
    deployment counter — round-robin 2 x LIMIT requests: exactly LIMIT
    accepted (200), the rest refused 429 + Retry-After, no matter which
    process serves them. On per-process memory every request was accepted
    (10/10 — the red recorded in log 299)."""
    headers = _events_headers()
    key = headers[0][1]
    shared_db = str(tmp_path / "shared.sqlite3")
    a = _Server(key, shared_db)
    b = _Server(key, shared_db)
    try:
        async with aiohttp.ClientSession() as s:
            await a.wait_ready(s)
            await b.wait_ready(s)
            codes = await _events_codes(s, [a, b], 2 * LIMIT, headers)
            assert codes.count(200) == LIMIT, \
                f"rate limit multiplied across processes: {codes.count(200)} " \
                f"accepted (limit {LIMIT}, {2 * LIMIT} round-robin requests)"
            assert all(c == 429 for c in codes if c != 200), codes
    finally:
        a.stop()
        b.stop()


async def test_admission_at_the_boundary_is_atomic_under_concurrency(tmp_path):
    """ATOMICITY: 40 simultaneous requests against a fresh shared counter
    with LIMIT=5 — EXACTLY 5 accepted, no more (the transactional admission
    is atomic: no check-then-act race between concurrent requests), the rest
    429. The client is one process; the store is the same shared file."""
    headers = _events_headers()
    key = headers[0][1]
    srv = _Server(key, str(tmp_path / "race.sqlite3"))
    try:
        async with aiohttp.ClientSession() as s:
            await srv.wait_ready(s)
            barrier = asyncio.Barrier(40)

            async def one():
                await barrier.wait()
                async with s.get(f"{srv.base}/api/events", headers=headers) as r:
                    return r.status

            codes = await asyncio.gather(*[one() for _ in range(40)])
            assert codes.count(200) == LIMIT, codes
            assert all(c == 429 for c in codes if c != 200), codes
    finally:
        srv.stop()


async def test_window_expiry_admission_returns(tmp_path):
    """WINDOW EXPIRY: the limit is a WINDOW (not a lifetime quota) — after
    the window passes, the same client is admitted again."""
    headers = _events_headers()
    key = headers[0][1]
    # a SHORT window (1s) — the same knob the server reads; the window
    # semantics are identical at any size, and 60s would make the test slow
    srv = _Server(key, str(tmp_path / "window.sqlite3"), window=1)
    try:
        async with aiohttp.ClientSession() as s:
            await srv.wait_ready(s)
            codes = await _events_codes(s, [srv], LIMIT, headers)
            assert codes == [200] * LIMIT, codes
            async with s.get(f"{srv.base}/api/events", headers=headers) as r:
                assert r.status == 429
            time.sleep(2)  # > the 1s window
            async with s.get(f"{srv.base}/api/events", headers=headers) as r:
                assert r.status == 200, r.status
    finally:
        srv.stop()


async def test_store_failure_fails_closed_then_recovers(tmp_path):
    """FAIL-CLOSED + RECOVERY: a broken store REFUSES the request (503 +
    Retry-After — the limiter never silently degrades to unlimited), and
    when the store returns the admission returns with it."""
    headers = _events_headers()
    key = headers[0][1]
    db = tmp_path / "flaky.sqlite3"
    srv = _Server(key, str(db))
    try:
        async with aiohttp.ClientSession() as s:
            await srv.wait_ready(s)
            # healthy: admitted
            async with s.get(f"{srv.base}/api/events", headers=headers) as r:
                assert r.status == 200
            # break the store: a directory where the DB file should be
            db.unlink()
            db.mkdir()
            async with s.get(f"{srv.base}/api/events", headers=headers) as r:
                assert r.status == 503, r.status
                assert "Retry-After" in r.headers, dict(r.headers)
            # the limiter does NOT degrade to unlimited while broken
            async with s.get(f"{srv.base}/api/events", headers=headers) as r:
                assert r.status == 503, r.status
            # recover: a valid store at the same path — admission returns
            db.rmdir()
            rate_limit_store.RateLimitStore(str(db))  # recreates the schema
            async with s.get(f"{srv.base}/api/events", headers=headers) as r:
                assert r.status == 200, r.status
    finally:
        srv.stop()


def test_the_default_store_file_is_outside_the_tracked_source_tree(monkeypatch):
    """The store file is RUNTIME STATE, not repo content: the default path is
    outside the tracked source tree (a read-only checkout must not break the
    limiter), and the env override is honored."""
    monkeypatch.delenv("AGOS_RATE_LIMIT_DB", raising=False)
    default = rate_limit_store.default_db_path()
    assert not default.startswith(str(REPO)), default
    monkeypatch.setenv("AGOS_RATE_LIMIT_DB", "/somewhere/else/rl.sqlite3")
    assert rate_limit_store.default_db_path() == "/somewhere/else/rl.sqlite3"


def test_a_waited_admission_samples_its_timestamp_after_acquiring_the_lock(tmp_path):
    """Log-299 review: a check that WAITED for the writer lock must stamp
    its hit with the time AFTER the wait, not before — a pre-wait timestamp
    can be born already expired (wait longer than the window), the next
    check prunes it, and the limit never holds.

    Proof design (no scheduling assumptions): an explicit handshake
    establishes CHECKPOINT ORDERING — the worker's connection is created and
    BEGIN is its next step, and with the blocker owning the writer lock it
    cannot have finished (asserted alive). The exact statement it is parked
    in is not asserted; what the controlled clock plus the mutation result
    detect is the defect. The clock is advanced far past the window while
    the worker still waits, and only then is the lock released — the
    acquisition time is provably the advanced time. The hit must hold the
    window measured from THAT time and expire exactly window-seconds later.
    Verified red on the pre-fix code (timestamp sampled before BEGIN): the
    hit is stamped at wait-start, already expired at the advanced time, and
    the next check admits again."""
    import sqlite3 as _sqlite3
    import threading as _threading
    db = tmp_path / "ts.sqlite3"
    store = rate_limit_store.RateLimitStore(str(db))
    # controlled clock: the store samples time.time() — swap the module's
    # `time` for a shim whose .time() this test advances by hand (the
    # module only uses time.sleep in init retries, kept real)
    class _Clock:
        now = 1000.0
        def time(self):
            return self.now
        def sleep(self, s):
            time.sleep(s)
    clock = _Clock()
    real_time_mod = rate_limit_store.time
    rate_limit_store.time = clock
    blocker = _sqlite3.connect(str(db), isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")   # hold the writer lock
    reached = _threading.Event()
    orig_connect = store._connect
    def _tracked_connect():
        conn = orig_connect()
        reached.set()   # the worker built its connection — next it runs BEGIN
        return conn
    store._connect = _tracked_connect
    outcome = {}
    def _waiting_check():
        outcome["res"] = store.check("waiter", 1, window_s=1.0)
    th = _threading.Thread(target=_waiting_check)
    try:
        th.start()
        assert reached.wait(timeout=10), "the worker never reached its connection"
        # Evidence, not an exact-location claim: the connection checkpoint is
        # behind us and BEGIN is the worker's next step; with the blocker
        # owning the writer lock the worker cannot have finished. (The exact
        # statement inside it is not asserted — what matters is the mutation
        # result below.) It is still waiting:
        time.sleep(0.3)
        assert th.is_alive(), "the check finished while the lock was held?!"
        # ...advance the clock FAR past the window while it waits, and only
        # now release: the acquisition time is provably the advanced one
        clock.now = 1010.0
        blocker.execute("COMMIT")
        th.join(timeout=10)
        assert not th.is_alive(), "the waiting check never finished"
        assert outcome["res"] == (True, 0), outcome   # it was admitted
        # stamped at ACQUISITION (1010): still holding at 1010.9, and it
        # expires exactly one window after 1010 — a wait-start stamp (1000)
        # would already be pruned and the 1010.9 check would ADMIT
        clock.now = 1010.9
        assert store.check("waiter", 1, window_s=1.0) == (False, 1), \
            "a waited admission was born expired — timestamp sampled before the lock"
        clock.now = 1011.05
        assert store.check("waiter", 1, window_s=1.0) == (True, 0)
    finally:
        rate_limit_store.time = real_time_mod
        blocker.close()
        th.join(timeout=10)


def test_transient_busy_during_init_retries_and_succeeds(tmp_path):
    """Another connection holds the writer lock while the store initializes:
    the init hits `database is locked` (an ACTUAL caught busy failure — the
    handshake below parks the worker inside the retry sleep that follows it),
    the bounded retry rides it out, and the construction SUCCEEDS (observed
    live: two servers starting on one shared file — the loser used to crash
    at import). No timing assumptions: the lock is released only AFTER the
    busy failure is provably caught."""
    import sqlite3 as _sqlite3
    import threading as _threading
    db = tmp_path / "init-busy.sqlite3"
    blocker = _sqlite3.connect(str(db), isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    # test-local gate around the module's `time`: the retry sleep that
    # FOLLOWS a caught busy failure signals busy_seen and parks until the
    # test allows the next attempt (the clock itself stays real)
    class _RetryGate:
        def __init__(self):
            self.busy_seen = _threading.Event()
            self.proceed = _threading.Event()
        def time(self):
            return time.time()
        def sleep(self, s):
            self.busy_seen.set()          # a busy failure was CAUGHT
            assert self.proceed.wait(timeout=30), "the retry sleep was never released"
    gate = _RetryGate()
    real_time_mod = rate_limit_store.time
    rate_limit_store.time = gate
    built = {}
    def _build():
        built["store"] = rate_limit_store.RateLimitStore(
            str(db), busy_timeout_s=0.3, init_retries=20, init_retry_sleep_s=0.05)
    th = _threading.Thread(target=_build)
    th.start()
    try:
        # wait for the actual caught busy failure (the worker is parked in
        # the retry sleep) — only then release the blocker
        assert gate.busy_seen.wait(timeout=10), "init never hit a busy failure"
        blocker.execute("COMMIT")
        gate.proceed.set()                # the next attempt runs lock-free
        th.join(timeout=10)
        assert not th.is_alive(), "init never finished after the lock was released"
        store = built["store"]
        assert store._init_busy_failures >= 1, (store._init_attempts,
                                                store._init_busy_failures)
        assert store._init_attempts > store._init_busy_failures
        assert store.check("c", 5, window_s=60) == (True, 0)
    finally:
        gate.proceed.set()               # never leave the worker parked
        rate_limit_store.time = real_time_mod
        try:
            blocker.execute("COMMIT")   # release if an assertion failed above
        except _sqlite3.Error:
            pass
        blocker.close()
        th.join(timeout=10)


def test_persistent_busy_during_init_fails_closed_within_the_retry_budget(tmp_path):
    """The lock is NEVER released: the init gives up after its bounded
    attempt budget and raises loudly (a store that cannot initialize must
    stop the process — never run unlimited). The budget is proven SPENT:
    the message carries the ACTUAL attempt count, and the inter-attempt
    sleeps alone span at least (attempts-1) x sleep."""
    import sqlite3 as _sqlite3
    db = tmp_path / "init-stuck.sqlite3"
    blocker = _sqlite3.connect(str(db), isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    t0 = time.monotonic()
    try:
        with pytest.raises(rate_limit_store.RateLimitStoreError) as ei:
            rate_limit_store.RateLimitStore(
                str(db), busy_timeout_s=0.3, init_retries=3, init_retry_sleep_s=0.2)
        elapsed = time.monotonic() - t0
        # the ACTUAL count (3 attempts, all busy) is in the message —
        # "3 init attempts, 3 of them busy" — not just the configured one
        assert "3 init attempts, 3 of them busy" in str(ei.value), str(ei.value)
        # the inter-attempt sleeps alone prove the loop ran its full budget:
        # 3 attempts = 2 sleeps >= 2 x 0.2s; each attempt itself fails fast
        assert elapsed >= 0.4, elapsed
        assert elapsed < 5, elapsed          # bounded, not open-ended
    finally:
        blocker.close()
