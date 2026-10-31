"""SSE subscription race (log 294 §75 / prereg S0 §6.1): events published before
the subscription is fully established are lost to the subscriber (no replay).

The S0 smoke run lost 8/20 terminal events and blamed a client-side race —
"the stream's HTTP 200 precedes the server's subscription (no replay)" — then
masked it with a 1-second stabilization sleep in scripts/r171_local_load.py.
These tests reproduce the loss WITHOUT that sleep, at every window where the
subscription could still be incomplete:

  1. HTTP level: connect, wait ONLY for the 200 headers, then immediately
     publish distinct events on the server's own loop — every id must arrive
     exactly once.
  2. Route level: the subscription is registered (``open()`` ran inside the
     handler) but the body generator has not started — an event published
     before the first ``__anext__()`` must still be delivered.
  3. The load experiment's exact flow: connect, wait for headers only (no
     1 s sleep, no server-internal subscriber check), then create a
     deterministic task over HTTP — its terminal event must arrive.

If these fail on main the loss is reproduced (red). If they pass, the loss is
NOT in the subscription-registration window and the search moves to the event
production / reception path — that outcome is recorded either way in the
evidence log, not assumed.
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
import threading
import time

import aiohttp
import pytest

import server

SSE = "/api/events/stream"


# ------------------------------------------------------------ helpers
# (same pattern as tests/test_sec04_sse_broadcast_bounds.py — a real uvicorn
# server on loopback; self-contained here so this file stays independent)

@pytest.fixture(scope="module")
def live():
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


@pytest.fixture
def bus(monkeypatch):
    """A fresh bus on the server (isolation from other tests' opens)."""
    from agent_civilization.core.sse import EventBus, SSELimits
    b = EventBus(SSELimits(), clock=lambda: 1000.0, on_metric=server._sse_metric)
    monkeypatch.setattr(server, "EVENT_BUS", b)
    return b


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
                if r.status != 200:
                    self.body = await r.text()
                    self.ready.set()
                    return
                self.ready.set()  # headers seen — the only signal a real client has
                async for raw in r.content:
                    line = raw.decode().rstrip("\n")
                    if line.startswith("data: "):
                        self.events.append(json.loads(line[6:]))
        finally:
            self.ended = True
            self.ready.set()


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


async def _stop(*listeners):
    for lst in listeners:
        lst.task.cancel()
    await asyncio.gather(*(lst.task for lst in listeners), return_exceptions=True)


def _request(host="127.0.0.1"):
    from starlette.requests import Request
    return Request({"type": "http", "method": "GET", "path": SSE, "headers": [], "query_string": b"",
                    "client": (host, 1)})


# ====================================================== HTTP (real server)

def test_events_published_right_after_200_headers_all_arrive(live, tenant_auth, bus):
    """Window 1: the client saw ONLY the 200 headers — no sleep, no first
    event, no server-internal check — and events are published immediately.
    Every id must arrive exactly once (the S0 smoke run lost 8/20 here)."""
    ids = [f"race-{i}" for i in range(10)]

    async def go():
        async with aiohttp.ClientSession(base_url=live.base) as c:
            lst = await _connect(c, tenant_auth.headers)
            try:
                assert lst.status == 200
                for i in ids:  # immediately — no stabilization sleep
                    live.publish(tenant_auth.tenant, {"type": "race_probe", "id": i})
                ok = await _settle(
                    lambda: all(any(e.get("id") == i for e in lst.events) for i in ids),
                    timeout=5)
            finally:
                await _stop(lst)
            return ok, lst
    ok, lst = asyncio.run(go())
    assert ok, lst.events
    seen = [e.get("id") for e in lst.events if e.get("type") == "race_probe"]
    assert sorted(seen) == sorted(ids), seen
    assert len(seen) == len(ids), seen  # each exactly once


def test_task_created_right_after_200_headers_gets_its_terminal_event(live, tenant_auth, bus):
    """Window 3 — the load experiment's exact flow: connect, wait for headers
    only (no 1 s sleep), then create a deterministic task over HTTP. The
    accepted task's terminal event must arrive on the stream."""
    async def go():
        async with aiohttp.ClientSession(base_url=live.base) as c:
            lst = await _connect(c, tenant_auth.headers)
            try:
                assert lst.status == 200
                async with c.post("/api/tasks",
                                 json={"specialization": "analyst", "prompt": "SSE race: 1 2 3"},
                                 headers=tenant_auth.headers) as r:
                    tid = (await r.json())["task_id"]

                def done():
                    return any(e.get("type") == "task_completed" and e.get("task_id") == tid
                               for e in lst.events)
                ok = await _settle(done, timeout=15)
            finally:
                await _stop(lst)
            return ok, tid, lst
    ok, tid, lst = asyncio.run(go())
    assert ok, [e for e in lst.events if e.get("task_id") == tid]
    mine = [e["type"] for e in lst.events if e.get("task_id") == tid]
    assert mine == ["task_created", "task_started", "task_completed"], mine


def test_burst_of_tasks_right_after_200_headers_all_terminal_events_arrive(live, tenant_auth, bus):
    """Window 3b — the S0 smoke run itself: ONE stream, MANY concurrent
    submitters, no stabilization sleep. Every accepted task's terminal event
    must arrive exactly once on that stream (the smoke run lost 8/20)."""
    N = 20

    async def go():
        async with aiohttp.ClientSession(base_url=live.base) as c:
            lst = await _connect(c, tenant_auth.headers)
            try:
                assert lst.status == 200

                async def submit(i):
                    async with c.post("/api/tasks",
                                     json={"specialization": "analyst", "prompt": f"SSE race burst {i}"},
                                     headers=tenant_auth.headers) as r:
                        body = await r.json()
                        return body.get("task_id") if r.status == 202 else None

                tids = await asyncio.gather(*(submit(i) for i in range(N)))
                accepted = [t for t in tids if t]
                assert len(accepted) == N, (len(accepted), tids)

                def done():
                    seen = [e.get("task_id") for e in lst.events
                            if e.get("type") == "task_completed"]
                    return all(t in seen for t in accepted)
                ok = await _settle(done, timeout=30)
            finally:
                await _stop(lst)
            return ok, accepted, lst
    ok, accepted, lst = asyncio.run(go())
    assert ok, [e.get("type") for e in lst.events]
    terminals = [e.get("task_id") for e in lst.events if e.get("type") == "task_completed"]
    for t in accepted:
        assert terminals.count(t) == 1, (t, terminals.count(t))  # each exactly once


# ====================================================== route (no server)

def test_event_published_before_first_anext_is_delivered(tenant_auth, bus):
    """Window 2: the subscription is registered but the body generator has not
    started. An event published before the first __anext__() must still be
    delivered (it must not be lost to a not-yet-running generator)."""
    probe = {"type": "race_probe", "id": "before-first-anext"}

    async def go():
        resp = await server.event_stream(_request(), api_key=tenant_auth.key)
        # open() ran inside the handler; the generator has not started. Publish
        # on the same bus the route uses, then read the first chunk.
        bus.publish(tenant_auth.tenant, dict(probe))
        it = resp.body_iterator
        chunk = await asyncio.wait_for(it.__anext__(), timeout=3)
        await it.aclose()
        return chunk
    chunk = asyncio.run(go())
    # G-160-5 (D-109): a bus event is framed with its SSE id line first.
    assert re.fullmatch(r"id: [0-9a-f]{8}-\d+-\d+\n" + re.escape(f"data: {json.dumps(probe)}\n\n"),
                        chunk), chunk
