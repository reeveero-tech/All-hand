#!/usr/bin/env python3
"""R-132: join a load report (run_r131_http_load.py --out) with its pool
samples (--probe-out) — per concurrency level: peak queue per pool, and the
reachability-check latency grouped by the queue depth of the pool it runs on
at the moment the request started.

Usage: python scripts/run_r132_pool_report.py LOAD.json POOLS.jsonl
"""
import json
import sys


def q(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 2) if xs else None


load = json.load(open(sys.argv[1], encoding="utf-8"))
samples = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
# load "t_s" is measured from the first level's start; the probe's "t" from
# server start. Align on the first request: the load starts once the server
# answers, so offset = first sample time at/after startup -> use the earliest
# request start (0.0) mapped to the first probe sample with traffic.
first_busy = next((s["t"] for s in samples if any((p or {}).get("threads", 0) > 0 or (p or {}).get("queued", 0)
                                                  for p in s["pools"].values())), samples[0]["t"])
offset = first_busy - 1.0


def at(t):
    best = None
    for s in samples:
        if s["t"] - offset <= t:
            best = s
        else:
            break
    return best or samples[0]


net_pool = "net" if any("net" in s["pools"] for s in samples) else "default"
print(f"samples={len(samples)} pools seen={sorted({k for s in samples for k in s['pools']})} "
      f"reachability runs on pool '{net_pool}'")
for lv in load["levels"]:
    rows = lv["rows"]
    t0 = min(r["t_s"] for r in rows)
    t1 = max(r["t_s"] + r["latency_s"] for r in rows)
    win = [s for s in samples if t0 <= s["t"] - offset <= t1]
    peaks = {}
    for s in win:
        for name, p in s["pools"].items():
            if p:
                peaks.setdefault(name, [0, 0, p["workers"]])
                peaks[name][0] = max(peaks[name][0], p["queued"])
                peaks[name][1] = max(peaks[name][1], p["threads"])
    lag = [s["lag_ms"] for s in win]
    print(f"\n== concurrency {lv['concurrency']} (window {t0:.0f}-{t1:.0f}s, {len(win)} samples, "
          f"loop lag p95={q(lag, .95)}ms max={max(lag) if lag else None}ms)")
    for name, (mq, mt, w) in sorted(peaks.items()):
        print(f"  pool {name:7s} workers={w:2d} peak threads={mt:2d} peak queued={mq}")
    net = [r for r in rows if r["message"].startswith("check if")]
    groups = {"queue=0": [], "queue 1-9": [], "queue>=10": []}
    for r in net:
        p = at(r["t_s"])["pools"].get(net_pool) or {}
        d = p.get("queued", 0)
        groups["queue=0" if d == 0 else "queue 1-9" if d < 10 else "queue>=10"].append(r["latency_s"])
    lat = [r["latency_s"] for r in net]
    print(f"  reachability n={len(net)} ok={sum(r['ok'] for r in net)} p50={q(lat, .5)}s "
          f"p95={q(lat, .95)}s max={max(lat) if lat else None}s")
    for g, xs in groups.items():
        if xs:
            print(f"    started with {net_pool} {g:9s}: n={len(xs):4d} p50={q(xs, .5)}s max={max(xs):.2f}s")
