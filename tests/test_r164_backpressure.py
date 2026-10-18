"""R-164 (D-083) — backpressure and limits.

Acceptance (P3_AGENT_COORDINATION_AR.md): saturating the ``web`` pool does
not stall local/net/storage; a full queue refuses or defers explicitly;
recovery after the saturation ends. Plus the ROADMAP scope: independent
limits per service, queue-length caps, acceptance rate, timeouts, cancel,
circuit breakers, retry jitter, DLQ and Retry-After — in the volatile mode
(no storage), the volatile mode WITH storage, and the durable queue mode.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
import urllib.error
from email.utils import formatdate

import pytest

import server
from agent_civilization.core import backpressure, llm, pools
from agent_civilization.core import contracts as C
from agent_civilization.storage import supabase_store as ss
from tests import test_r163_durable_queue as r163
from tests.test_r163_durable_queue import _PostgREST, _client, _enable_queue, _row, _wait_for

# the R-163 fake-PostgREST fixtures, re-exported (assignment, not import:
# the test parameters of the same name are the fixture requests)
db = r163.db
tenants = r163.tenants
_clean_tasks = r163._clean_tasks          # autouse: clean TASKS/_QUEUE_STATE per test


# ------------------------------------------------------------- primitives

class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_r164_backoff_is_bounded_jittered_and_growing():
    import random
    rng = random.Random(7)
    for attempt in range(1, 12):
        exp = min(30.0, 1.0 * 2 ** (attempt - 1))
        for _ in range(50):
            d = backpressure.backoff_delay(attempt, 1.0, 30.0, rng)
            assert exp / 2 <= d <= exp            # equal jitter: never 0, never above cap
    # jitter: two callers that failed together do not retry together
    draws = {round(backpressure.backoff_delay(3, 1.0, 30.0), 6) for _ in range(40)}
    assert len(draws) > 30
    # absurd attempt counts stay at the cap (no overflow)
    assert backpressure.backoff_delay(10_000, 1.0, 30.0) <= 30.0


def test_r164_parse_retry_after_seconds_date_garbage_and_cap():
    assert backpressure.parse_retry_after("7") == 7.0
    assert backpressure.parse_retry_after("100000") == backpressure.RETRY_AFTER_CAP_S
    now = time.time()
    assert 25 <= backpressure.parse_retry_after(formatdate(now + 30, usegmt=True), now=now) <= 30
    assert backpressure.parse_retry_after(formatdate(now - 30, usegmt=True), now=now) == 0.0
    for junk in (None, "", "soon", "-5", "1.5e9x"):
        assert backpressure.parse_retry_after(junk) is None


def test_r164_breaker_state_machine():
    clk = _Clock()
    b = backpressure.CircuitBreaker("t", threshold=3, reset_s=10, clock=clk)
    for _ in range(2):
        assert b.allow()
        b.failure()
    assert b.state() == "closed"
    b.success()                                   # consecutive: a success resets the count
    for _ in range(3):
        b.failure()
    assert b.state() == "open" and not b.allow()
    assert 9 <= b.retry_after() <= 10
    with pytest.raises(backpressure.CircuitOpen) as ei:
        b.check()
    assert ei.value.retry_after >= 1 and "retry in" in str(ei.value)
    clk.t += 10
    assert b.state() == "half_open"
    assert b.allow()                              # exactly one probe
    assert not b.allow() and b.is_open()
    b.failure()                                   # probe failed -> open again
    assert b.state() == "open"
    clk.t += 10
    assert b.allow()
    b.success()                                   # probe ok -> closed
    assert b.state() == "closed" and b.allow() and b.allow()
    assert b.snapshot()["opened"] == 2


def test_r164_breaker_lost_probe_and_neutral_outcome_free_the_slot():
    clk = _Clock()
    b = backpressure.CircuitBreaker("t", threshold=1, reset_s=5, clock=clk)
    b.failure()
    clk.t += 5
    assert b.allow() and not b.allow()
    b.release_probe()                             # neutral outcome (4xx of the request)
    assert b.allow()
    clk.t += 5                                    # probe never reported (cancelled)
    assert b.allow()


def test_r164_breaker_disabled_still_honours_retry_after_hold():
    clk = _Clock()
    b = backpressure.CircuitBreaker("t", threshold=0, reset_s=5, clock=clk)
    for _ in range(100):
        b.failure()
    assert b.state() == "closed"                  # 0 = never opens on failures
    b.hold(3)                                     # but the provider said "come back in 3 s"
    assert not b.allow() and 2 <= b.retry_after() <= 3
    clk.t += 3
    assert b.allow()


def test_r164_breaker_env_is_validated_at_start(monkeypatch):
    monkeypatch.setenv("AGOS_BREAKER_WEB_THRESHOLD", "lots")
    backpressure.reset_all()
    with pytest.raises(RuntimeError, match="AGOS_BREAKER_WEB_THRESHOLD"):
        backpressure.validate_env()
    monkeypatch.setenv("AGOS_BREAKER_WEB_THRESHOLD", "2")
    backpressure.reset_all()
    assert backpressure.breaker("web").threshold == 2


def test_r164_guarded_call_classifies_provider_outcomes(monkeypatch):
    monkeypatch.setenv("AGOS_BREAKER_WEB_THRESHOLD", "2")
    backpressure.reset_all()
    calls = []

    def boom(exc):
        calls.append(exc)
        raise exc

    # a request error (bad input / policy) says nothing about the provider
    for _ in range(5):
        with pytest.raises(ValueError):
            backpressure.guarded_call("web", boom, ValueError("blocked url"))
    assert backpressure.breaker("web").state() == "closed"
    # transport failures open it; then the provider is not called at all
    for _ in range(2):
        with pytest.raises(OSError):
            backpressure.guarded_call("web", boom, urllib.error.URLError("down"))
    n = len(calls)
    with pytest.raises(backpressure.CircuitOpen):
        backpressure.guarded_call("web", boom, OSError("never called"))
    assert len(calls) == n
    # 429 + Retry-After holds the breaker for that long (even if closed)
    backpressure.reset_all()
    err = urllib.error.HTTPError("u", 429, "slow down", {"Retry-After": "40"}, None)
    with pytest.raises(urllib.error.HTTPError):
        backpressure.guarded_call("translate", boom, err)
    assert 39 <= backpressure.breaker("translate").retry_after() <= 40


def test_r164_web_search_provider_is_behind_its_breaker(monkeypatch):
    from agent_civilization.core.agents import real_dispatcher as rd
    monkeypatch.setenv("AGOS_BREAKER_WEB_THRESHOLD", "2")
    backpressure.reset_all()
    hits = []

    def dead(*a, **k):
        hits.append(1)
        raise urllib.error.URLError("provider down")
    monkeypatch.setattr(rd, "_web_search_raw", dead)
    for _ in range(2):
        with pytest.raises(OSError):
            rd._web_search("q")
    t0 = time.monotonic()
    with pytest.raises(backpressure.CircuitOpen):
        rd._web_search("q")
    assert time.monotonic() - t0 < 0.05 and len(hits) == 2
    # translation has its own breaker: the search outage does not touch it
    assert backpressure.breaker("translate").state() == "closed"


# ------------------------------------------------- pool isolation (acceptance)

def test_r164_web_pool_saturation_does_not_stall_local_net_or_storage(db, monkeypatch):
    """The acceptance criterion itself: every ``web`` thread blocked AND a
    backlog waiting -> local / net / storage still answer at once; with a
    queue cap the next web call is refused explicitly; after the release
    the web pool recovers."""
    monkeypatch.setenv("AGOS_POOL_WEB_QUEUE", "3")
    gate = threading.Event()
    size = pools.size("web")

    def blocked():
        gate.wait(10)
        return "web-done"

    async def scenario():
        web = [asyncio.ensure_future(pools.run("web", blocked)) for _ in range(size + 3)]
        await asyncio.sleep(0.2)
        load = pools.load()["web"]
        assert load["waiting"] == 3              # saturated: all threads + backlog
        t0 = time.monotonic()
        local = await pools.run("local", lambda: "local-ok")
        net = await pools.run("net", lambda: "net-ok")
        stored = await ss.get_storage().insert_event("r164.isolation", {"n": 1})
        fast = time.monotonic() - t0
        # full web queue: refused at once, explicitly (no silent wait)
        with pytest.raises(pools.PoolBusy):
            await pools.run("web", blocked)
        gate.set()
        done = await asyncio.wait_for(asyncio.gather(*web), timeout=10)
        after = await pools.run("web", lambda: "web-recovered")
        return local, net, stored, fast, done, after

    try:
        local, net, stored, fast, done, after = asyncio.run(scenario())
    finally:
        gate.set()
        asyncio.run(ss.get_storage().close())
    assert (local, net, stored) == ("local-ok", "net-ok", True)
    assert fast < 1.0, f"local/net/storage took {fast:.2f}s while web was saturated"
    assert done == ["web-done"] * (size + 3) and after == "web-recovered"
    assert pools.load()["web"]["busy_rejections"] >= 1


def test_r164_cancelled_queued_pool_call_never_runs():
    """Cancel propagation through a pool: a call still waiting for a thread
    is dropped when its caller is cancelled — it never starts later."""
    gate = threading.Event()
    ran = []
    size = pools.size("local")

    async def scenario():
        busy = [asyncio.ensure_future(pools.run("local", gate.wait, 10)) for _ in range(size)]
        await asyncio.sleep(0.1)
        waiting = asyncio.ensure_future(pools.run("local", lambda: ran.append("late")))
        await asyncio.sleep(0.05)
        waiting.cancel()
        # the cancel reaches the queued concurrent future via a loop callback
        # (asyncio chains futures with call_soon) — let it settle before the
        # threads free up; releasing in the same tick races the test itself
        await asyncio.gather(waiting, return_exceptions=True)
        await asyncio.sleep(0)
        assert waiting.cancelled()
        gate.set()
        await asyncio.gather(*busy)
        await asyncio.sleep(0.2)

    try:
        asyncio.run(scenario())
    finally:
        gate.set()
    assert ran == []


# ------------------------------------------------------------ storage limits

def _hanging_server():
    """A TCP server that accepts and never answers (a store that hangs)."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(64)
    conns = []
    stop = threading.Event()

    def loop():
        srv.settimeout(0.1)
        while not stop.is_set():
            try:
                conns.append(srv.accept()[0])
            except OSError:
                pass
    threading.Thread(target=loop, daemon=True).start()
    return srv, stop, conns


