"""R-164 — the three concurrency defects raised in the review of PR #189.

Each test reproduces its defect against the pre-fix code (17e7bd9; the run is
kept in docs/audit/M8/logs/raw/154_r164_review_prefix_repro.log) and passes
after the fix. They use only the behaviour-level entry points (guarded_call,
the keyed LLM client, POST /api/tasks), so the same file runs on both sides:

1. backpressure.CircuitBreaker: the late success of a call admitted BEFORE a
   Retry-After hold (or before the breaker opened) must not close the breaker
   or cut the hold short; in half-open only the probe's own outcome counts.
2. core/llm.py keyed path: requests waiting for a concurrency slot must not
   reach the provider if the calls ahead of them opened the breaker (failure
   threshold or Retry-After) while they waited.
3. server.py durable mode: AGOS_TASK_ACCEPT_RATE_PER_MIN must hold under
   concurrent POST /api/tasks — including storage failures and idempotent
   replays, which must give their reserved slot back.
"""
from __future__ import annotations

import asyncio
import json
import threading
import urllib.error

import pytest

import server
from agent_civilization.core import backpressure, llm
from tests import test_r163_durable_queue as r163
from tests.test_r163_durable_queue import _PostgREST, _enable_queue
from tests.test_r164_backpressure import _Clock, _keyed_server

# the R-163 fake-PostgREST fixtures, re-exported (assignment, not import:
# the test parameters of the same name are the fixture requests)
db = r163.db
tenants = r163.tenants
_clean_tasks = r163._clean_tasks          # autouse: clean TASKS/_QUEUE_STATE per test


def _http_429(retry_after: str):
    return urllib.error.HTTPError("http://provider.invalid", 429, "Too Many Requests",
                                  {"Retry-After": retry_after}, None)


def _in_thread(fn):
    out: list = []
    t = threading.Thread(target=lambda: out.append(backpressure.guarded_call("web", fn)), daemon=True)
    t.start()
    return t, out


# ------------------------------------------------ 1. stale success vs hold

def test_r164_review_late_success_does_not_lift_retry_after_hold():
    """A call in flight before the provider's 429 + Retry-After answers 200
    afterwards: the hold must stand for its full duration (before the fix the
    late success closed the breaker and the next call hit the provider)."""
    brk = backpressure.breaker("web")
    started, release = threading.Event(), threading.Event()

    def slow_ok():
        started.set()
        release.wait(5)
        return "late ok"
    t, out = _in_thread(slow_ok)
    assert started.wait(2)

    def limited():
        raise _http_429("30")
    with pytest.raises(urllib.error.HTTPError):
        backpressure.guarded_call("web", limited)
    assert brk.state() == "open"

    release.set()                       # the stale success arrives now
    t.join(5)
    assert out == ["late ok"]
    assert brk.state() == "open", "a stale success closed the breaker during Retry-After"
    assert brk.retry_after() > 25
    calls: list = []
    with pytest.raises(backpressure.CircuitOpen):
        backpressure.guarded_call("web", lambda: calls.append(1))
    assert calls == [], "a call reached the provider inside its Retry-After window"
    assert brk.snapshot()["stale"] >= 1


