"""SEC-04 (R-144): tenant SSE broadcast and connection bounds.

Before R-144 (``TASK_EVENTS``): one unbounded ``asyncio.Queue`` per tenant,
shared by every SSE connection of that tenant — an event reached ONE of the
tenant's listeners, events piled up with no listener, and the stream route had
no rate limit and no connection / lifetime bounds.

Three levels:
  * HTTP: a real uvicorn server on loopback, real concurrent SSE connections
    (aiohttp), events published on the server's own loop. These run unchanged
    on the old code and fail there for behavioural reasons. Client: aiohttp
    (a runtime dependency already).
  * The real route (``server.event_stream``) called with a ``Request``: the
    real generator, a controllable clock and short limits — no long sleeps.
  * ``EventBus`` itself: drop policy, admission, tracking-map bound, config.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time

import aiohttp
import pytest

import server

SSE = "/api/events/stream"


# ------------------------------------------------------------ helpers

def _bus(**limits):
    """A fresh bus on the server (fake clock); old code: no-op (None)."""
    try:
        from agent_civilization.core.sse import EventBus, SSELimits
    except ImportError:
        return None
    clock = [1000.0]
    bus = EventBus(SSELimits(**limits), clock=lambda: clock[0], on_metric=server._sse_metric)
    bus.fake_clock = clock
    return bus


@pytest.fixture
def bus(monkeypatch):
    def make(**limits):
        b = _bus(**limits)
        if b is not None:
            monkeypatch.setattr(server, "EVENT_BUS", b)
        return b
    make.default = make()
    return make


@pytest.fixture(scope="module")
def live():
    """A real uvicorn server on loopback; ``live.publish`` runs the server's
    ``_publish`` on the server's own event loop."""
    import uvicorn
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    userver = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port,
                                            log_level="warning", lifespan="off"))
    holder = {}

    def run():
        loop = asyncio.new_event_loop()
        holder["loop"] = loop
        loop.run_until_complete(userver.serve())

    t = threading.Thread(target=run, daemon=True)
    t.start()
    for _ in range(500):
        if userver.started:
            break
        time.sleep(0.01)
    assert userver.started

    class Live:
        base = f"http://127.0.0.1:{port}"

        def publish(self, tenant, event):
            fut = asyncio.run_coroutine_threadsafe(server._publish(tenant, event), holder["loop"])
            fut.result(timeout=5)

    yield Live()
    userver.should_exit = True
    t.join(timeout=10)


class _Listener:
    """One real SSE connection read in the background."""

    def __init__(self, client, headers):
        self.client, self.headers = client, headers
        self.events, self.status, self.ended = [], None, False
        self.ready = asyncio.Event()

    async def run(self):
        try:
            async with self.client.get(SSE, headers=self.headers) as r:
                self.status = r.status
                self.retry_after = r.headers.get("Retry-After")
                if r.status != 200:
                    self.body = await r.text()
                    self.ready.set()
                    return
                self.ready.set()
                async for raw in r.content:
                    line = raw.decode().rstrip("\n")
                    if line.startswith("data: "):
                        self.events.append(json.loads(line[6:]))
        finally:
            self.ended = True
            self.ready.set()

    def types(self, drop_heartbeat=True):
        return [e.get("type") for e in self.events if not (drop_heartbeat and e.get("type") == "heartbeat")]


async def _connect(client, headers):
    lst = _Listener(client, headers)
    lst.task = asyncio.ensure_future(lst.run())
    await asyncio.wait_for(lst.ready.wait(), timeout=5)
    return lst


