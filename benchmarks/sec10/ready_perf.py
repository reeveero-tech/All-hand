"""SEC-10 (D-135): measured cost of the storage readiness check on GET /health/ready.

What it measures (the owner's «وثق أثر الفحص على الأداء»):
  * /health/ready latency per storage mode: volatile (no probe), live-local with
    the default cache (1.0s), and live-local with cache disabled (0 = a probe on
    EVERY request — the worst case an operator can configure);
  * how many HEAD probes actually reach the store under a /health/ready flood
    (counted by a loopback counting proxy in front of the local PostgREST);
  * the effect of that flood on another endpoint (/api/system/status p95, alone
    vs during the flood).

Honest limits: shared 2-CPU agent sandbox, SUT + generator + store on ONE node
(R-176 separation is process-level only); local PostgREST, not hosted Supabase;
the generator is an aiohttp open-model client in a separate OS process from the
SUT (not k6) because it needs exact per-request timing at a fixed arrival rate.
It is a cost measurement, not a capacity test — rates are low (Q-023).

Usage:  R177_STORAGE_URL=http://127.0.0.1:54321 R177_STORAGE_KEY=... \
        python3 benchmarks/sec10/ready_perf.py --json out.json
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import os
import pathlib
import statistics
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import aiohttp

REPO = pathlib.Path(__file__).resolve().parents[2]


def _r177():
    spec = importlib.util.spec_from_file_location("r177", REPO / "scripts" / "r177_per_service.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["r177"] = mod
    spec.loader.exec_module(mod)
    return mod


class CountingProxy:
    """Loopback reverse proxy that forwards to the local PostgREST and counts the
    SEC-10 readiness probes (HEAD .../tasks?select=task_id&limit=1)."""

    def __init__(self, upstream: str):
        self.upstream = upstream.rstrip("/")
        self.probes = 0
        self.other = 0
        self.lock = threading.Lock()
        proxy = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):  # quiet
                pass

            def _fwd(self, method):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else None
                is_probe = method == "HEAD" and "select=task_id&limit=1" in self.path
                with proxy.lock:
                    if is_probe:
                        proxy.probes += 1
                    else:
                        proxy.other += 1
                hdrs = {k: v for k, v in self.headers.items()
                        if k.lower() not in ("host", "content-length", "connection")}
                req = urllib.request.Request(proxy.upstream + self.path, data=body,
                                             method=method, headers=hdrs)
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        status, rh, data = r.status, r.headers, r.read()
                except urllib.error.HTTPError as e:
                    status, rh, data = e.code, e.headers, e.read()
                self.send_response(status)
                for k, v in rh.items():
                    if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if method != "HEAD":
                    self.wfile.write(data)

            def do_GET(self): self._fwd("GET")
            def do_HEAD(self): self._fwd("HEAD")
            def do_POST(self): self._fwd("POST")
            def do_PATCH(self): self._fwd("PATCH")
            def do_DELETE(self): self._fwd("DELETE")

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return self.probes, self.other

    def close(self):
        self.srv.shutdown()


def _pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, math.ceil(p / 100 * len(xs)) - 1))  # nearest-rank (no banker's rounding)
    return round(xs[k], 3)


async def _open_model(url: str, rate: float, duration: float, headers=None):
    """Fixed arrival rate (open model): one request every 1/rate s regardless of
    completions, so a slow server cannot slow the generator down (no coordinated
    omission). Returns latency list (ms) + status counts."""
    lat, statuses = [], {}
    conn = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=conn, timeout=aiohttp.ClientTimeout(total=10)) as s:
        async def one():
            t0 = time.perf_counter()
            try:
                async with s.get(url, headers=headers) as r:
                    await r.read()
                    code = r.status
            except Exception as e:  # noqa: BLE001 — counted, never hidden
                code = type(e).__name__
            lat.append((time.perf_counter() - t0) * 1000)
            statuses[str(code)] = statuses.get(str(code), 0) + 1
        tasks, start, i = [], time.perf_counter(), 0
        while True:
            due = start + i / rate
            if due - start >= duration:
                break
            now = time.perf_counter()
            if due > now:
                await asyncio.sleep(due - now)
            tasks.append(asyncio.create_task(one()))
            i += 1
        await asyncio.gather(*tasks)
    return {"requests": len(lat), "statuses": statuses, "p50_ms": _pct(lat, 50),
            "p95_ms": _pct(lat, 95), "p99_ms": _pct(lat, 99), "max_ms": round(max(lat), 3),
            "mean_ms": round(statistics.fmean(lat), 3)}


async def _flood_with_side(base: str, ready_rate: float, side_rate: float, duration: float):
    ready, side = await asyncio.gather(
        _open_model(base + "/health/ready", ready_rate, duration),
        _open_model(base + "/api/system/status", side_rate, duration))
    return ready, side


def run(args) -> dict:
    r177 = _r177()
    out = {"tool": "benchmarks/sec10/ready_perf.py", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "ready_rate_rps": args.rate, "side_rate_rps": args.side_rate, "duration_s": args.duration,
           "cases": []}
    upstream = os.environ.get("R177_STORAGE_URL", "")
    proxy = CountingProxy(upstream) if upstream else None
    cases = [("volatile", None), ("live-local", "1.0"), ("live-local", "0")]
    for storage, cache in cases:
        if storage == "live-local" and not proxy:
            out["cases"].append({"storage": storage, "skipped": "R177_STORAGE_URL not set"})
            continue
        extra = {}
        if storage == "live-local":
            os.environ["R177_STORAGE_URL"] = proxy.url  # SUT talks to the store THROUGH the counter
            extra["AGOS_READY_STORAGE_CACHE_S"] = cache
        sut = r177.SutProcess(storage=storage)
        sut.env.update(extra)
        try:
            sut.start()
            if sut.wait_status("/health/ready", 200, timeout=30) is None:
                raise RuntimeError("SUT never became ready")
            side_alone = asyncio.run(_open_model(sut.url + "/api/system/status", args.side_rate, args.duration))
            live_only = asyncio.run(_open_model(sut.url + "/health/live", args.rate, args.duration))
            p0 = proxy.snapshot() if proxy else (0, 0)
            ready, side = asyncio.run(_flood_with_side(sut.url, args.rate, args.side_rate, args.duration))
            p1 = proxy.snapshot() if proxy else (0, 0)
            out["cases"].append({
                "storage": storage, "ready_cache_s": cache,
                "health_live_same_rate": live_only,
                "health_ready_flood": ready,
                "probes_reaching_store": p1[0] - p0[0],
                "probes_per_s": round((p1[0] - p0[0]) / args.duration, 2),
                "system_status_alone": side_alone,
                "system_status_during_flood": side,
            })
        finally:
            sut.stop()
            if storage == "live-local":
                os.environ["R177_STORAGE_URL"] = upstream
    if proxy:
        proxy.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=100.0)
    ap.add_argument("--side-rate", type=float, default=10.0)
    ap.add_argument("--duration", type=float, default=15.0)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    rep = run(args)
    s = json.dumps(rep, indent=1, ensure_ascii=False)
    if args.json:
        pathlib.Path(args.json).write_text(s)
    print(s)


if __name__ == "__main__":
    main()
