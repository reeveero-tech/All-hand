"""R-188 (log 302): the task's TERMINAL-state write is retried — bounded,
jittered, loud on exhaustion, and DEFERRED (never orphaned) past the budget.

Proven by the 500-task volatile load test
(``tests/test_r188_operational_limits.py``, this batch): under the
completion storm the storage circuit breaker opened (5 failures / 10s
reset), ``update_task`` failed SOFT (it catches every storage error —
including ``CircuitOpen`` — and returns False) and the completion site
IGNORED the return value, so a handful of rows stayed ``running`` FOREVER:
the effect had happened and the record lost it — no retry, no
reconciliation in the volatile mode (the durable queue has its own).

``server._write_task_terminal`` is the fix; this file pins its contract
deterministically (in-process, no load needed):

1. a TRANSIENT soft failure (the breaker's exact shape) no longer orphans
   the row — the write is retried until it lands (attempts observed, no
   CRITICAL);
2. an EXHAUSTED budget is LOUD and DEFERRED, not orphaned: CRITICAL names
   the task and its state, exactly the configured number of in-line
   attempts were made (never silent, never a hot loop), and the write is
   handed to the background drainer (``TERMINAL_WRITE_PENDING``);
3. the deferral CONVERGES: once storage recovers, the drainer lands the
   write and empties the map — a living server never leaves its own
   rows ``running`` forever (round 3: reproduced under verified external
   saturation — 35 of 500 rows were stuck at the 600s ceiling before the
   drainer existed);
4. the completion SITE routes the terminal write through the retry: a real
   volatile task whose storage fails the terminal write twice still lands
   ``completed`` (the call-site proof — the load test can only show it
   under its storm).

Honest limits: the deferral is IN-MEMORY — a server restart still loses
the pending writes (the volatile mode's documented durability bound; the
durable queue is the no-loss guarantee). The pending map has NO
configured memory bound: entries accumulate for as long as an outage
lasts across the server's lifetime (each carries the task's result
payload). Reconciliation after recovery is eventual, never immediate,
and implies nothing about exactly-once execution.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time

import pytest

import server
from tests.test_r163_durable_queue import _client, _wait_for

sys.path.insert(0, "tests")
import test_r163_durable_queue as r163  # noqa: E402

tenants = r163.tenants          # re-export: the fixture requests


@pytest.fixture(autouse=True)
async def _clean_deferrals(monkeypatch):
    """Keep the module-level deferral state test-local: clear the pending
    map before/after each test and cancel a live drainer task ON ITS OWN
    LOOP before that loop closes. The task is retrieved AFTER the test
    (the drainer the TEST started, not whatever existed before it); it is
    only AWAITED when it belongs to THIS fixture's running loop (a task
    from a loop that already closed — the sync two-loop test's own
    responsibility — is only cancel-marked here; awaiting it from another
    loop is impossible). Depends on `monkeypatch` explicitly so this
    teardown runs BEFORE monkeypatch restoration could hide the task.
    The map is cleared last so a cancelled drainer's in-flight round finds
    no entries to touch."""
    server.TERMINAL_WRITE_PENDING.clear()
    yield
    task = server._TERMINAL_WRITE_DRAIN_TASK
    if task is not None and not task.done():
        task.cancel()
        if task.get_loop() is asyncio.get_running_loop() \
                and not task.get_loop().is_closed():
            try:
                await task
            except asyncio.CancelledError:
                pass
    server.TERMINAL_WRITE_PENDING.clear()
    server._TERMINAL_WRITE_DRAIN_TASK = None


# ------------------------------------------------------------- unit: retry

class _FlakyStorage:
    """``update_task`` fails the first N calls then succeeds — the breaker's
    shape (soft failure: False, never an exception)."""

    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    async def update_task(self, task_id, status, **kw):
        self.calls += 1
        return self.calls > self.failures


@pytest.fixture
def fast_retry(monkeypatch):
    """Shrink the retry sleeps (the budget SHAPE is under test, not the
    wall-clock; the production values are in the server's block comment)."""
    monkeypatch.setattr(server, "TASK_TERMINAL_WRITE_MAX_WAIT_S", 0.01)


async def test_transient_soft_failure_is_retried_not_orphaned(fast_retry, monkeypatch):
    """REQUIRED behavior: a transient soft failure (the breaker's shape)
    does not orphan the row — the write is retried until it lands, with no
    CRITICAL (the failure was recovered, not lost)."""
    flaky = _FlakyStorage(failures=2)
    monkeypatch.setattr(server, "get_storage", lambda: flaky)
    ok = await server._write_task_terminal(
        "retry-1", "completed", agent_name="a", result={"ok": True})
    assert ok is True
    assert flaky.calls == 3          # 2 failed + 1 landed — retried, not dropped


async def test_exhausted_budget_is_loud_bounded_and_deferred(fast_retry, caplog, monkeypatch):
    """REQUIRED behavior: an exhausted budget is LOUD (CRITICAL names the
    task and its state — visible, never silent), makes EXACTLY the
    configured number of IN-LINE attempts (bounded, never a hot loop),
    and DEFERS the write to the background drainer instead of orphaning
    the row forever."""
    monkeypatch.setattr(server, "TASK_TERMINAL_WRITE_ATTEMPTS", 3)
    never = _FlakyStorage(failures=10 ** 9)
    monkeypatch.setattr(server, "get_storage", lambda: never)
    with caplog.at_level(logging.CRITICAL):
        ok = await server._write_task_terminal("orphan-1", "failed")
    assert ok is False
    assert never.calls == 3           # exactly the in-line budget, no more
    assert any("NOT persisted" in r.message and "orphan-1" in r.message
               for r in caplog.records), \
        "a deferred terminal write must be CRITICAL-logged, never silent"
    # DEFERRED, not orphaned: the exact write is pending for the drainer
    assert server.TERMINAL_WRITE_PENDING.get("orphan-1") == {
        "status": "failed",
        "kw": {"agent_name": None, "result": None, "elapsed_s": None}}
    assert server._TERMINAL_WRITE_DRAIN_TASK is not None, \
        "the exhaustion must have started the background drainer"


async def test_deferred_write_lands_when_storage_recovers(fast_retry, monkeypatch):
    """REQUIRED behavior (round 3 — the saturation repro): a write that
    exhausts the in-line budget during a sustained outage CONVERGES once
    storage recovers — the drainer keeps retrying the exact deferred
    payload until it lands, then empties the map. A living server never
    leaves its own rows ``running`` forever."""
    monkeypatch.setattr(server, "TASK_TERMINAL_WRITE_ATTEMPTS", 2)
    monkeypatch.setattr(server, "TERMINAL_WRITE_DRAIN_INTERVAL_S", 0.05)
    rec = _FlakyStorage(failures=6)   # 2 in-line + 4 drainer rounds, then up
    monkeypatch.setattr(server, "get_storage", lambda: rec)
    ok = await server._write_task_terminal(
        "defer-1", "completed", agent_name="a", result={"ok": True},
        elapsed_s=1.5)
    assert ok is False
    assert "defer-1" in server.TERMINAL_WRITE_PENDING
    deadline = time.time() + 15
    while time.time() < deadline:
        if "defer-1" not in server.TERMINAL_WRITE_PENDING:
            break
        await asyncio.sleep(0.02)
    assert "defer-1" not in server.TERMINAL_WRITE_PENDING, \
        "the drainer never landed the deferred write " \
        f"(calls={rec.calls}, pending={server.TERMINAL_WRITE_PENDING})"
    assert rec.calls > 6             # the drainer kept trying past the budget
    # the drainer self-terminates once the map is empty (no idle loop)
    await asyncio.sleep(0.2)
    assert server._TERMINAL_WRITE_DRAIN_TASK.done()


# ------------------------------------------- drainer resilience (round 3)

class _QueueStorage:
    """A storage whose ARMED calls are held each on ITS OWN response
    future: every held update_task call queues its COMPLETE arguments
    and awaits a future the TEST resolves (bounded waits everywhere, no
    shared gate to accidentally release the next attempt, no scheduling
    assumptions). Unarmed calls (the in-line attempt) return the default
    fast — the deferral is made quickly."""

    def __init__(self):
        self.requests = []           # [(args-dict, response-future), ...]
        self.new_request = asyncio.Event()
        self.hold = False
        self.default = False         # what an UNARMED call returns (soft fail)

    async def update_task(self, task_id, status, **kw):
        if not self.hold:
            return self.default       # in-line: fast soft fail
        fut = asyncio.get_running_loop().create_future()
        self.requests.append(
            ({"task_id": task_id, "status": status, "kw": dict(kw)}, fut))
        self.new_request.set()
        return await fut               # the test decides this call's outcome

    async def next_request(self, timeout=10.0):
        """Bounded wait for the next queued held call's COMPLETE arguments
        and its response future."""
        while not self.requests:
            await asyncio.wait_for(self.new_request.wait(), timeout)
            self.new_request.clear()
        return self.requests.pop(0)


def _newer_payload():
    return {"status": "completed",
            "kw": {"agent_name": "new-agent", "result": {"new": True},
                   "elapsed_s": 9.9}}


async def _drain_one_deferred_write(storage, old_lands):
    """Shared body of the re-deferral race tests, fully deterministic and
    bounded: one task deferred (the in-line attempt fails SOFT and fast),
    then the storage is armed so the drainer's calls are each held on
    their OWN future: the OLD drain attempt's COMPLETE arguments are
    retrieved and checked, the entry is REPLACED with a newer payload,
    the old future is resolved with the test-chosen outcome (`old_lands`),
    and the NEWER attempt is then retrieved WHILE STILL HELD — its
    complete arguments must equal the replacement payload and the
    pending entry must still BE the replacement object."""
    ok = await server._write_task_terminal("race-1", "failed",
                                          result={"old": True})
    assert ok is False
    assert "race-1" in server.TERMINAL_WRITE_PENDING
    old_payload = server.TERMINAL_WRITE_PENDING["race-1"]
    storage.hold = True               # arm: the drainer's calls are held
    # the OLD drain attempt, held on its own future
    old_req, old_fut = await storage.next_request()
    assert old_req == {"task_id": "race-1",
                       "status": old_payload["status"],
                       "kw": old_payload["kw"]}
    # a NEWER deferral for the same task replaces the pending payload
    server.TERMINAL_WRITE_PENDING["race-1"] = _newer_payload()
    newer = server.TERMINAL_WRITE_PENDING["race-1"]
    old_fut.set_result(old_lands)     # release the OLD attempt (test-chosen)
    # the NEWER payload's own round, retrieved WHILE STILL HELD: the
    # drainer serves the replacement next, and the pending entry is the
    # replacement OBJECT (not deleted, not rotated, not overwritten)
    new_req, new_fut = await storage.next_request()
    assert new_req == {"task_id": "race-1",
                       "status": newer["status"],
                       "kw": newer["kw"]}
    assert server.TERMINAL_WRITE_PENDING.get("race-1") is newer
    new_fut.set_result(True)          # storage recovers for the replacement
    return newer


async def test_redeferred_payload_survives_a_landed_old_attempt(
        fast_retry, monkeypatch):
    """REQUIRED behavior (round 3 review): a drain attempt that LANDS while
    a NEWER deferral replaced its entry must NOT delete the newer payload —
    the replacement is the payload that must eventually land."""
    monkeypatch.setattr(server, "TASK_TERMINAL_WRITE_ATTEMPTS", 1)
    monkeypatch.setattr(server, "TERMINAL_WRITE_DRAIN_INTERVAL_S", 0.05)
    st = _QueueStorage()
    monkeypatch.setattr(server, "get_storage", lambda: st)
    await _drain_one_deferred_write(st, old_lands=True)
    # the OLD attempt landed AND the map emptied because the NEWER payload
    # landed via its own round — not because the old one popped it
    deadline = time.time() + 15
    while time.time() < deadline:
        if "race-1" not in server.TERMINAL_WRITE_PENDING:
            break
        await asyncio.sleep(0.02)
    assert "race-1" not in server.TERMINAL_WRITE_PENDING, \
        "the newer payload never landed (pending=%s)" % server.TERMINAL_WRITE_PENDING
    drainer = server._TERMINAL_WRITE_DRAIN_TASK
    deadline = time.time() + 15
    while time.time() < deadline and not drainer.done():
        await asyncio.sleep(0.02)
    assert drainer.done()  # the drainer self-terminated once empty


async def test_redeferred_payload_survives_a_failed_old_attempt(
        fast_retry, monkeypatch):
    """REQUIRED behavior (round 3 review): a drain attempt that FAILS while
    a NEWER deferral replaced its entry must NOT rotate or overwrite the
    newer payload — the replacement stays where the newer deferral put it,
    and it is what lands."""
    monkeypatch.setattr(server, "TASK_TERMINAL_WRITE_ATTEMPTS", 1)
    monkeypatch.setattr(server, "TERMINAL_WRITE_DRAIN_INTERVAL_S", 0.05)
    st = _QueueStorage()
    monkeypatch.setattr(server, "get_storage", lambda: st)
    newer = await _drain_one_deferred_write(st, old_lands=False)
    # the OLD attempt FAILED and did not touch the replacement (asserted
    # while the newer attempt was held: the pending entry `is newer`)
    assert newer is not None
    # the NEWER payload landed via its own round and the entry is gone
    deadline = time.time() + 15
    while time.time() < deadline:
        if "race-1" not in server.TERMINAL_WRITE_PENDING:
            break
        await asyncio.sleep(0.02)
    assert "race-1" not in server.TERMINAL_WRITE_PENDING, \
        "the newer payload never landed (pending=%s)" % server.TERMINAL_WRITE_PENDING
    drainer = server._TERMINAL_WRITE_DRAIN_TASK
    deadline = time.time() + 15
    while time.time() < deadline and not drainer.done():
        await asyncio.sleep(0.02)
    assert drainer.done()  # the drainer self-terminated once empty


async def test_drainer_survives_a_raising_write(fast_retry, caplog, monkeypatch):
    """REQUIRED behavior (round 3 review): an exception in ONE deferred
    write must not end reconciliation for the rest — the drainer catches
    it per write, keeps the entry pending, and lands the write on a later
    round with NO restart (the SAME drainer task, no new defer needed).
    The exception carries a SENSITIVE MARKER: the log must carry the TYPE
    only — never the raw message (R-149 redaction discipline)."""
    monkeypatch.setattr(server, "TASK_TERMINAL_WRITE_ATTEMPTS", 1)
    monkeypatch.setattr(server, "TERMINAL_WRITE_DRAIN_INTERVAL_S", 0.05)
    marker = "credential-not-for-logs:secret-token-xyz"

    class _RaisingStorage:
        """Fails SOFT in-line (the contract's shape), then RAISES on the
        first drain attempt — an exception whose message carries a marker
        the adapter should never forward to the log."""

        def __init__(self, raise_at_call):
            self.raise_at_call = raise_at_call
            self.calls = 0

        async def update_task(self, task_id, status, **kw):
            self.calls += 1
            if self.calls == self.raise_at_call:
                raise RuntimeError(f"adapter bug echoing: {marker}")
            return self.calls > self.raise_at_call

    st = _RaisingStorage(raise_at_call=2)
    monkeypatch.setattr(server, "get_storage", lambda: st)
    with caplog.at_level(logging.ERROR):
        ok = await server._write_task_terminal(
            "raise-1", "completed", agent_name="a", result={"ok": True})
    assert ok is False
    assert "raise-1" in server.TERMINAL_WRITE_PENDING
    drainer_after_defer = server._TERMINAL_WRITE_DRAIN_TASK
    assert drainer_after_defer is not None
    deadline = time.time() + 15
    while time.time() < deadline:
        if "raise-1" not in server.TERMINAL_WRITE_PENDING:
            break
        await asyncio.sleep(0.02)
    assert "raise-1" not in server.TERMINAL_WRITE_PENDING, \
        "the drainer never landed the write past the raising round " \
        f"(calls={st.calls}, pending={server.TERMINAL_WRITE_PENDING})"
    assert st.calls >= 3            # the raising round did not end reconciliation
    # no restart: the SAME task object drained it (a new defer was NOT made)
    assert server._TERMINAL_WRITE_DRAIN_TASK is drainer_after_defer
    await asyncio.sleep(0.2)
    assert drainer_after_defer.done()  # and it self-terminated once empty
    # R-149 redaction: the marker text must NEVER reach the log — the TYPE
    # only (RuntimeError), never the raw exception message or traceback
    assert marker not in caplog.text, \
        "a raising storage adapter's raw message leaked into the log"
    assert "RuntimeError" in caplog.text  # the type IS named


async def test_drainer_is_fair_past_the_batch(fast_retry, monkeypatch):
    """REQUIRED behavior (round 3 review): a round serves the FIRST
    TERMINAL_WRITE_DRAIN_BATCH entries and moves FAILED ones to the BACK —
    a persistently-failing head must not starve the entries behind it.
    30 deferred writes with the production batch of 25: the first 25 fail
    forever; the LAST 5 must still land."""
    monkeypatch.setattr(server, "TASK_TERMINAL_WRITE_ATTEMPTS", 1)
    monkeypatch.setattr(server, "TERMINAL_WRITE_DRAIN_INTERVAL_S", 0.05)

    class _FairnessStorage:
        """Fails EVERYTHING while the 30 deferrals are made (so all 30 defer
        in-line), then fails only the stuck head forever — the drainer alone
        decides who lands."""

        def __init__(self):
            self.fail_all = True
            self.stuck = set()

        async def update_task(self, task_id, status, **kw):
            if self.fail_all:
                return False
            return task_id not in self.stuck

    st = _FairnessStorage()
    monkeypatch.setattr(server, "get_storage", lambda: st)
    for i in range(30):
        ok = await server._write_task_terminal(f"fair-{i}", "completed")
        assert ok is False
    assert len(server.TERMINAL_WRITE_PENDING) == 30
    # storage recovers for everyone EXCEPT the persistently-failing head
    st.fail_all = False
    st.stuck = {f"fair-{i}" for i in range(25)}
    deadline = time.time() + 15
    while time.time() < deadline:
        if all(f"fair-{i}" not in server.TERMINAL_WRITE_PENDING
               for i in range(25, 30)):
            break
        await asyncio.sleep(0.02)
    # the tail behind 25 persistently-failing entries LANDED (not starved)
    for i in range(25, 30):
        assert f"fair-{i}" not in server.TERMINAL_WRITE_PENDING, \
            f"fair-{i} was starved by the failing head"
    # the failing head is still pending, fair-rounded to the back
    for i in range(25):
        assert f"fair-{i}" in server.TERMINAL_WRITE_PENDING


def test_stale_drainer_task_does_not_block_a_fresh_loop(
        monkeypatch):
    """REQUIRED behavior (round 3 review): a drainer task abandoned on a
    DIFFERENT event loop is never done() — loop identity, not done()
    alone, must decide liveness, or a stale task blocks a fresh drainer
    forever. Synchronous on purpose: two loops SEQUENTIALLY — a dormant
    task is planted on a foreign (never-run) loop, reconciliation then
    runs on a SECOND loop which must start a live drainer of its own and
    land the write; the planted task is cancelled and its cancellation
    PROCESSED on its owning loop before that loop closes (no abandoned
    coroutines, no suppressed warnings)."""
    monkeypatch.setattr(server, "TASK_TERMINAL_WRITE_ATTEMPTS", 1)
    monkeypatch.setattr(server, "TERMINAL_WRITE_DRAIN_INTERVAL_S", 0.05)
    # plant a never-done drainer task on a FOREIGN, never-run loop — the
    # exact shape a dead loop's abandoned drainer keeps forever (never
    # done(), wrong loop identity)
    foreign_loop = asyncio.new_event_loop()

    async def _forever():
        await asyncio.sleep(3600)

    stale = foreign_loop.create_task(_forever())
    # plant it as the module's drainer task — the exact state a dead loop
    # leaves behind (never done(), wrong loop identity)
    server._TERMINAL_WRITE_DRAIN_TASK = stale

    async def _reconcile_on_a_second_loop():
        # (a fresh defer on THIS loop must not see the stale task as live)
        flaky = _FlakyStorage(failures=1)   # in-line soft fail -> defer
        monkeypatch.setattr(server, "get_storage", lambda: flaky)
        ok = await server._write_task_terminal("stale-1", "completed")
        assert ok is False
        assert "stale-1" in server.TERMINAL_WRITE_PENDING
        fresh = server._TERMINAL_WRITE_DRAIN_TASK
        assert fresh is not stale, \
            "the stale task was treated as live and no fresh drainer started"
        assert fresh is not None
        assert fresh.get_loop() is asyncio.get_running_loop(), \
            "the fresh drainer was created on a foreign loop"
        deadline = time.time() + 15
        while time.time() < deadline:
            if "stale-1" not in server.TERMINAL_WRITE_PENDING:
                break
            await asyncio.sleep(0.02)
        assert "stale-1" not in server.TERMINAL_WRITE_PENDING, \
            "the stale task blocked the fresh drainer — the write never landed"
        return fresh

    try:
        asyncio.run(_reconcile_on_a_second_loop())
    finally:
        # cleanup, each task on its OWNING loop: process the planted task's
        # cancellation on the foreign loop before closing it (no abandoned
        # coroutines — no RuntimeWarning to suppress). The module handle
        # is _TERMINAL_WRITE_DRAIN_TASK (with the underscore) — this sync
        # test cleans up after itself: the sync fixture cannot await a
        # task whose loop is not the fixture's own loop
        stale.cancel()
        try:
            foreign_loop.run_until_complete(stale)
        except asyncio.CancelledError:
            pass
        foreign_loop.close()
        server._TERMINAL_WRITE_DRAIN_TASK = None
        server.TERMINAL_WRITE_PENDING.clear()


# ---------------------------------------------- call site: the completion
class _FastAgent:
    """Completes immediately — the retry is the subject, not the run."""

    name = "fast-agent"
    stats = {"tasks_completed": 0, "tasks_failed": 0, "total_time_s": 0.0}

    async def execute(self, payload):
        return {"ok": True, "result": "done", "elapsed_s": 0.01}


def test_completion_site_routes_the_terminal_write_through_the_retry(
        tenants, monkeypatch, caplog):
    """REQUIRED behavior (the call-site proof): a real volatile task whose
    storage fails the TERMINAL write twice (the breaker's shape) still
    lands ``completed`` — the completion path retries the soft failure
    instead of dropping the record on the floor. The intermediate
    ``running`` write may fail silently (self-healing at the terminal
    write); the TERMINAL one may not."""
    terminal_calls = {"n": 0}

    class _TerminalFlaky:
        """Delegates everything to the real storage; fails the first two
        ``completed`` writes (soft, False) then succeeds."""

        def __init__(self, real):
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

        def persists(self):
            # G-160-2 batch 21: this double's terminal write CAN land (it
            # answers it itself) — it must not inherit the unconfigured
            # real store's "nothing to persist" short-circuit
            return True

        async def update_task(self, task_id, status, **kw):
            if status == "completed":
                terminal_calls["n"] += 1
                if terminal_calls["n"] <= 2:
                    return False        # the breaker's shape
                return True
            return await self._real.update_task(task_id, status, **kw)

    real_get = server.get_storage
    monkeypatch.setattr(server, "_pick_agent", lambda s: _FastAgent())
    monkeypatch.setattr(server, "get_storage",
                        lambda: _TerminalFlaky(real_get()))
    with caplog.at_level(logging.CRITICAL):
        with _client() as c:
            r = c.post("/api/tasks", headers=tenants.headers,
                       json={"specialization": "analyst", "prompt": "retry me"})
            assert r.status_code == 202, r.text
            tid = r.json()["task_id"]
            # the memory status flips BEFORE the terminal write lands — the
            # REAL subject is the write: wait for the retry to finish (the
            # 2 soft failures + the landing write)
            assert _wait_for(lambda: server.TASKS[tid]["status"] == "completed"
                             and terminal_calls["n"] == 3, timeout=15), \
                f"the terminal write never landed: status={server.TASKS[tid]['status']}, " \
                f"terminal writes={terminal_calls['n']}"
    # the terminal write was attempted 3 times (2 failed + 1 landed) —
    # retried through the helper, not dropped
    assert terminal_calls["n"] == 3, terminal_calls
    # the recovered write is NOT a CRITICAL (only exhaustion is)
    assert not any("NOT persisted" in r.message for r in caplog.records)
