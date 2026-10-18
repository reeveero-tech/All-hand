#!/usr/bin/env python3
"""R-162: measure the agent registry at 10,000 logical agents.

Builds the real civilization (20 specializations x N, default 500 = 10,000
executors), registers every executor as an identity, adds W in-process workers
(default 10 x 1000 capacity), takes one lease per identity, then stops half the
workers and removes the other half. Prints one JSON object: counts, wall/CPU
time per phase, peak traced memory (tracemalloc) and RSS growth, OS thread and
child-process counts before/after. No network, no storage, no secrets.

Usage: python scripts/r162_registry_scale.py [--per-spec 500] [--workers 10] [--capacity 1000]
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import pathlib
import platform
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agent_civilization.agents.real import create_civilization  # noqa: E402
from agent_civilization.core import agent_registry as AR  # noqa: E402


def _threads() -> int:
    return len(os.listdir("/proc/self/task"))


def _rss_kb() -> int:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return -1


def _phase(name, fn, out):
    t0, c0 = time.perf_counter(), time.process_time()
    r = fn()
    out[name] = {"wall_s": round(time.perf_counter() - t0, 4), "cpu_s": round(time.process_time() - c0, 4)}
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-spec", type=int, default=500)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--capacity", type=int, default=1000)
    a = ap.parse_args()

    res = {"python": platform.python_version(), "per_spec": a.per_spec,
           "workers": a.workers, "capacity": a.capacity, "phases": {}}
    threads0, children0, rss0 = _threads(), len(multiprocessing.active_children()), _rss_kb()
    tracemalloc.start()
    civ = _phase("build_civilization", lambda: create_civilization(a.per_spec, impl="real"), res["phases"])
    _, peak_civ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    cur0, _ = tracemalloc.get_traced_memory()
    reg = AR.AgentRegistry()
    n = _phase("register_identities", lambda: AR.register_civilization(reg, civ, version="real"), res["phases"])

    def workers():
        for i in range(a.workers):
            reg.register_worker(f"w{i}", "inproc", a.capacity)
            reg.mark_ready(f"w{i}")
    _phase("register_workers", workers, res["phases"])

    tids = []

    def lease_all():
        for spec in civ:
            for _ in range(len(civ[spec])):
                ag = reg.select_agent(spec)
                tid = f"t{len(tids)}"
                reg.acquire(tid, ag.agent_id)
                tids.append(tid)
    _phase("select_and_lease_all", lease_all, res["phases"])
    saturation = reg.saturation()
    try:
        reg.acquire("extra", next(iter(reg._agents)))
        extra = "accepted"
    except AR.Saturated:
        extra = "Saturated"
    cur1, peak_reg = tracemalloc.get_traced_memory()
    orphaned = []

    def stop_half():
        for i in range(a.workers // 2):
            orphaned.extend(reg.stop(f"w{i}").task_ids)
        for i in range(a.workers // 2, a.workers):
            orphaned.extend(reg.remove_worker(f"w{i}").task_ids)
    _phase("stop_and_remove_workers", stop_half, res["phases"])
    tracemalloc.stop()

    res.update({
        "identities": n, "leases_taken": len(tids), "saturation_at_full": saturation,
        "lease_beyond_capacity": extra,
        "orphans_returned": len(orphaned), "orphans_unique": len(set(orphaned)),
        "orphans_equal_leases": sorted(orphaned) == sorted(tids),
        "leases_active_after": reg.snapshot()["leases_active"],
        "traced_peak_civilization_mb": round(peak_civ / 2**20, 2),
        "traced_registry_retained_mb": round((cur1 - cur0) / 2**20, 2),
        "traced_registry_peak_mb": round(peak_reg / 2**20, 2),
        "rss_growth_mb": round((_rss_kb() - rss0) / 1024, 2),
        "max_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
        "os_threads_before": threads0, "os_threads_after": _threads(),
        "child_processes_before": children0, "child_processes_after": len(multiprocessing.active_children()),
    })
    print(json.dumps(res, indent=2))
    ok = (n == len(tids) == res["orphans_unique"] and res["orphans_equal_leases"]
          and res["leases_active_after"] == 0 and extra == "Saturated"
          and res["os_threads_after"] == threads0 and res["child_processes_after"] == children0)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
