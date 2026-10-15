#!/usr/bin/env python3
"""R-121 evidence: which handler grows C-level (untraced) memory?

Runs ONE specialization's real handler N times in a fresh process and prints
VmRSS, glibc mallinfo2 in-use bytes and Python-traced bytes before/after.
Untraced growth = in-use growth minus traced growth (memory held by C code:
OpenSSL, SQLite, ...). Usage: r121_handler_isolation.py <spec> <n> [prompt]
"""
import asyncio
import ctypes
import gc
import os
import sys
import tracemalloc
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
os.environ.setdefault("AGOS_ALLOW_CODE_EXEC", "1")


class MallInfo2(ctypes.Structure):
    _fields_ = [(n, ctypes.c_size_t) for n in (
        "arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks", "fsmblks", "uordblks", "fordblks", "keepcost")]


libc = ctypes.CDLL("libc.so.6")
libc.mallinfo2.restype = MallInfo2

PROMPTS = {
    "researcher": "research the best practices for API pagination",
    "network_client": "check if example.com is reachable",
    "api_integrator": "integrate with the public endpoint https://example.com",
    "db_manager": "design a table schema for orders with line items",
    "test_runner": "write and run tests for: def add(a, b): return a + b",
    "communicator": "translate 'hello world' to French",
    "analyst": "sum these numbers: [1, 2, 3, 4, 5]",
    "monitor": "monitor the health of the system",
    "builder": "scaffold a starter project for a todo web service",
    "educator": "explain the difference between REST and GraphQL",
}


def rss():
    for line in open("/proc/self/status"):
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) / 1024
    return 0.0


def inuse():
    m = libc.mallinfo2()
    return (m.uordblks + m.hblkhd) / 1048576


def free_held():
    return libc.mallinfo2().fordblks / 1048576


def threads_and_maps():
    th = next((int(ln.split()[1]) for ln in open("/proc/self/status") if ln.startswith("Threads:")), 0)
    maps = sum(1 for _ in open("/proc/self/maps"))
    return th, maps


async def main():
    spec, n = sys.argv[1], int(sys.argv[2])
    prompt = sys.argv[3] if len(sys.argv) > 3 else PROMPTS[spec]
    from agent_civilization.core.agents import real_dispatcher as rd
    await rd.dispatch(spec, {"prompt": prompt})  # warm-up (imports, caches)
    gc.collect()
    tracemalloc.start(1)
    r0, u0, f0, (t0, m0) = rss(), inuse(), free_held(), threads_and_maps()
    ok = 0
    for _ in range(n):
        out = await rd.dispatch(spec, {"prompt": prompt})
        ok += bool(out and out.get("ok"))
    gc.collect()
    traced = tracemalloc.get_traced_memory()[0] / 1048576
    r1, u1, f1, (t1, m1) = rss(), inuse(), free_held(), threads_and_maps()
    print(f"{spec:15s} n={n:4d} ok={ok:4d} | rss {r0:6.1f} -> {r1:6.1f} ({r1 - r0:+6.1f}) MB "
          f"| malloc in-use {u0:6.1f} -> {u1:6.1f} ({u1 - u0:+6.1f}) | python-traced {traced:5.2f} "
          f"| untraced growth/call {(u1 - u0 - traced) * 1024 / n:7.1f} KB "
          f"| malloc free-but-held {f0:5.1f} -> {f1:5.1f} | threads {t0} -> {t1} | maps {m0} -> {m1}", flush=True)


asyncio.run(main())
