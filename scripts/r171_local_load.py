#!/usr/bin/env python3
"""R-171/S0 prep — LOCAL incremental load against a real server process.

Category: **local test** (one machine, one uvicorn process, loopback HTTP, volatile
mode — no storage, no queue, no external network, no model provider). It is not
an integration test with Supabase, not a live test, and not a simulation: every
request goes through the real FastAPI app over TCP.

Phases (each bounded by --max-s; results are written raw, nothing is retried silently):
  A closed-loop ladder — C clients, each: POST /api/tasks -> poll GET /api/tasks/{id}
    until terminal -> next. Measures accept latency, end-to-end latency, throughput,
    error classes, and "record vanished" (404 after acceptance).
  B open-loop burst — S submitters POST as fast as possible (no waiting for
    completion) to find the real admission limit; refusals (429/503 + Retry-After)
    are counted, not retried. Completion of every ACCEPTED task is verified through
    the tenant SSE stream (task_completed events; stream_gap counted).
  Server RSS / threads are sampled from /proc every 0.25 s; queue depth from the
  ``tasks_pending`` gauge of /metrics.

The task mix is deterministic and offline (analyst / data_processor / validator /
designer / reviewer). No OPENROUTER key, keyless provider disabled.

    python3 scripts/r171_local_load.py --agents-per-spec 50 --ladder 1,10,50,100,200 \
        --tasks-per-client 20 --burst 2000 --burst-submitters 100 --out report.json
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import socket
import statistics
import subprocess
import sys
import time
from pathlib import Path

import aiohttp

REPO = Path(__file__).resolve().parent.parent
MIX = [
    ("analyst", "average of the data", [1, 2, 3, 4, 5]),
    ("data_processor", "sum", [1, 2, 3, 4]),
    ("validator", 'validate this JSON: {"a": 1}', None),
    ("designer", "pick a dark mode color palette from #3366ff", None),
    ("reviewer", "review this code: def f(x): return x*2", None),
]
TERMINAL = {"completed", "failed", "cancelled"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 4)


def lat(xs):
    return {"n": len(xs), "p50": pct(xs, 50), "p95": pct(xs, 95), "p99": pct(xs, 99),
            "max": round(max(xs), 4) if xs else None,
            "mean": round(statistics.fmean(xs), 4) if xs else None}


def proc_stat(pid: int):
    try:
        out = {}
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            k, _, v = line.partition(":")
            if k in ("VmRSS", "VmHWM", "Threads"):
                out[k] = int(v.split()[0])
        return out
    except OSError:
        return {}


class Sampler:
    def __init__(self, pid, base, headers):
        self.pid, self.base, self.headers = pid, base, headers
        self.samples = []
        self._stop = False

    async def run(self, session):
        while not self._stop:
            s = proc_stat(self.pid)
            s["t"] = time.monotonic()
            try:
                async with session.get(f"{self.base}/metrics", timeout=aiohttp.ClientTimeout(total=5)) as r:
                    for line in (await r.text()).splitlines():
                        if line.startswith("agos_tasks_pending") or line.startswith("tasks_pending"):
                            s["tasks_pending"] = float(line.split()[-1])
            except Exception:  # noqa: BLE001 — a missed sample is recorded as missing
                s["tasks_pending"] = None
            self.samples.append(s)
            await asyncio.sleep(0.25)

    def stop(self):
        self._stop = True

    def window(self, t0, t1):
        w = [s for s in self.samples if t0 <= s["t"] <= t1]
        def mx(k):
            v = [s[k] for s in w if s.get(k) is not None]
            return max(v) if v else None
        return {"samples": len(w), "rss_kb_max": mx("VmRSS"), "threads_max": mx("Threads"),
                "tasks_pending_max": mx("tasks_pending")}


def body(i):
    spec, prompt, data = MIX[i % len(MIX)]
    b = {"specialization": spec, "prompt": prompt}
    if data is not None:
        b["data"] = data
    return b


async def closed_loop(session, base, headers, clients, per_client, max_s):
    accept, e2e, codes, vanished, results = [], [], {}, 0, {}
    deadline = time.monotonic() + max_s
    timeouts = 0

    async def client(cid):
        nonlocal vanished, timeouts
        for k in range(per_client):
            if time.monotonic() > deadline:
                timeouts += 1
                return
            t0 = time.monotonic()
            try:
                async with session.post(f"{base}/api/tasks", json=body(cid * per_client + k), headers=headers) as r:
                    codes[r.status] = codes.get(r.status, 0) + 1
                    if r.status != 202:
                        await r.read()
                        continue
                    tid = (await r.json())["task_id"]
            except Exception as e:  # noqa: BLE001
                codes[type(e).__name__] = codes.get(type(e).__name__, 0) + 1
                continue
            accept.append(time.monotonic() - t0)
            while True:
                if time.monotonic() > deadline:
                    results["unfinished_at_deadline"] = results.get("unfinished_at_deadline", 0) + 1
                    return
                async with session.get(f"{base}/api/tasks/{tid}", headers=headers) as g:
                    if g.status == 404:
                        vanished += 1
                        break
                    st = (await g.json()).get("status")
                if st in TERMINAL:
                    e2e.append(time.monotonic() - t0)
                    results[st] = results.get(st, 0) + 1
                    break
                await asyncio.sleep(0.02)

    t0 = time.monotonic()
    await asyncio.gather(*(client(c) for c in range(clients)))
    wall = time.monotonic() - t0
    done = sum(v for k, v in results.items() if k in TERMINAL)
    return {"clients": clients, "tasks_planned": clients * per_client, "wall_s": round(wall, 3),
            "http_codes": {str(k): v for k, v in sorted(codes.items(), key=str)},
            "terminal": results, "record_vanished_404": vanished, "stopped_by_deadline": timeouts,
            "throughput_completed_per_s": round(done / wall, 2) if wall else None,
            "accept_latency_s": lat(accept), "end_to_end_latency_s": lat(e2e)}


async def burst(session, base, headers, total, submitters, max_s):
    """Open loop. SSE is opened first so every accepted task's terminal event is seen."""
    accepted, codes, accept_lat, retry_after = set(), {}, [], []
    seen_terminal, gaps, sse_errors, types = {}, 0, [], {}
    sse_ready = asyncio.Event()

    async def sse():
        nonlocal gaps
        try:
            async with session.get(f"{base}/api/events/stream", headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=None, sock_read=None)) as r:
                if r.status != 200:
                    sse_errors.append(f"HTTP {r.status}")
                    sse_ready.set()
                    return
                sse_ready.set()
                async for raw in r.content:
                    line = raw.decode().strip()
                    if not line.startswith("data:"):
                        continue
                    ev = json.loads(line[5:])
                    if ev.get("type") == "stream_gap":
                        gaps += int(ev.get("missed") or 0)
                    types[ev.get("type")] = types.get(ev.get("type"), 0) + 1
                    if ev.get("type") == "task_completed":
                        tid = ev.get("task_id")
                        seen_terminal[tid] = seen_terminal.get(tid, 0) + 1
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            sse_errors.append(type(e).__name__)
            sse_ready.set()

    sse_task = asyncio.create_task(sse())
    await asyncio.wait_for(sse_ready.wait(), 10)
    await asyncio.sleep(1.0)   # HTTP 200 precedes the server-side subscription (no replay)
    counter = iter(range(total))
    t0 = time.monotonic()

    async def sub():
        for i in counter:
            s = time.monotonic()
            try:
                async with session.post(f"{base}/api/tasks", json=body(i), headers=headers) as r:
                    codes[r.status] = codes.get(r.status, 0) + 1
                    if r.status == 202:
                        accepted.add((await r.json())["task_id"])
                        accept_lat.append(time.monotonic() - s)
                    else:
                        if "Retry-After" in r.headers:
                            retry_after.append(int(r.headers["Retry-After"]))
                        await r.read()
            except Exception as e:  # noqa: BLE001
                codes[type(e).__name__] = codes.get(type(e).__name__, 0) + 1

    await asyncio.gather(*(sub() for _ in range(submitters)))
    submit_wall = time.monotonic() - t0
    deadline = time.monotonic() + max_s
    while time.monotonic() < deadline and not accepted <= set(seen_terminal):
        await asyncio.sleep(0.1)
    drain_wall = time.monotonic() - t0
    sse_task.cancel()
    await asyncio.wait({sse_task}, timeout=5)
    missing = accepted - set(seen_terminal)
    missing_status = {}
    for tid in sorted(missing)[:200]:
        async with session.get(f"{base}/api/tasks/{tid}", headers=headers) as g:
            st = (await g.json()).get("status") if g.status == 200 else f"HTTP {g.status}"
        missing_status[st] = missing_status.get(st, 0) + 1
    dup = sum(1 for t in accepted if seen_terminal.get(t, 0) > 1)
    return {"submitted": total, "submitters": submitters, "submit_wall_s": round(submit_wall, 3),
            "http_codes": {str(k): v for k, v in sorted(codes.items(), key=str)},
            "accepted": len(accepted), "accept_latency_s": lat(accept_lat),
            "retry_after_values": sorted(set(retry_after)),
            "terminal_events_for_accepted": len(accepted & set(seen_terminal)),
            "accepted_without_terminal_event": len(missing), "duplicate_terminal_events": dup,
            "sse_stream_gap_missed": gaps, "sse_errors": sse_errors, "sse_event_types": types,
            "status_of_accepted_without_event": missing_status,
            "drain_wall_s": round(drain_wall, 3),
            "accepted_per_s": round(len(accepted) / submit_wall, 2) if submit_wall else None}


