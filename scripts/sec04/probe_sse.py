"""SEC-04 (R-144) evidence probe — runs unchanged on the old and the new code.

A real uvicorn server (production mode, random keys: hashes only) on
loopback; real concurrent SSE connections (aiohttp); events published with
the server's own ``_publish`` on the server's loop. Sections:
  1. broadcast: two listeners of tenant_a + one of tenant_b;
  2. no listener: events published with nobody connected, then a listener;
  3. queue bound / publisher: 1000 events with no reader — kept? time?;
  4. concurrent connections from one client (7 attempts);
  5. open-rate: 12 sequential open+close from one client;
  6. release: capacity after disconnect.
Run: PYTHONPATH=. python3 scripts/sec04/probe_sse.py"""
import asyncio
import hashlib
import json
import os
import secrets
import socket
import threading
import time

KEYS = {"tenant_a": "pa-" + secrets.token_urlsafe(16), "tenant_b": "pb-" + secrets.token_urlsafe(16)}
os.environ.update({
    "AGOS_ENV": "production",
    "AGOS_TENANT_KEYS": json.dumps([{"tenant": t, "role": "user", "key_sha256": hashlib.sha256(k.encode()).hexdigest()}
                                    for t, k in KEYS.items()]),
    "AGOS_RATE_LIMIT_REQUESTS": "100000",   # keep the general limiter out of the way
})

import aiohttp  # noqa: E402
import uvicorn  # noqa: E402

import server  # noqa: E402

NEW = hasattr(server, "EVENT_BUS")
H = {t: {"X-API-Key": k} for t, k in KEYS.items()}


def fresh_bus():
    if NEW:
        from agent_civilization.core.sse import EventBus
        server.EVENT_BUS = EventBus(server.SSE_LIMITS, on_metric=server._sse_metric)


def internals(tenant):
    if NEW:
        b = server.EVENT_BUS
        subs = list(b._subs.get(tenant, ()))
        return (f"EVENT_BUS: connections={b.total}, subscriptions[{tenant}]={len(subs)}, "
                f"queue maxsize={[s.queue.maxsize for s in subs]}, stats={b.stats}")
    q = server.TASK_EVENTS.get(tenant)
    return f"TASK_EVENTS[{tenant}]: " + ("absent" if q is None else f"qsize={q.qsize()}, maxsize={q.maxsize} (0 = unbounded)")


s = socket.socket()
s.bind(("127.0.0.1", 0))
PORT = s.getsockname()[1]
s.close()
userver = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=PORT, log_level="warning", lifespan="off"))
LOOP = {}


def run():
    loop = asyncio.new_event_loop()
    LOOP["l"] = loop
    loop.run_until_complete(userver.serve())


threading.Thread(target=run, daemon=True).start()
while not userver.started:
    time.sleep(0.01)


def publish(tenant, event):
    asyncio.run_coroutine_threadsafe(server._publish(tenant, event), LOOP["l"]).result(5)


def on_loop(fn):
    async def call():
        return fn()
    return asyncio.run_coroutine_threadsafe(call(), LOOP["l"]).result(5)


class Listener:
    def __init__(self, c, h):
        self.c, self.h, self.events, self.status, self.retry_after = c, h, [], None, None
        self.ready = asyncio.Event()

    async def run(self):
        try:
            async with self.c.get("/api/events/stream", headers=self.h) as r:
                self.status, self.retry_after = r.status, r.headers.get("Retry-After")
                self.ready.set()
                if r.status != 200:
                    self.body = await r.text()
                    return
                async for raw in r.content:
                    line = raw.decode().strip()
                    if line.startswith("data: "):
                        e = json.loads(line[6:])
                        if e.get("type") != "heartbeat":
                            self.events.append(e.get("type"))
        finally:
            self.ready.set()


async def connect(c, h):
    lst = Listener(c, h)
    lst.task = asyncio.ensure_future(lst.run())
    await asyncio.wait_for(lst.ready.wait(), 5)
    return lst


async def stop(*ls):
    for lst in ls:
        lst.task.cancel()
    await asyncio.gather(*(lst.task for lst in ls), return_exceptions=True)