def test_r164_review_late_success_is_not_the_half_open_probe(monkeypatch):
    """Only the half-open probe may close the breaker: the late success of a
    call admitted while it was still closed changes nothing, and calls stay
    refused until the probe itself reports."""
    clk = _Clock()
    brk = backpressure.CircuitBreaker("web", threshold=1, reset_s=10, clock=clk)
    monkeypatch.setitem(backpressure._breakers, "web", brk)

    old_started, old_release = threading.Event(), threading.Event()

    def old_call():
        old_started.set()
        old_release.wait(5)
        return "old"
    t_old, out_old = _in_thread(old_call)
    assert old_started.wait(2)

    def down():
        raise urllib.error.URLError("connection refused")
    with pytest.raises(urllib.error.URLError):
        backpressure.guarded_call("web", down)
    assert brk.state() == "open"
    clk.t += 10
    assert brk.state() == "half_open"

    probe_started, probe_release = threading.Event(), threading.Event()

    def probe_call():
        probe_started.set()
        probe_release.wait(5)
        return "probe"
    t_probe, out_probe = _in_thread(probe_call)
    assert probe_started.wait(2)

    old_release.set()                   # stale success while the probe is out
    t_old.join(5)
    assert out_old == ["old"]
    assert brk.state() == "half_open", "a non-probe success closed the half-open breaker"
    calls: list = []
    with pytest.raises(backpressure.CircuitOpen):
        backpressure.guarded_call("web", lambda: calls.append(1))
    assert calls == []

    probe_release.set()                 # the probe's own success closes it
    t_probe.join(5)
    assert out_probe == ["probe"] and brk.state() == "closed"
    assert backpressure.guarded_call("web", lambda: "after") == "after"


# ------------------------------- 2. queued keyed LLM calls vs open breaker

def _keyed_burst(monkeypatch, first_status: int, first_headers: dict, threshold: str | None):
    """12 concurrent keyed calls with AGOS_LLM_CONCURRENCY=8: the provider
    answers the FIRST call at once with ``first_status`` (opening the
    breaker) and the other in-flight ones late with 200. Returns (provider
    hits, results, a follow-up result, hits after the follow-up)."""
    from aiohttp import web
    if threshold is not None:
        monkeypatch.setenv("AGOS_BREAKER_LLM_THRESHOLD", threshold)
    backpressure.reset_all()
    monkeypatch.setattr(llm, "KEYED_CONCURRENCY", 8)
    monkeypatch.setattr(llm, "_keyed_sem", None)
    hits = {"n": 0}

    async def handler(request):
        hits["n"] += 1
        if hits["n"] == 1:
            return web.Response(status=first_status, text="no", headers=first_headers)
        await asyncio.sleep(0.4)
        return web.json_response({"choices": [{"message": {"content": "late ok"}}]})
    box = _keyed_server(handler)
    url = f"http://127.0.0.1:{box['port']}/v1/chat/completions"

    async def scenario():
        client = llm.LLMClient(api_key="r164-fake-key", url=url)
        res = await asyncio.gather(*[client.complete("s", f"p{i}", 20, max_wait_s=5.0) for i in range(12)])
        after = await client.complete("s", "after", 20, max_wait_s=5.0)
        await client.close()
        return list(res), after
    try:
        res, after = asyncio.run(scenario())
    finally:
        box["loop"].call_soon_threadsafe(box["loop"].stop)
    return hits["n"], res, after


def test_r164_review_queued_llm_calls_do_not_pass_an_opened_breaker(monkeypatch):
    """The first in-flight call's 503 opens the breaker (threshold 1): the 4
    calls queued behind the concurrency limit must be refused, not sent."""
    n, res, after = _keyed_burst(monkeypatch, 503, {}, threshold="1")
    assert n == 8, f"{n - 8} queued request(s) reached the provider after the breaker opened"
    busy = [r for r in res if r.get("busy")]
    assert len(busy) == 4 and all(r["retry_after"] >= 1 for r in busy)
    assert sum(1 for r in res if r["ok"]) == 7          # the late in-flight ones still answer
    assert after.get("busy") is True


def test_r164_review_queued_llm_calls_respect_a_retry_after_from_ahead(monkeypatch):
    """The first in-flight call gets 429 + Retry-After: 30; the 7 others
    answer 200 later (stale successes). The 4 queued calls, and any call
    after them, must not reach the provider inside those 30 s."""
    n, res, after = _keyed_burst(monkeypatch, 429, {"Retry-After": "30"}, threshold=None)
    assert n == 8, f"{n - 8} request(s) reached the provider inside Retry-After"
    busy = [r for r in res if r.get("busy")]
    assert len(busy) == 4 and all(r["retry_after"] >= 29 for r in busy)
    assert after.get("busy") is True and after["retry_after"] >= 29
    assert backpressure.breaker("llm").state() == "open"


