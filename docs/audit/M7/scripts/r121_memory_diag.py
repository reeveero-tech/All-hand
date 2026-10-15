#!/usr/bin/env python3
"""R-121 evidence: WHY does VmRSS grow under the live workload (R-120)?

Runs the R-118/R-120 live workload (same 19 templates and truth checks, the
production path route() -> execute_plan, 1,000 agents, live keyless LLM and
network, concurrent loops) and every --snap-s seconds prints:

  * VmRSS (current resident set) and OS thread count (/proc/self/status);
  * Python-traced memory (tracemalloc, 1 frame) — what Python objects hold;
  * glibc mallinfo2: arena (main-arena heap), hblkhd (mmapped), uordblks
    (in use), fordblks (free but still held by the allocator);
  * experience-store entries.

If VmRSS grows while traced memory stays flat and fordblks grows, the growth is
allocator retention (free memory not returned to the OS), not a Python leak.
Run it with and without MALLOC_ARENA_MAX to compare.

Usage: python docs/audit/M7/scripts/r121_memory_diag.py --duration-min 30 --label base
"""
import argparse
import asyncio
import ctypes
import importlib.util
import json
import os
import random
import sys
import threading
import time
import tracemalloc
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
os.environ.setdefault("AGOS_ALLOW_CODE_EXEC", "1")

spec = importlib.util.spec_from_file_location("live", REPO / "scripts" / "run_r119_live_hour.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class MallInfo2(ctypes.Structure):
    _fields_ = [(n, ctypes.c_size_t) for n in (
        "arena", "ordblks", "smblks", "hblks", "hblkhd", "usmblks", "fsmblks", "uordblks", "fordblks", "keepcost")]


_libc = ctypes.CDLL("libc.so.6")
_libc.mallinfo2.restype = MallInfo2


def proc_status():
    out = {}
    with open("/proc/self/status", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(("VmRSS:", "Threads:")):
                k, v = line.split(":", 1)
                out[k] = int(v.split()[0])
    return out


def sample(t0, n_req, experience):
    st = proc_status()
    mi = _libc.mallinfo2()
    mb = 1024 * 1024
    return {
        "t_s": round(time.monotonic() - t0, 1), "requests": n_req,
        "vmrss_mb": round(st.get("VmRSS", 0) / 1024, 1), "os_threads": st.get("Threads"),
        "py_threads": threading.active_count(),
        "traced_mb": round(tracemalloc.get_traced_memory()[0] / mb, 1),
        "malloc_arena_mb": round(mi.arena / mb, 1), "malloc_mmap_mb": round(mi.hblkhd / mb, 1),
        "malloc_inuse_mb": round(mi.uordblks / mb, 1), "malloc_free_held_mb": round(mi.fordblks / mb, 1),
        "experience_entries": len(experience.memory()._memories),
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration-min", type=float, default=30)
    ap.add_argument("--interval-s", type=float, default=1.5)
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--snap-s", type=float, default=60)
    ap.add_argument("--label", default="run")
    ap.add_argument("--seed", type=int, default=120)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    random.seed(args.seed)

    from agent_civilization.agents.real import create_civilization
    from agent_civilization.core import experience, llm as llm_mod
    from agent_civilization.pipeline.orchestrator import execute_plan
    from agent_civilization.routing.auto_router import route

    tracemalloc.start(1)
    civ = create_civilization(50)
    print(f"[{args.label}] MALLOC_ARENA_MAX={os.environ.get('MALLOC_ARENA_MAX')} "
          f"AGOS_MEMORY_MAX={os.environ.get('AGOS_MEMORY_MAX')} cpus={os.cpu_count()} "
          f"llm={llm_mod.get_llm().provider}", flush=True)
    t0 = time.monotonic()
    deadline = t0 + args.duration_min * 60
    state = {"n": 0, "verified": 0, "fail": 0, "exc": 0}
    samples = []

    async def loop():
        while time.monotonic() < deadline:
            req = random.choice(live.REQUEST_TEMPLATES)
            state["n"] += 1
            try:
                res = await execute_plan(route(req), civ)
                stages = res.get("stages", [])
                ok = all(s.get("ok") for s in stages) and live.verify(req, stages)[0]
                state["verified" if ok else "fail"] += 1
            except Exception:  # noqa: BLE001
                state["exc"] += 1
            await asyncio.sleep(args.interval_s)

    async def snaps():
        while time.monotonic() < deadline:
            await asyncio.sleep(min(args.snap_s, max(0.5, deadline - time.monotonic())))
            s = sample(t0, state["n"], experience)
            s.update(verified=state["verified"], fail=state["fail"], exc=state["exc"],
                     llm=dict(llm_mod.keyless_stats))
            samples.append(s)
            print(f"[{args.label}] " + json.dumps(s, ensure_ascii=False), flush=True)

    await asyncio.gather(snaps(), *(loop() for _ in range(args.concurrency)))
    top = tracemalloc.take_snapshot().statistics("lineno")[:8]
    print(f"[{args.label}] top traced allocation sites at end:", flush=True)
    for st in top:
        print(f"[{args.label}]   {st}", flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps({"label": args.label, "env": {
            "MALLOC_ARENA_MAX": os.environ.get("MALLOC_ARENA_MAX"),
            "AGOS_MEMORY_MAX": os.environ.get("AGOS_MEMORY_MAX")}, "samples": samples}, indent=1),
            encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