async def main():
    print(f"code: {'new (EVENT_BUS)' if NEW else 'old (TASK_EVENTS shared queue)'}; server http://127.0.0.1:{PORT} AGOS_ENV=production")
    async with aiohttp.ClientSession(base_url=f"http://127.0.0.1:{PORT}") as c:
        print("\n## 1. broadcast: listeners A1, A2 (tenant_a) and B1 (tenant_b) connected; 6 events to tenant_a, 2 to tenant_b")
        fresh_bus()
        a1, a2, b1 = await connect(c, H["tenant_a"]), await connect(c, H["tenant_a"]), await connect(c, H["tenant_b"])
        await asyncio.sleep(0.3)
        for i in range(6):
            publish("tenant_a", {"type": f"a{i}"})
        for i in range(2):
            publish("tenant_b", {"type": f"b{i}"})
        await asyncio.sleep(0.8)
        for name, lst in (("A1", a1), ("A2", a2), ("B1", b1)):
            print(f"{name} status={lst.status} received={lst.events}")
        print("tenant_b events seen by A1/A2:", [e for e in a1.events + a2.events if e.startswith("b")],
              "| tenant_a events seen by B1:", [e for e in b1.events if e.startswith("a")])
        await stop(a1, a2, b1)
        await asyncio.sleep(0.3)

        print("\n## 2. no listener: 5 events to tenant_a while nobody is connected, then a listener connects (0.8 s)")
        fresh_bus()
        for i in range(5):
            publish("tenant_a", {"type": f"backlog{i}"})
        print("before connecting:", on_loop(lambda: internals("tenant_a")))
        lst = await connect(c, H["tenant_a"])
        await asyncio.sleep(0.8)
        print(f"late listener received={lst.events}")
        await stop(lst)
        await asyncio.sleep(0.3)

        print("\n## 3. queue bound / publisher: 1000 events to tenant_b with no reader")
        fresh_bus()
        t0 = time.perf_counter()
        for i in range(1000):
            publish("tenant_b", {"type": "flood", "i": i})
        print(f"published 1000 in {time.perf_counter() - t0:.3f}s;", on_loop(lambda: internals("tenant_b")))
        if not NEW:
            on_loop(lambda: [server.TASK_EVENTS["tenant_b"].get_nowait() for _ in range(server.TASK_EVENTS["tenant_b"].qsize())])
        if NEW:
            # one connected listener that is never scheduled to read: its own queue stays bounded
            sub = on_loop(lambda: server.EVENT_BUS.open("tenant_b", "probe-slow"))
            t0 = time.perf_counter()
            for i in range(1000):
                publish("tenant_b", {"type": "flood", "i": i})
            print(f"with one non-reading subscriber: published 1000 in {time.perf_counter() - t0:.3f}s; "
                  f"its queue qsize={sub.queue.qsize()} maxsize={sub.queue.maxsize} dropped={sub.dropped}")
            print("its next event:", asyncio.run_coroutine_threadsafe(sub.next(0.1), LOOP["l"]).result(5))
            on_loop(lambda: server.EVENT_BUS.close(sub))

        print("\n## 4. concurrent connections from one client: 7 attempts (tenant_a)")
        fresh_bus()
        held = [await connect(c, H["tenant_a"]) for _ in range(7)]
        print("statuses:", [x.status for x in held], "| Retry-After:", [x.retry_after for x in held if x.status != 200],
              "| bodies:", sorted({getattr(x, "body", "") for x in held if x.status != 200}))
        await stop(*held)
        await asyncio.sleep(0.5)
        print("after disconnect:", on_loop(lambda: internals("tenant_a")))

        print("\n## 5. open-rate: 12 sequential open+close from one client (tenant_a)")
        fresh_bus()
        statuses = []
        for _ in range(12):
            lst = await connect(c, H["tenant_a"])
            statuses.append((lst.status, lst.retry_after))
            await stop(lst)
            await asyncio.sleep(0.15)
        print("(status, Retry-After):", statuses)

        print("\n## 6. release: capacity after disconnect")
        fresh_bus()
        first = [await connect(c, H["tenant_a"]) for _ in range(5)]
        await stop(*first)
        await asyncio.sleep(0.5)
        print("after 5 opened+closed:", on_loop(lambda: internals("tenant_a")))
        again = [await connect(c, H["tenant_a"]) for _ in range(5)]
        print("5 new connections:", [x.status for x in again])
        await stop(*again)
    userver.should_exit = True


asyncio.run(main())