def test_r164_hanging_store_opens_breaker_then_fails_fast_others_unaffected(monkeypatch):
    srv, stop, conns = _hanging_server()
    monkeypatch.setattr(ss, "SUPABASE_URL", f"http://127.0.0.1:{srv.getsockname()[1]}")
    monkeypatch.setattr(ss, "SUPABASE_KEY", "r164-test-not-a-real-key")
    monkeypatch.setattr(ss, "STORAGE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(ss, "_storage", None)
    monkeypatch.setenv("AGOS_BREAKER_STORAGE_THRESHOLD", "3")
    backpressure.reset_all()

    async def scenario():
        st = ss.get_storage()
        t0 = time.monotonic()
        # while the store hangs, the local pool is not held by it (async I/O)
        slow = asyncio.ensure_future(asyncio.gather(*[st.insert_event("r164.hang", {}) for _ in range(3)]))
        local = await pools.run("local", lambda: "local-ok")
        local_s = time.monotonic() - t0
        assert await slow == [False, False, False]      # each timed out (0.3 s)
        t1 = time.monotonic()
        refused = await st.insert_event("r164.hang", {})
        fast = time.monotonic() - t1
        await st.close()
        return local, local_s, refused, fast

    try:
        local, local_s, refused, fast = asyncio.run(scenario())
    finally:
        stop.set()
        srv.close()
        monkeypatch.setattr(ss, "_storage", None)
    assert local == "local-ok" and local_s < 0.25
    assert refused is False and fast < 0.05, f"open breaker still waited {fast:.2f}s"
    assert backpressure.breaker("storage").state() == "open"
    last = ss.storage_stats()["last_error"]["reason"]
    assert last.startswith("circuit open after exception:")      # root cause still named


# --------------------------------------------------------- model provider

def _keyless(monkeypatch, post):
    monkeypatch.setattr(llm, "_keyless_post", post)
    monkeypatch.setattr(llm, "KEYLESS_MIN_SPACING_S", 0.01)
    monkeypatch.setattr(llm, "KEYLESS_BUDGET_S", 5.0)
    monkeypatch.setattr(llm, "_keyless_last", 0.0)


def test_r164_keyless_honours_retry_after_longer_than_budget(monkeypatch):
    calls = []

    def post(*a):
        calls.append(1)
        raise urllib.error.HTTPError("u", 429, "rate", {"Retry-After": "120"}, None)
    _keyless(monkeypatch, post)
    first = asyncio.run(llm._keyless_complete("s", "p", 50))
    assert first["ok"] is False and first["busy"] is True and first["retry_after"] == 120
    C.validate("llm_result", first)                    # additive fields, v1
    assert len(calls) == 1                             # stopped at once, no hammering
    t0 = time.monotonic()
    second = asyncio.run(llm._keyless_complete("s", "p", 50))
    assert second["busy"] is True and len(calls) == 1 and time.monotonic() - t0 < 0.05


def test_r164_keyless_waits_retry_after_within_budget_then_succeeds(monkeypatch):
    calls = []

    def post(*a):
        calls.append(time.monotonic())
        if len(calls) == 1:
            raise urllib.error.HTTPError("u", 429, "rate", {"Retry-After": "1"}, None)
        return {"choices": [{"message": {"content": "ok after wait"}}]}
    _keyless(monkeypatch, post)
    out = asyncio.run(llm._keyless_complete("s", "p", 50))
    assert out["ok"] is True and out["content"] == "ok after wait"
    assert calls[1] - calls[0] >= 0.95                 # waited the provider's 1 s


def test_r164_keyless_retries_are_jittered(monkeypatch):
    import types
    waits = []
    real_sleep = asyncio.sleep
    virtual = {"t": time.monotonic()}

    async def fake_sleep(d):                          # virtual time: no real 60 s wait
        waits.append(d)
        virtual["t"] += d
        await real_sleep(0)
    monkeypatch.setattr(llm, "_time", types.SimpleNamespace(monotonic=lambda: virtual["t"]))

    def post(*a):
        raise urllib.error.HTTPError("u", 503, "busy", {}, None)
    _keyless(monkeypatch, post)
    monkeypatch.setattr(llm, "KEYLESS_MIN_SPACING_S", 1.0)
    monkeypatch.setattr(llm, "KEYLESS_BUDGET_S", 60.0)
    monkeypatch.setattr(llm._asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(llm, "_keyless_last", -1e9)
    asyncio.run(llm._keyless_complete("s", "p", 50))
    assert len(waits) >= 3
    for n, w in enumerate(waits[:3], start=1):
        exp = min(8.0, 2 ** n)                        # old envelope: spacing * 2**min(n,3)
        assert max(1.0, exp / 2) - 0.01 <= w <= exp + 0.01, (n, w)


def test_r164_keyless_breaker_opens_on_transport_failures_and_recovers(monkeypatch):
    monkeypatch.setenv("AGOS_BREAKER_LLM_THRESHOLD", "3")
    monkeypatch.setenv("AGOS_BREAKER_LLM_RESET_S", "0.3")
    backpressure.reset_all()
    state = {"down": True, "calls": 0}

    def post(*a):
        state["calls"] += 1
        if state["down"]:
            raise urllib.error.URLError("connection refused")
        return {"choices": [{"message": {"content": "back"}}]}
    _keyless(monkeypatch, post)
    out = asyncio.run(llm._keyless_complete("s", "p", 50))
    assert out["busy"] is True and "circuit open" in out["error"]
    assert state["calls"] == 3                         # stopped at the threshold, budget not burnt
    t0 = time.monotonic()
    again = asyncio.run(llm._keyless_complete("s", "p", 50))
    assert again["busy"] is True and state["calls"] == 3 and time.monotonic() - t0 < 0.05
    state["down"] = False
    time.sleep(0.35)                                   # reset period -> half-open probe
    ok = asyncio.run(llm._keyless_complete("s", "p", 50))
    assert ok["ok"] is True and backpressure.breaker("llm").state() == "closed"


def _keyed_server(handler):
    from aiohttp import web
    started = threading.Event()
    box = {}

    def run():
        loop = asyncio.new_event_loop()
        app = web.Application()
        app.router.add_post("/v1/chat/completions", handler)
        runner = web.AppRunner(app)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 0)
        loop.run_until_complete(site.start())
        box["port"] = site._server.sockets[0].getsockname()[1]
        box["loop"] = loop
        started.set()
        loop.run_forever()
    threading.Thread(target=run, daemon=True).start()
    started.wait(5)
    return box


def test_r164_keyed_provider_retry_after_and_concurrency_limit(monkeypatch):
    from aiohttp import web
    hits = {"n": 0, "mode": "429"}

    async def handler(request):
        hits["n"] += 1
        if hits["mode"] == "429":
            return web.Response(status=429, text="slow", headers={"Retry-After": "30"})
        await asyncio.sleep(0.5)
        return web.json_response({"choices": [{"message": {"content": "keyed ok"}}]})
    box = _keyed_server(handler)
    url = f"http://127.0.0.1:{box['port']}/v1/chat/completions"

    async def call(client, wait=None):
        return await client.complete("s", "p", 20, max_wait_s=wait)

    async def scenario_429():
        client = llm.LLMClient(api_key="r164-fake-key", url=url)
        first = await call(client)
        second = await call(client)
        await client.close()
        return first, second
    first, second = asyncio.run(scenario_429())
    assert first["ok"] is False and hits["n"] == 1
    assert second["busy"] is True and second["retry_after"] >= 29 and hits["n"] == 1
    C.validate("llm_result", second)

    backpressure.reset_all()
    hits["mode"] = "slow"
    monkeypatch.setattr(llm, "KEYED_CONCURRENCY", 1)
    monkeypatch.setattr(llm, "_keyed_sem", None)

    async def scenario_limit():
        client = llm.LLMClient(api_key="r164-fake-key", url=url)
        a, b = await asyncio.gather(call(client, 2.0), call(client, 0.1))
        await client.close()
        return a, b
    a, b = asyncio.run(scenario_limit())
    assert a["ok"] is True and a["content"] == "keyed ok"
    assert b["busy"] is True and "AGOS_LLM_CONCURRENCY" in b["error"]
    box["loop"].call_soon_threadsafe(box["loop"].stop)


# -------------------------------------------------------------- HTTP edge

def test_r164_rate_limit_429_carries_retry_after(tenant_auth, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(server, "RATE_LIMIT_REQUESTS", 2)
    with TestClient(server.app) as c:
        codes = [c.post("/api/tasks", json={"specialization": "analyst", "prompt": "rl"},
                        headers=tenant_auth.headers) for _ in range(3)]
    assert [r.status_code for r in codes[:2]] == [202, 202]
    r = codes[2]
    assert r.status_code == 429
    assert 1 <= int(r.headers["Retry-After"]) <= server.RATE_LIMIT_WINDOW
    C.validate("error_detail", r.json())               # body contract unchanged


class _HangAgent:
    """Runs until cancelled; records starts and cancellations."""
    name = "hang-agent"
    stats = {"tasks_completed": 0, "tasks_failed": 0, "total_time_s": 0.0}

    def __init__(self, log):
        self.log = log

    async def execute(self, payload):
        self.log.append(("start", payload.get("prompt")))
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.log.append(("cancelled", payload.get("prompt")))
            raise
        return {"ok": True, "agent": self.name, "elapsed_s": 0, "result": {"ok": True, "answer": "x"}}


def _post(c, headers, prompt, **extra):
    return c.post("/api/tasks", json={"specialization": "analyst", "prompt": prompt, **extra}, headers=headers)


def _volatile_caps_scenario(tenants, monkeypatch, *, with_storage):
    log = []
    monkeypatch.setattr(server, "_pick_agent", lambda specialization: _HangAgent(log))
    monkeypatch.setattr(server, "TASK_QUEUE_MAX_PER_TENANT", 2)
    monkeypatch.setattr(server, "TASK_QUEUE_MAX", 3)
    with _client() as c:
        a1 = _post(c, tenants.headers, "a1", idempotency_key="k-a1")
        a2 = _post(c, tenants.headers, "a2")
        assert (a1.status_code, a2.status_code) == (202, 202)
        before = len(server.TASKS)
        a3 = _post(c, tenants.headers, "a3")
        assert a3.status_code == 429 and int(a3.headers["Retry-After"]) == server.TASK_RETRY_AFTER_S
        assert "this tenant" in a3.json()["detail"]
        C.validate("error_detail", a3.json())
        assert len(server.TASKS) == before              # a refused task leaves no record
        # the replay of an accepted key is never refused (it creates nothing)
        rep = _post(c, tenants.headers, "a1", idempotency_key="k-a1")
        assert rep.status_code == 202 and rep.json()["duplicate"] is True
        # tenant isolation: B is not blocked by A's full quota
        b1 = _post(c, tenants.headers_b, "b1")
        assert b1.status_code == 202
        # service cap reached: 503 + Retry-After, naming no other tenant
        b2 = _post(c, tenants.headers_b, "b2")
        assert b2.status_code == 503 and "Retry-After" in b2.headers
        assert "tenant" not in b2.json()["detail"]
        # recovery: cancelling frees capacity at once
        assert _wait_for(lambda: sum(1 for k, v in log if k == "start") == 3)
        cx = c.post(f"/api/tasks/{a1.json()['task_id']}/cancel", headers=tenants.headers)
        assert cx.status_code == 200
        assert _wait_for(lambda: ("cancelled", "a1") in log)      # the RUN was cancelled
        assert _post(c, tenants.headers, "a4").status_code == 202
        for tid in list(server._VOLATILE_RUNS):
            server._VOLATILE_RUNS[tid].get_loop().call_soon_threadsafe(server._VOLATILE_RUNS[tid].cancel)
    return log


def test_r164_volatile_queue_caps_isolation_and_recovery(tenants, monkeypatch):
    _volatile_caps_scenario(tenants, monkeypatch, with_storage=False)


def test_r164_volatile_with_storage_caps_leave_no_row(tenants, db, monkeypatch):
    log = _volatile_caps_scenario(tenants, monkeypatch, with_storage=True)
    # every accepted task (a1, a2, b1, a4) has a row; refused ones have none
    assert sorted(r["prompt"] for r in db["tasks"]) == ["a1", "a2", "a4", "b1"]
    assert ("start", "a3") not in log and ("start", "b2") not in log


def test_r164_accept_rate_per_tenant(tenants, monkeypatch):
    monkeypatch.setattr(server, "TASK_ACCEPT_RATE_PER_MIN", 2)
    with _client() as c:
        assert [_post(c, tenants.headers, f"r{i}").status_code for i in range(2)] == [202, 202]
        r = _post(c, tenants.headers, "r3")
        assert r.status_code == 429 and 1 <= int(r.headers["Retry-After"]) <= 60
        assert "AGOS_TASK_ACCEPT_RATE_PER_MIN" in r.json()["detail"]
        assert _post(c, tenants.headers_b, "other").status_code == 202    # per tenant


def test_r164_volatile_timeout_fails_the_run_and_frees_the_slot(tenants, monkeypatch):
    log = []
    monkeypatch.setattr(server, "_pick_agent", lambda specialization: _HangAgent(log))
    monkeypatch.setattr(server, "TASK_TIMEOUT_S", 0.3)
    with _client() as c:
        tid = _post(c, tenants.headers, "hang").json()["task_id"]
        assert _wait_for(lambda: server.TASKS[tid]["status"] == "failed", timeout=5)
        got = c.get(f"/api/tasks/{tid}", headers=tenants.headers).json()
        assert "timed out" in got["result"]["result"]["error"]
        C.validate("agent_outcome", got["result"])
        assert ("cancelled", "hang") in log and tid not in server._VOLATILE_RUNS


def test_r164_prune_drops_finished_records_before_in_flight():
    server.TASKS.clear()
    server.TASKS["inflight"] = {"status": "running", "created_at": "2000-01-01T00:00:00"}
    for i in range(server.MAX_TASKS_KEPT + 5):
        server.TASKS[f"done{i}"] = {"status": "completed", "created_at": f"2001-01-01T00:{i // 60:02d}:{i % 60:02d}"}
    server._prune_tasks()
    assert len(server.TASKS) == server.MAX_TASKS_KEPT and "inflight" in server.TASKS


def test_r164_metrics_expose_pools_breakers_and_pending(tenant_auth):
    from fastapi.testclient import TestClient
    with TestClient(server.app) as c:
        asyncio.run(pools.run("local", lambda: 1))
        body = c.get("/metrics", headers=tenant_auth.headers).text
    for name in ("breaker_state", "tasks_pending", "pool_waiting"):
        assert name in body, name


# -------------------------------------------------------------- durable mode

def test_r164_durable_queue_caps_counted_in_the_store(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    monkeypatch.setattr(server, "TASK_QUEUE_MAX_PER_TENANT", 2)
    monkeypatch.setattr(server, "TASK_QUEUE_MAX", 4)
    monkeypatch.setattr(server, "_queue_dispatcher", lambda: asyncio.sleep(0))   # nothing runs
    # two pending rows of A already in the store (e.g. from before a restart)
    db["tasks"] += [_row("old1", "test_tenant"), _row("old2", "test_tenant", status="running")]
    with _client() as c:
        r = _post(c, tenants.headers, "a3")
        assert r.status_code == 429 and "Retry-After" in r.headers
        C.validate("error_detail", r.json())
        assert all(x["task_id"] in ("old1", "old2") for x in db["tasks"])     # nothing written
        assert _post(c, tenants.headers_b, "b1").status_code == 202      # B: 1, all: 3
        assert _post(c, tenants.headers_b, "b2").status_code == 202      # B: 2, all: 4
        full = _post(c, tenants.headers_b, "b3")                        # B at its cap
        assert full.status_code == 429 and "Retry-After" in full.headers
        monkeypatch.setattr(server, "TASK_QUEUE_MAX_PER_TENANT", 0)
        svc = _post(c, tenants.headers_b, "b4")                         # service at its cap
        assert svc.status_code == 503 and "Retry-After" in svc.headers
        monkeypatch.setattr(server, "TASK_QUEUE_MAX_PER_TENANT", 2)
        # finished rows do not count: recovery when A's rows end
        for x in db["tasks"]:
            if x["tenant_id"] == "test_tenant":
                x["status"] = "completed"
        assert _post(c, tenants.headers, "a4").status_code == 202


def test_r164_durable_global_cap_is_503_and_replay_is_never_refused(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    monkeypatch.setattr(server, "TASK_QUEUE_MAX", 1)
    monkeypatch.setattr(server, "_queue_dispatcher", lambda: asyncio.sleep(0))
    with _client() as c:
        first = _post(c, tenants.headers, "only", idempotency_key="k1")
        assert first.status_code == 202
        full = _post(c, tenants.headers_b, "b")
        assert full.status_code == 503 and int(full.headers["Retry-After"]) >= 1
        assert "tenant" not in full.json()["detail"]
        replay = _post(c, tenants.headers, "only", idempotency_key="k1")
        assert replay.status_code == 202 and replay.json()["duplicate"] is True


def test_r164_durable_store_down_fails_fast_with_retry_after_and_recovers(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    monkeypatch.setenv("AGOS_BREAKER_STORAGE_THRESHOLD", "3")
    monkeypatch.setenv("AGOS_BREAKER_STORAGE_RESET_S", "0.5")
    backpressure.reset_all()
    hits = [0]
    orig = _PostgREST._send

    def counting(self, *a, **k):
        hits[0] += 1
        return orig(self, *a, **k)
    monkeypatch.setattr(_PostgREST, "_send", counting)
    with _client() as c:
        _PostgREST.mode[0] = "reject"
        for _ in range(3):
            assert _post(c, tenants.headers, "down").status_code == 503
        assert _wait_for(lambda: backpressure.breaker("storage").is_open(), timeout=3)
        seen = hits[0]
        t0 = time.monotonic()
        r = _post(c, tenants.headers, "down")
        assert r.status_code == 503 and int(r.headers["Retry-After"]) >= 1
        assert time.monotonic() - t0 < 0.5 and hits[0] == seen        # the store was not called
        # services that do not need the store keep answering
        assert c.get("/health/live").status_code == 200
        assert asyncio.run(pools.run("local", lambda: "ok")) == "ok"
        # recovery once the store answers again (half-open probe -> closed)
        _PostgREST.mode[0] = "ok"
        assert _wait_for(lambda: _post(c, tenants.headers, "back").status_code == 202, timeout=5, step=0.2)
        assert backpressure.breaker("storage").state() == "closed"


def test_r164_durable_fairness_newer_tenant_is_not_starved(tenants, db, monkeypatch):
    """Tenant A has a backlog deeper than one dispatch batch (and older);
    tenant B queues one task later. B must run long before A's backlog
    drains (before R-164 it waited for all of it)."""
    _enable_queue(monkeypatch)
    monkeypatch.setattr(server, "TASK_DISPATCH_BATCH", 5)
    monkeypatch.setattr(server, "WORKER_CAPACITY", 2)
    done = []
    live = [0, 0]                       # [running now, max running]

    class _Quick:
        name = "quick"
        stats = {"tasks_completed": 0, "tasks_failed": 0, "total_time_s": 0.0}

        async def execute(self, payload):
            live[0] += 1
            live[1] = max(live[1], live[0])
            await asyncio.sleep(0.15)
            live[0] -= 1
            done.append(payload.get("prompt"))
            return {"ok": True, "agent": self.name, "elapsed_s": 0.15, "result": {"ok": True, "answer": "ok"}}
    monkeypatch.setattr(server, "_pick_agent", lambda specialization: _Quick())
    for i in range(20):
        db["tasks"].append(_row(f"a{i:02d}", "test_tenant", prompt=f"a{i:02d}",
                                created_at=f"2026-10-18T09:00:{i:02d}+00:00"))
    db["tasks"].append(_row("b00", "other_tenant", prompt="b00", created_at="2026-10-18T09:59:00+00:00"))
    calls = []
    st_cls = type(ss.get_storage())
    real_list = st_cls.list_queued_tasks

    async def spy(self, limit=50, exclude_tenants=None):
        out = await real_list(self, limit, exclude_tenants)
        calls.append((tuple(exclude_tenants or ()), [r.get("task_id") for r in out or []]))
        return out
    monkeypatch.setattr(st_cls, "list_queued_tasks", spy)
    with _client():
        assert _wait_for(lambda: "b00" in done, timeout=20), calls[:8]
        a_before_b = done.index("b00")
        assert a_before_b <= 2, f"B ran after {a_before_b} of A's tasks (starved)"
        # the hidden tenant was found by the exclusion scan of the full batch
        assert any(ex == ("test_tenant",) and "b00" in ids for ex, ids in calls)
        assert _wait_for(lambda: len(done) == 21, timeout=30)
    assert len(done) == len(set(done)) == 21                     # each ran exactly once
    assert live[1] <= 2, f"{live[1]} ran at once with WORKER_CAPACITY=2"   # per-claim capacity check


def test_r164_fair_order_round_robins_tenants():
    rows = [{"task_id": f"a{i}", "tenant_id": "A", "created_at": f"09:0{i}"} for i in range(4)]
    rows += [{"task_id": "b0", "tenant_id": "B", "created_at": "09:59"},
             {"task_id": "c0", "tenant_id": "C", "created_at": "09:58"}]
    order = [r["task_id"] for r in server._fair_order(rows, {})]
    assert order[:3] == ["a0", "c0", "b0"] and order[3:] == ["a1", "a2", "a3"]
    # a tenant already running work waits behind tenants with none
    order = [r["task_id"] for r in server._fair_order(rows, {"A": 1})]
    assert order[:2] == ["c0", "b0"]


def test_r164_durable_timeout_is_terminal_and_never_rerun(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    monkeypatch.setattr(server, "TASK_TIMEOUT_S", 0.3)
    monkeypatch.setattr(server, "TASK_REAP_S", 0.1)
    log = []
    monkeypatch.setattr(server, "_pick_agent", lambda specialization: _HangAgent(log))
    with _client() as c:
        tid = _post(c, tenants.headers, "hang").json()["task_id"]
        assert _wait_for(lambda: next((x["status"] == "failed" for x in db["tasks"] if x["task_id"] == tid), False))
        time.sleep(1.0)
        row = next(x for x in db["tasks"] if x["task_id"] == tid)
        assert row["attempts"] == 1 and tid not in server._RUNNING_RUNS
        assert [k for k, _ in log].count("start") == 1                # not retried
        got = c.get(f"/api/tasks/{tid}", headers=tenants.headers).json()
        assert "timed out" in got["result"]["result"]["error"]


def test_r164_poison_task_dead_letters_after_bounded_jittered_retries(tenants, db, monkeypatch):
    """A task that kills its worker every time: re-run at most
    max_attempts times, each after a jittered back-off, then dead-letter
    (kept, visible, never re-run). Another tenant's work goes on meanwhile."""
    _enable_queue(monkeypatch)
    monkeypatch.setattr(server, "TASK_REAP_S", 0.1)
    monkeypatch.setattr(server, "TASK_LEASE_S", 1.0)
    monkeypatch.setattr(server, "TASK_RETRY_BASE_S", 0.2)
    delays = []
    real_backoff = backpressure.backoff_delay

    def spy(attempt, base, cap, rng=None):
        d = real_backoff(attempt, base, cap, rng)
        delays.append((attempt, d))
        return d
    monkeypatch.setattr(backpressure, "backoff_delay", spy)
    runs = []

    class _Poison:
        name = "poison"
        stats = {"tasks_completed": 0, "tasks_failed": 0, "total_time_s": 0.0}

        async def execute(self, payload):
            if payload.get("prompt") != "poison":
                return {"ok": True, "agent": self.name, "elapsed_s": 0, "result": {"ok": True, "answer": "fine"}}
            # (not asyncio.current_task(): on Python 3.11 wait_for runs the
            # agent in an inner task — CI runs 3.11)
            tid = next(t for t, info in server._QUEUE_STATE.items()
                       if info.get("state") == "running" and t in poison_ids)
            runs.append(tid)
            # the process "dies" mid-run: it forgets the run, the row keeps its lease
            server._RUNNING_RUNS.pop(tid, None)
            server._QUEUE_STATE.pop(tid, None)
            server.TASKS.pop(tid, None)
            raise asyncio.CancelledError()
    monkeypatch.setattr(server, "_pick_agent", lambda specialization: _Poison())
    poison_ids = {"r164-poison-task"}
    db["tasks"].append(_row("r164-poison-task", "test_tenant", prompt="poison", payload=None))
    with _client() as c:
        tid = "r164-poison-task"
        ok = _post(c, tenants.headers_b, "healthy").json()["task_id"]
        assert _wait_for(lambda: next((x["status"] == "completed" for x in db["tasks"] if x["task_id"] == ok), False))
        assert _wait_for(lambda: next((x["status"] == "dead_letter" for x in db["tasks"] if x["task_id"] == tid),
                                      False), timeout=30)
        time.sleep(1.5)
        assert runs == [tid] * 3                                     # exactly max_attempts runs
        row = next(x for x in db["tasks"] if x["task_id"] == tid)
        assert row["attempts"] == 3 and row["status"] == "dead_letter"
        got = c.get(f"/api/tasks/{tid}", headers=tenants.headers).json()
        assert got["status"] == "failed" and "dead-letter" in got["result"]["result"]["error"]
    # two recoveries went back to the queue, each with a bounded jittered delay
    rec = [d for a, d in delays]
    assert len(rec) >= 2 and all(0.1 - 1e-9 <= d <= server.TASK_RETRY_CAP_S for d in rec)


def test_r164_durable_saturated_dispatch_defers_with_jitter(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    from agent_civilization.core import agent_registry
    real_acquire = server.AGENT_REGISTRY.acquire
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) <= 2:
            raise agent_registry.Saturated("no capacity (test)")
        return real_acquire(*a, **k)
    monkeypatch.setattr(server.AGENT_REGISTRY, "acquire", flaky)
    monkeypatch.setattr(server, "TASK_RETRY_BASE_S", 0.2)
    with _client() as c:
        tid = _post(c, tenants.headers, "deferred").json()["task_id"]
        assert _wait_for(lambda: next((x["status"] == "completed" for x in db["tasks"] if x["task_id"] == tid), False))
        row = next(x for x in db["tasks"] if x["task_id"] == tid)
    # deferrals did not consume attempts and did not run the task twice
    assert len(calls) >= 3 and row["attempts"] == 1
    assert tid not in server._QUEUE_DEFERRALS and tid not in server._QUEUE_BACKOFF


def test_r164_durable_cancel_queued_never_runs_and_running_is_not_rerun(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    monkeypatch.setattr(server, "TASK_REAP_S", 0.1)
    log = []
    monkeypatch.setattr(server, "_pick_agent", lambda specialization: _HangAgent(log))
    with _client() as c:
        tid = _post(c, tenants.headers, "running").json()["task_id"]
        assert _wait_for(lambda: ("start", "running") in log)
        assert c.post(f"/api/tasks/{tid}/cancel", headers=tenants.headers).status_code == 200
        assert _wait_for(lambda: ("cancelled", "running") in log)
        time.sleep(1.0)
        assert [k for k, _ in log].count("start") == 1               # not recovered / re-run
        row = next(x for x in db["tasks"] if x["task_id"] == tid)
        assert row["status"] == "cancelled"
