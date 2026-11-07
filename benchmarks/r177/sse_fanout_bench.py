"""R-177 (local gap, D-136 batch): SSE fan-out under concurrent streams, within the
DEFAULT admission limits — event-delivery latency with a sufficient sample, tenant
isolation on the stream, and the admission caps answering 429 at the edge.

Why: the R-177 api-sse rows (log 368) carry n=5..13 stream samples — too few for
any p95/p99 verdict (D-134 needs n>=299 for p99). The default open-rate cap
(AGOS_SSE_OPEN_RATE=10 per 60s per client) makes that sample impossible to get
by opening streams; delivery latency over LONG-LIVED streams is the measurable
quantity, and it is what a dashboard actually experiences.

Set-up (all on loopback, no limit raised):
* SUT = real uvicorn server in its own OS process (scripts/r177_per_service.py
  SutProcess, --storage volatile|live-local (loopback PostgREST only), two session tenants with random keys);
* clients use distinct loopback SOURCE addresses (127.0.0.2..5) because the
  admission "client" is the peer IP: 2 IPs x 5 streams per tenant = 10 streams =
  AGOS_SSE_MAX_PER_TENANT, 20 streams total (< 100 global);
* tenant A and tenant B each submit M tasks at a fixed rate; every stream records
  the receive time of each event for its tenant's task_ids.

Measured: delivery latency (POST sent -> event received on the stream) for
task_created/task_started/task_completed, per-stream completeness (every task of
the tenant seen on every stream of that tenant), cross-tenant leaks (an event
for the other tenant's task on a stream — must be 0), stream_gap events, and the
caps: a 6th stream from one IP and an 11th stream for one tenant must be 429 with
Retry-After.

NOT measured: hundreds/thousands of concurrent streams (needs raised limits and
an isolated env — R-180/R-181, Q-023), browser EventSource behaviour, proxies.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import pathlib
import socket
import sys
import time

import aiohttp

REPO = pathlib.Path(__file__).resolve().parents[2]


def _r177():
    spec = importlib.util.spec_from_file_location("r177", REPO / "scripts" / "r177_per_service.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["r177"] = mod
    spec.loader.exec_module(mod)
    return mod


def _pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, math.ceil(p / 100 * len(xs)) - 1))  # nearest-rank (no banker's rounding)
    return round(xs[k], 3)


def _loopback_ok(ip: str) -> bool:
    s = socket.socket()
    try:
        s.bind((ip, 0))
        return True
    except OSError:
        return False
    finally:
        s.close()


class Stream:
    def __init__(self, name, tenant):
        self.name, self.tenant = name, tenant
        self.events = []  # (recv_monotonic, type, task_id)
        self.gaps = 0
        self.status = None
        self.ready = asyncio.Event()

    async def run(self, url, key, ip, stop: asyncio.Event):
        conn = aiohttp.TCPConnector(local_addr=(ip, 0), limit=1)
        async with aiohttp.ClientSession(connector=conn, timeout=aiohttp.ClientTimeout(total=None, sock_read=None)) as s:
            async with s.get(url + "/api/events/stream", headers={"X-API-Key": key}) as r:
                self.status = r.status
                self.ready.set()
                if r.status != 200:
                    return
                buf = b""
                reader = asyncio.ensure_future(r.content.readany())
                while not stop.is_set():
                    done, _ = await asyncio.wait({reader}, timeout=0.2)
                    if not done:
                        continue
                    chunk = reader.result()
                    if not chunk:
                        break
                    now = time.monotonic()
                    buf += chunk
                    while b"\n\n" in buf:
                        frame, buf = buf.split(b"\n\n", 1)
                        for line in frame.decode("utf-8", "replace").splitlines():
                            if line.startswith("event: stream_gap"):
                                self.gaps += 1
                            if line.startswith("data:"):
                                try:
                                    d = json.loads(line[5:].strip())
                                except ValueError:
                                    continue
                                if isinstance(d, dict) and d.get("task_id"):
                                    self.events.append((now, d.get("type"), d["task_id"]))
                    reader = asyncio.ensure_future(r.content.readany())
                reader.cancel()


async def _probe_cap(url, key, ip):
    conn = aiohttp.TCPConnector(local_addr=(ip, 0), limit=1)
    async with aiohttp.ClientSession(connector=conn, timeout=aiohttp.ClientTimeout(total=5)) as s:
        async with s.get(url + "/api/events/stream", headers={"X-API-Key": key}) as r:
            return {"status": r.status, "retry_after": r.headers.get("Retry-After")}


async def _submit(url, key, m, rate, sent):
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        start = time.monotonic()
        for i in range(m):
            due = start + i / rate
            if due > time.monotonic():
                await asyncio.sleep(due - time.monotonic())
            t0 = time.monotonic()
            async with s.post(url + "/api/tasks", headers={"X-API-Key": key},
                              json={"specialization": "validator",
                                    "prompt": f'validate this JSON: {{"ok": true, "n": {i}}}'}) as r:
                body = await r.json(content_type=None)
            tid = (body or {}).get("task_id")
            if tid:
                sent[tid] = t0


async def bench(url, tenants, m, rate, per_ip, ips):
    stop = asyncio.Event()
    streams, tasks = [], []
    plan = [("A", tenants.a_key, ips[0]), ("A", tenants.a_key, ips[1]),
            ("B", tenants.b_key, ips[2]), ("B", tenants.b_key, ips[3])]
    for tenant, key, ip in plan:
        for j in range(per_ip):
            st = Stream(f"{tenant}@{ip}#{j}", tenant)
            streams.append(st)
            tasks.append(asyncio.create_task(st.run(url, key, ip, stop)))
            await st.ready.wait()
    caps = {
        "6th_stream_same_ip": await _probe_cap(url, tenants.a_key, ips[0]),
        "11th_stream_same_tenant_new_ip": await _probe_cap(url, tenants.a_key, ips[4]),
    }
    await asyncio.sleep(0.5)
    sent = {"A": {}, "B": {}}
    await asyncio.gather(_submit(url, tenants.a_key, m, rate, sent["A"]),
                         _submit(url, tenants.b_key, m, rate, sent["B"]))
    await asyncio.sleep(3.0)  # let the last completions arrive
    stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    lat = {"task_created": [], "task_started": [], "task_completed": []}
    leaks, incomplete, gaps = 0, [], 0
    for st in streams:
        mine, other = sent[st.tenant], sent["B" if st.tenant == "A" else "A"]
        seen = set()
        gaps += st.gaps
        for t, typ, tid in st.events:
            if tid in other:
                leaks += 1
            if tid in mine:
                seen.add(tid)
                if typ in lat:
                    lat[typ].append((t - mine[tid]) * 1000)
        missing = set(mine) - seen
        if missing:
            incomplete.append({"stream": st.name, "missing": len(missing)})
    return {
        "streams_opened": sum(1 for s in streams if s.status == 200),
        "stream_statuses": sorted({str(s.status) for s in streams}),
        "caps": caps,
        "tasks_submitted": {k: len(v) for k, v in sent.items()},
        "cross_tenant_leaks": leaks,
        "incomplete_streams": incomplete,
        "stream_gap_events": gaps,
        "delivery_latency_ms": {k: {"n": len(v), "p50": _pct(v, 50), "p95": _pct(v, 95),
                                    "p99": _pct(v, 99), "max": round(max(v), 3) if v else None}
                                for k, v in lat.items()},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", type=int, default=40, help="tasks per tenant")
    ap.add_argument("--rate", type=float, default=2.0, help="tasks/s per tenant")
    ap.add_argument("--storage", default="volatile", choices=["volatile", "live-local"])
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    ips = ["127.0.0.2", "127.0.0.3", "127.0.0.4", "127.0.0.5", "127.0.0.6"]
    if not all(_loopback_ok(ip) for ip in ips):
        raise SystemExit("needs the 127.0.0.0/8 loopback range (Linux)")
    r177 = _r177()
    sut = r177.SutProcess(storage=a.storage)
    try:
        sut.start()
        res = asyncio.run(bench(sut.url, sut.tenants, a.tasks, a.rate, 5, ips))
    finally:
        sut.stop()
    rep = {"tool": "benchmarks/r177/sse_fanout_bench.py",
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "limits": "defaults (max_per_client=5, max_per_tenant=10, max_connections=100, open_rate=10/60s)",
           "storage": a.storage, "tasks_per_tenant": a.tasks, "rate_per_tenant": a.rate, **res}
    s = json.dumps(rep, indent=1, ensure_ascii=False)
    if a.json:
        pathlib.Path(a.json).write_text(s)
    print(s)


if __name__ == "__main__":
    main()