# ------------------------------- 3. durable accept rate under concurrency

async def _asgi_post(path: str, body: dict, headers: dict):
    """One POST through the ASGI app (no network, real event-loop
    interleaving — the concurrency TestClient cannot give)."""
    raw = json.dumps(body).encode()
    hdrs = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]
    hdrs += [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": hdrs, "client": ("127.0.0.1", 50164),
             "server": ("testserver", 80)}
    sent = {"done": False}

    async def receive():
        if not sent["done"]:
            sent["done"] = True
            return {"type": "http.request", "body": raw, "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}
    out = {"status": None, "headers": {}, "body": b""}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = {k.decode().lower(): v.decode() for k, v in msg.get("headers", [])}
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")
    await server.app(scope, receive, send)
    out["json"] = json.loads(out["body"] or b"null")
    return out


def _burst(headers, bodies):
    async def go():
        try:
            return await asyncio.gather(*[_asgi_post("/api/tasks", b, headers) for b in bodies])
        finally:
            from agent_civilization.storage import supabase_store as ss
            if ss._storage is not None:
                await ss._storage.close()
                ss._storage = None
    return asyncio.run(go())


def _rate_refused(r) -> bool:
    return r["status"] == 429 and "task acceptance rate exceeded" in (r["json"] or {}).get("detail", "")


@pytest.fixture
def durable_rate(tenants, db, monkeypatch):
    _enable_queue(monkeypatch)
    monkeypatch.setattr(server, "TASK_ACCEPT_RATE_PER_MIN", 3)
    server._ACCEPTED_AT.clear()
    server.request_counts.clear()
    yield tenants
    server._ACCEPTED_AT.clear()


def _new(i):
    return {"specialization": "analyst", "prompt": f"R-164 review {i}"}


def test_r164_review_durable_accept_rate_holds_under_concurrency(durable_rate, db):
    res = _burst(durable_rate.headers, [_new(i) for i in range(10)])
    accepted = [r for r in res if r["status"] == 202]
    refused = [r for r in res if _rate_refused(r)]
    assert len(accepted) == 3, f"{len(accepted)} concurrent tasks accepted past a rate of 3/min"
    assert len(refused) == 7 and all(int(r["headers"]["retry-after"]) >= 1 for r in refused)
    assert len(db["tasks"]) == 3


def test_r164_review_storage_failure_gives_the_slot_back(durable_rate, db, monkeypatch):
    """Inserts fail (store answers 500 on POST). While the 3 admitted
    requests are in flight they HOLD their reservations (so the 3 others are
    refused by the rate, not let through); each failed insert is a 503 and
    gives its slot back — afterwards no quota is consumed and the next
    concurrent burst still gets exactly the full 3 slots."""
    monkeypatch.setenv("AGOS_BREAKER_STORAGE_THRESHOLD", "0")   # keep the store reachable
    backpressure.reset_all()
    _PostgREST.mode[0] = "reject_insert"
    res = _burst(durable_rate.headers, [_new(i) for i in range(6)])
    failed = [r for r in res if r["status"] == 503]
    assert len(failed) == 3 and all("storage unavailable" in r["json"]["detail"] for r in failed)
    assert sum(1 for r in res if _rate_refused(r)) == 3
    assert db["tasks"] == [] and server._ACCEPTED_AT.get("test_tenant", []) == []
    _PostgREST.mode[0] = "ok"
    res = _burst(durable_rate.headers, [_new(10 + i) for i in range(6)])
    assert sum(1 for r in res if r["status"] == 202) == 3
    assert sum(1 for r in res if _rate_refused(r)) == 3
    assert len(db["tasks"]) == 3


def test_r164_review_replays_never_refused_and_never_charged(durable_rate, db):
    """One task accepted with key K (1 slot). Then, concurrently: 5 replays
    of K and 5 new tasks. Replays are always 202 duplicate (the same task)
    and charge nothing, so exactly 2 of the new tasks fit the rate of 3."""
    first = _burst(durable_rate.headers, [{**_new(0), "idempotency_key": "r164-k"}])[0]
    assert first["status"] == 202
    tid = first["json"]["task_id"]
    bodies = [{**_new(0), "idempotency_key": "r164-k"} for _ in range(5)] + [_new(i) for i in range(1, 6)]
    res = _burst(durable_rate.headers, bodies)
    replays, fresh = res[:5], res[5:]
    assert all(r["status"] == 202 and r["json"]["duplicate"] is True and r["json"]["task_id"] == tid
               for r in replays)
    assert sum(1 for r in fresh if r["status"] == 202) == 2
    assert sum(1 for r in fresh if _rate_refused(r)) == 3
    assert len(db["tasks"]) == 3


def test_r164_review_concurrent_same_key_charges_one_slot(durable_rate, db):
    """3 concurrent first submissions of one NEW key: the unique index lets
    one insert win and the others become replays (insert 409 -> replay) —
    those give their reserved slot back, so 2 more tasks still fit."""
    res = _burst(durable_rate.headers, [{**_new(0), "idempotency_key": "r164-race"} for _ in range(3)])
    ok = [r for r in res if r["status"] == 202]
    assert len(ok) == 3 and len({r["json"]["task_id"] for r in ok}) == 1
    assert sum(1 for r in ok if r["json"].get("duplicate")) == 2
    assert len([t for t in db["tasks"] if t.get("idempotency_key") == "r164-race"]) == 1
    res = _burst(durable_rate.headers, [_new(i) for i in range(1, 4)])
    assert sum(1 for r in res if r["status"] == 202) == 2
    assert sum(1 for r in res if _rate_refused(r)) == 1


# ------------------------- storage: each request reports with its own permit

def test_r164_review_storage_requests_report_with_their_own_permit(monkeypatch):
    """The storage breaker is fed by aiohttp trace hooks: each HTTP request
    takes its permit in on_request_start and reports with it. A request that
    started before the breaker opened cannot close the half-open breaker,
    and a cancelled request (caller timeout) is not a storage failure — it
    only frees the probe slot. (New hooks: no pre-fix counterpart.)"""
    from types import SimpleNamespace as NS
    from agent_civilization.storage import supabase_store as ss
    clk = _Clock()
    brk = backpressure.CircuitBreaker("storage", threshold=1, reset_s=10, clock=clk)
    monkeypatch.setitem(backpressure._breakers, "storage", brk)
    run = asyncio.run
    ok200, err = NS(response=NS(status=200)), NS(exception=OSError("refused"))

    old = NS()
    run(ss._breaker_on_start(None, old, None))          # admitted while closed
    other = NS()
    run(ss._breaker_on_start(None, other, None))
    run(ss._breaker_on_exception(None, other, err))     # -> open (threshold 1)
    assert brk.state() == "open"
    with pytest.raises(backpressure.CircuitOpen):
        run(ss._breaker_on_start(None, NS(), None))     # refused before any I/O
    clk.t += 10
    probe = NS()
    run(ss._breaker_on_start(None, probe, None))
    assert probe.permit.probe
    run(ss._breaker_on_end(None, old, ok200))           # stale success
    assert brk.state() == "half_open"
    run(ss._breaker_on_exception(None, probe, NS(exception=asyncio.CancelledError())))
    assert brk.state() == "half_open" and brk.snapshot()["opened"] == 1   # not a failure
    again = NS()
    run(ss._breaker_on_start(None, again, None))        # the slot was freed
    run(ss._breaker_on_end(None, again, ok200))
    assert brk.state() == "closed"