async def _settle(pred, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return pred()


async def _wait_subscribed(tenant, n):
    """New code: until the bus has n subscriptions; old code: a short pause."""
    if hasattr(server, "EVENT_BUS"):
        assert await _settle(lambda: server.EVENT_BUS.subscribers(tenant) >= n)
    else:
        await asyncio.sleep(0.3)


async def _stop(*listeners):
    for lst in listeners:
        lst.task.cancel()
    await asyncio.gather(*(lst.task for lst in listeners), return_exceptions=True)


async def _serve_then_disconnect(resp):
    """Run the response as the ASGI server would; the client disconnects at once."""
    async def receive():
        return {"type": "http.disconnect"}

    async def send(msg):
        pass
    await resp({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, send)


def _request(host="127.0.0.1"):
    from starlette.requests import Request
    return Request({"type": "http", "method": "GET", "path": SSE, "headers": [], "query_string": b"",
                    "client": (host, 1)})


# ====================================================== HTTP (real server)

def test_two_listeners_of_one_tenant_each_receive_every_event_and_other_tenant_none(
        live, tenant_auth, bus, monkeypatch):
    """Broadcast + isolation over real concurrent connections."""
    import hashlib
    import secrets
    key_b = "sb-" + secrets.token_urlsafe(16)
    creds = dict(server.CREDENTIALS)
    creds[hashlib.sha256(key_b.encode()).hexdigest()] = server.Principal("other_tenant", "user")
    monkeypatch.setattr(server, "CREDENTIALS", creds)

    async def go():
        async with aiohttp.ClientSession(base_url=live.base) as c:
            a1 = await _connect(c, tenant_auth.headers)
            a2 = await _connect(c, tenant_auth.headers)
            b1 = await _connect(c, {"X-API-Key": key_b})
            await _wait_subscribed("test_tenant", 2)
            await _wait_subscribed("other_tenant", 1)
            for i in range(5):
                live.publish("test_tenant", {"type": f"a-{i}"})
            for i in range(2):
                live.publish("other_tenant", {"type": f"b-{i}"})
            await _settle(lambda: len(a1.types()) >= 5 and len(a2.types()) >= 5 and len(b1.types()) >= 2,
                          timeout=3)
            await _stop(a1, a2, b1)
            return a1, a2, b1
    a1, a2, b1 = asyncio.run(go())
    assert (a1.status, a2.status, b1.status) == (200, 200, 200)
    assert a1.types() == [f"a-{i}" for i in range(5)]
    assert a2.types() == [f"a-{i}" for i in range(5)]
    assert b1.types() == ["b-0", "b-1"]


def test_real_task_events_reach_both_listeners(live, tenant_auth, bus):
    """Task execution still publishes; both listeners get the task's events."""
    async def go():
        async with aiohttp.ClientSession(base_url=live.base) as c:
            a1 = await _connect(c, tenant_auth.headers)
            a2 = await _connect(c, tenant_auth.headers)
            await _wait_subscribed("test_tenant", 2)
            async with c.post("/api/tasks", json={"specialization": "analyst", "prompt": "SEC-04: 1 2 3"},
                              headers=tenant_auth.headers) as r:
                tid = (await r.json())["task_id"]

            def done(lst):
                return any(e.get("type") == "task_completed" and e.get("task_id") == tid for e in lst.events)
            await _settle(lambda: done(a1) and done(a2), timeout=10)
            await _stop(a1, a2)
            return tid, a1, a2
    tid, a1, a2 = asyncio.run(go())
    for lst in (a1, a2):
        mine = [e["type"] for e in lst.events if e.get("task_id") == tid]
        assert mine == ["task_created", "task_started", "task_completed"], mine


def test_no_listener_nothing_is_kept(live, tenant_auth, bus):
    """Events published while nobody listens are not queued for later."""
    for i in range(5):
        live.publish("test_tenant", {"type": f"backlog-{i}"})

    async def go():
        async with aiohttp.ClientSession(base_url=live.base) as c:
            lst = await _connect(c, tenant_auth.headers)
            await asyncio.sleep(0.8)
            await _stop(lst)
            return lst
    lst = asyncio.run(go())
    assert lst.status == 200
    assert lst.types() == [], lst.types()
    if bus.default is not None:
        assert bus.default.stats["no_subscriber"] == 5


def test_per_client_limit_refused_with_429_before_stream(live, tenant_auth, bus):
    """Default AGOS_SSE_MAX_PER_CLIENT=5: the 6th concurrent connection from
    one address is refused with 429 + Retry-After and a fixed message."""
    async def go():
        async with aiohttp.ClientSession(base_url=live.base) as c:
            ok = [await _connect(c, tenant_auth.headers) for _ in range(5)]
            sixth = await _connect(c, tenant_auth.headers)
            await _stop(*ok, sixth)
            return ok, sixth
    ok, sixth = asyncio.run(go())
    assert [x.status for x in ok] == [200] * 5
    assert sixth.status == 429
    assert sixth.retry_after is not None and int(sixth.retry_after) >= 1
    assert json.loads(sixth.body) == {"detail": "too many event streams, retry later"}
    assert sixth.events == []


def test_disconnect_releases_capacity(live, tenant_auth, bus):
    b = bus(max_per_client=1, max_per_tenant=1, max_connections=1)

    async def go():
        async with aiohttp.ClientSession(base_url=live.base) as c:
            first = await _connect(c, tenant_auth.headers)
            await _wait_subscribed("test_tenant", 1)
            refused = await _connect(c, tenant_auth.headers)
            await _stop(first, refused)              # client closes the connection
            released = await _settle(lambda: b.total == 0)
            again = await _connect(c, tenant_auth.headers)
            await _stop(again)
            await _settle(lambda: b.total == 0)
            return refused, released, again
    refused, released, again = asyncio.run(go())
    assert refused.status == 429
    assert released
    assert again.status == 200
    assert b.total == 0 and b.stats["opened"] == b.stats["closed"] == 2


def test_unauthenticated_refused_without_subscription_or_capacity(live, tenant_auth, bus):
    async def go():
        async with aiohttp.ClientSession(base_url=live.base) as c:
            out = []
            for h in ({}, {"X-API-Key": ""}, {"X-API-Key": "wrong-key-not-configured"}):
                out.append(await _connect(c, h))
            await _stop(*out)
            return out
    out = asyncio.run(go())
    assert [x.status for x in out] == [401, 401, 401]
    if bus.default is not None:
        snap = bus.default.snapshot()
        assert snap["connections"] == 0 and snap["opened"] == 0 and snap["tracked_clients"] == 0


def test_stream_route_has_general_rate_limit(live, tenant_auth, bus, monkeypatch):
    """@rate_limit (AGOS_RATE_LIMIT_REQUESTS per window) now covers the stream."""
    monkeypatch.setattr(server, "RATE_LIMIT_REQUESTS", 2)

    async def go():
        async with aiohttp.ClientSession(base_url=live.base) as c:
            out = [await _connect(c, {}) for _ in range(3)]
            await _stop(*out)
            return out
    out = asyncio.run(go())
    assert [x.status for x in out] == [401, 401, 429]


def test_lifetime_ends_stream_and_releases(live, tenant_auth, bus):
    b = bus(max_lifetime_s=10, heartbeat_s=1)

    async def go():
        async with aiohttp.ClientSession(base_url=live.base) as c:
            lst = await _connect(c, tenant_auth.headers)
            await _wait_subscribed("test_tenant", 1)
            b.fake_clock[0] += 11                    # past the max lifetime
            ended = await _settle(lambda: lst.ended, timeout=4)
            await _stop(lst)
            return lst, ended
    lst, ended = asyncio.run(go())
    assert lst.status == 200 and ended
    assert b.total == 0 and b.stats["closed"] == 1


# ================================================ the real route, direct

def test_heartbeat_when_idle_and_format(tenant_auth, bus):
    bus(heartbeat_s=1)

    async def go():
        resp = await server.event_stream(_request(), api_key=tenant_auth.key)
        it = resp.body_iterator
        chunk = await asyncio.wait_for(it.__anext__(), timeout=3)
        await it.aclose()
        return chunk
    assert asyncio.run(go()) == 'data: {"type": "heartbeat"}\n\n'


def test_full_queue_drops_oldest_reports_gap_and_never_blocks_publisher(tenant_auth, bus):
    b = bus(queue_size=3)

    async def go():
        slow = await server.event_stream(_request(), api_key=tenant_auth.key)
        fast_sub = b.open("test_tenant", "other-host")
        fast_seen = []
        for i in range(10):
            await asyncio.wait_for(server._publish("test_tenant", {"type": "task_created", "task_id": f"t{i}"}),
                                   timeout=0.05)          # never waits for the slow listener
            fast_seen.append(fast_sub.queue.get_nowait()["task_id"])
        slow_sub = next(s for s in b._subs["test_tenant"] if s is not fast_sub)
        assert slow_sub.queue.qsize() == 3 and slow_sub.queue.maxsize == 3
        it = slow.body_iterator
        chunks = [await asyncio.wait_for(it.__anext__(), timeout=2) for _ in range(4)]
        await it.aclose()
        b.close(fast_sub)
        return fast_seen, chunks, slow_sub
    fast_seen, chunks, slow_sub = asyncio.run(go())
    assert fast_seen == [f"t{i}" for i in range(10)]            # other listener unaffected
    events = [json.loads(c[len("data: "):]) for c in chunks]
    assert events[0] == {"type": "stream_gap", "missed": 7, "recover": ["/api/tasks", "/api/events"]}
    assert events[1:] == [{"type": "task_created", "task_id": f"t{i}"} for i in (7, 8, 9)]  # newest kept
    assert slow_sub.dropped == 7 and b.stats["dropped"] == 7
    from agos_kernel.infrastructure.metrics import get_metrics
    assert get_metrics()._counters.get(get_metrics()._make_key("sse_events_dropped_total", None), 0) >= 7
    assert b.total == 0


@pytest.mark.parametrize("limit,opens", [
    (dict(max_connections=2, max_per_tenant=2, max_per_client=2), [("test_tenant", "h1"), ("t2", "h2")]),
    (dict(max_per_tenant=2), [("test_tenant", "h1"), ("test_tenant", "h2")]),
    (dict(max_per_client=2), [("t2", "127.0.0.1"), ("t3", "127.0.0.1")]),
], ids=["total", "per_tenant", "per_client"])
def test_capacity_limits_refuse_before_stream(tenant_auth, bus, limit, opens):
    b = bus(**limit)
    held = [b.open(t, h) for t, h in opens]
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        asyncio.run(server.event_stream(_request("127.0.0.1"), api_key=tenant_auth.key))
    assert e.value.status_code == 429
    assert e.value.detail == "too many event streams, retry later"
    assert e.value.headers == {"Retry-After": "30"}
    assert b.total == 2 and b.subscribers("test_tenant") == sum(t == "test_tenant" for t, _ in opens)
    for s in held:
        b.close(s)
    assert b.snapshot()["connections"] == 0 and b.snapshot()["clients"] == 0


def test_open_rate_refuses_then_allows_after_window(tenant_auth, bus):
    b = bus(open_rate=2, open_window_s=60)
    from fastapi import HTTPException

    async def open_close():
        resp = await server.event_stream(_request(), api_key=tenant_auth.key)
        await _serve_then_disconnect(resp)

    asyncio.run(open_close())
    b.fake_clock[0] += 10
    asyncio.run(open_close())
    b.fake_clock[0] += 5
    with pytest.raises(HTTPException) as e:
        asyncio.run(open_close())
    assert e.value.status_code == 429 and e.value.headers == {"Retry-After": "45"}   # first open + 60 - now
    assert b.total == 0
    b.fake_clock[0] += 45                            # the first open leaves the window
    asyncio.run(open_close())
    assert b.stats["refused_rate"] == 1 and b.stats["opened"] == 3


def test_cleanup_once_on_cancel_exception_and_unstarted_response(tenant_auth, bus, monkeypatch):
    b = bus()
    from agent_civilization.core import sse

    async def cancelled():
        resp = await server.event_stream(_request(), api_key=tenant_auth.key)
        t = asyncio.ensure_future(resp.body_iterator.__anext__())
        await asyncio.sleep(0.05)
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
        await resp.body_iterator.aclose()

    asyncio.run(cancelled())
    assert b.total == 0 and b.stats["closed"] == 1

    async def boom(self, timeout):
        raise RuntimeError("listener failure")
    original_next = sse.Subscription.next
    monkeypatch.setattr(sse.Subscription, "next", boom)

    async def failing():
        resp = await server.event_stream(_request(), api_key=tenant_auth.key)
        with pytest.raises(RuntimeError):
            await resp.body_iterator.__anext__()
    asyncio.run(failing())
    assert b.total == 0 and b.stats["closed"] == 2
    monkeypatch.setattr(sse.Subscription, "next", original_next)

    async def never_iterated():
        resp = await server.event_stream(_request(), api_key=tenant_auth.key)
        assert b.total == 1

        async def receive():
            return {"type": "http.disconnect"}

        async def send(msg):
            raise OSError("peer gone")
        with pytest.raises(Exception):
            await resp({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    asyncio.run(never_iterated())
    assert b.total == 0 and b.stats["closed"] == 3
    sub = b.open("test_tenant", "h")
    assert b.close(sub) is True and b.close(sub) is False      # idempotent
    assert b.stats["closed"] == 4 and b._subs == {} and b._per_client == {}


def test_admin_stream_is_own_tenant_only(tenant_auth, bus):
    """No widening: an admin's stream carries the admin's tenant only, even
    with ?all_tenants=true (the route takes no such parameter)."""
    b = bus()

    async def go():
        resp = await server.event_stream(_request(), api_key=tenant_auth.admin_key)
        await server._publish("test_tenant", {"type": "tenant-secret"})
        await server._publish("test_admin", {"type": "admin-own"})
        chunk = await asyncio.wait_for(resp.body_iterator.__anext__(), timeout=2)
        await resp.body_iterator.aclose()
        return chunk
    assert json.loads(asyncio.run(go())[6:]) == {"type": "admin-own"}
    assert b.stats["no_subscriber"] == 1


# ===================================================== EventBus / config

def test_tracking_map_is_bounded(bus, monkeypatch):
    from agent_civilization.core import sse
    b = bus(open_window_s=60)
    for i in range(1000):
        b.close(b.open("test_tenant", f"10.0.{i // 256}.{i % 256}"))
    assert len(b._opens) == 1000
    b.fake_clock[0] += 61
    b.close(b.open("test_tenant", "fresh"))
    assert len(b._opens) == 1                                   # stale entries pruned
    monkeypatch.setattr(sse, "MAX_TRACKED_CLIENTS", 50)
    for i in range(49):
        b.close(b.open("test_tenant", f"c{i}"))
    assert len(b._opens) == 50
    with pytest.raises(sse.StreamLimit) as e:
        b.open("test_tenant", "c-overflow")                     # fail closed, map not grown
    assert e.value.reason == "tracking_full" and len(b._opens) == 50


@pytest.mark.parametrize("var,value", [
    ("AGOS_SSE_QUEUE_SIZE", "0"), ("AGOS_SSE_QUEUE_SIZE", "-5"), ("AGOS_SSE_QUEUE_SIZE", "abc"),
    ("AGOS_SSE_QUEUE_SIZE", "100000000"), ("AGOS_SSE_MAX_CONNECTIONS", "0"),
    ("AGOS_SSE_MAX_PER_TENANT", "0"), ("AGOS_SSE_MAX_PER_CLIENT", "-1"), ("AGOS_SSE_OPEN_RATE", "0"),
    ("AGOS_SSE_OPEN_WINDOW", "0"), ("AGOS_SSE_MAX_LIFETIME", "0"), ("AGOS_SSE_MAX_LIFETIME", "9999999"),
    ("AGOS_SSE_HEARTBEAT", "0"), ("AGOS_SSE_MAX_PER_TENANT", "500"), ("AGOS_SSE_OPEN_RATE", "1.5"),
])
def test_invalid_limits_rejected(var, value):
    from agent_civilization.core.sse import SSELimits
    with pytest.raises(ValueError) as e:
        SSELimits.from_env({var: value})
    assert var in str(e.value)


def test_defaults_and_valid_override():
    from agent_civilization.core.sse import SSELimits
    d = SSELimits.from_env({})
    assert (d.queue_size, d.max_connections, d.max_per_tenant, d.max_per_client, d.open_rate,
            d.open_window_s, d.max_lifetime_s, d.heartbeat_s) == (100, 100, 10, 5, 10, 60, 3600, 30)
    assert SSELimits.from_env({"AGOS_SSE_QUEUE_SIZE": " 250 "}).queue_size == 250


def test_server_refuses_to_start_with_invalid_limit():
    env = {**os.environ, "AGOS_ENV": "test", "AGOS_SSE_MAX_CONNECTIONS": "0"}
    env.pop("AGOS_TENANT_KEYS", None)
    r = subprocess.run([sys.executable, "-c", "import server"], env=env, capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))), timeout=120)
    assert r.returncode != 0
    assert "AGOS_SSE_MAX_CONNECTIONS must be an integer in [1, 10000], got 0" in r.stderr