async def main_async(a):
    key = "load-" + secrets.token_urlsafe(24)
    port = free_port()
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("OPENROUTER", "SUPABASE", "AGOS_", "LLM_"))}
    env.update({
        "AGOS_TENANT_KEYS": json.dumps([{"tenant": "load", "key_sha256": hashlib.sha256(key.encode()).hexdigest()}]),
        "AGOS_RATE_LIMIT_REQUESTS": "100000000",     # all clients share 127.0.0.1 here
        "AGOS_TASK_RETRY_AFTER_S": "1",
        "AGOS_SSE_QUEUE_SIZE": str(a.sse_queue),
        "AGENTS_PER_SPEC": str(a.agents_per_spec),
    })
    # The server logs every request at INFO, synchronously, on the event loop. A
    # pipe nobody reads fills (~64 KB) and then BLOCKS the whole server — so
    # stderr goes to a file (found while building this harness; see log 294).
    errlog = open(a.server_log, "wb")
    t_start = time.monotonic()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1",
                             "--port", str(port), "--log-level", "warning"],
                            cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=errlog)
    base = f"http://127.0.0.1:{port}"
    headers = {"X-API-Key": key}
    report = {"category": "local test (one process, loopback HTTP, volatile mode, offline task mix)",
              "agents_per_spec": a.agents_per_spec, "python": sys.version.split()[0],
              "cpu_count": os.cpu_count(), "config": {k: env[k] for k in env if k.startswith("AGOS_") and k != "AGOS_TENANT_KEYS"}}
    try:
        conn = aiohttp.TCPConnector(limit=0)
        async with aiohttp.ClientSession(connector=conn, timeout=aiohttp.ClientTimeout(total=60)) as session:
            for _ in range(600):
                try:
                    async with session.get(f"{base}/health/ready") as r:
                        if r.status == 200:
                            break
                except aiohttp.ClientError:
                    pass
                if proc.poll() is not None:
                    raise SystemExit("server exited; see " + a.server_log)
                await asyncio.sleep(0.1)
            report["startup_s"] = round(time.monotonic() - t_start, 3)
            report["idle"] = proc_stat(proc.pid)
            async with session.get(f"{base}/api/system/status") as r:
                st = await r.json()
            report["agents_reported"] = st.get("total_agents") or st.get("agents")
            sampler = Sampler(proc.pid, base, headers)
            st_task = asyncio.create_task(sampler.run(session))
            report["ladder"] = []
            for c in a.ladder:
                t0 = time.monotonic()
                res = await closed_loop(session, base, headers, c, a.tasks_per_client, a.max_s)
                res["server"] = sampler.window(t0, time.monotonic())
                report["ladder"].append(res)
                print(json.dumps({"ladder": c, "thr": res["throughput_completed_per_s"],
                                  "e2e_p95": res["end_to_end_latency_s"]["p95"], "codes": res["http_codes"],
                                  "vanished": res["record_vanished_404"], "rss": res["server"]["rss_kb_max"]}),
                      flush=True)
            if a.burst:
                t0 = time.monotonic()
                res = await burst(session, base, headers, a.burst, a.burst_submitters, a.max_s)
                res["server"] = sampler.window(t0, time.monotonic())
                report["burst"] = res
                print(json.dumps({"burst": {k: res[k] for k in ("accepted", "http_codes", "accepted_without_terminal_event",
                                                               "duplicate_terminal_events", "sse_stream_gap_missed")},
                                  "pending_max": res["server"]["tasks_pending_max"]}), flush=True)
            sampler.stop()
            await st_task
            report["final"] = proc_stat(proc.pid)
            report["server_alive_after"] = proc.poll() is None
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        errlog.close()
    report["server_log_bytes"] = Path(a.server_log).stat().st_size
    Path(a.out).write_text(json.dumps(report, indent=1) + "\n")
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agents-per-spec", type=int, default=50)
    ap.add_argument("--ladder", type=lambda s: [int(x) for x in s.split(",") if x], default=[1, 10, 50])
    ap.add_argument("--tasks-per-client", type=int, default=20)
    ap.add_argument("--burst", type=int, default=0)
    ap.add_argument("--burst-submitters", type=int, default=50)
    ap.add_argument("--sse-queue", type=int, default=10000)
    ap.add_argument("--max-s", type=float, default=120.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--server-log", default="/tmp/r171_server.log")
    a = ap.parse_args()
    asyncio.run(main_async(a))


if __name__ == "__main__":
    main()
