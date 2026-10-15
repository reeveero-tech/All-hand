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

R-123: every unverified request / exception is now recorded with its time,
request, the first failing stage (name + output head) or the truth check that
rejected it, and printed as `[label] FAIL {...}` + kept in the JSON
(`failures`, and `fail_by_reason` per snapshot) — failures are attributed, not
only counted. --templates all|r119 picks the 24 (R-122) or the original 19.

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
    ap.add_argument("--templates", choices=("all", "r119"), default="r119",
                    help="r119 = the original 19 (R-121 comparability); all = the 24 of R-122")
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
    failures = []
    templates = list(live.REQUEST_TEMPLATES) if args.templates == "all" else list(live.REQUEST_TEMPLATES)[:19]

    def record(kind, req, stages=None, err=None, truth=None):
        bad = next((s for s in (stages or []) if not s.get("ok")), None)
        f = {"t_s": round(time.monotonic() - t0, 1), "kind": kind, "request": req,
             "stage": bad.get("name") if bad else None,
             "specialization": bad.get("specialization") if bad else None,
             "reason": (err or (bad or {}).get("output") or (f"truth check: {truth}" if truth else ""))[:240]}
        failures.append(f)
        print(f"[{args.label}] FAIL " + json.dumps(f, ensure_ascii=False), flush=True)

    async def loop():
        while time.monotonic() < deadline:
            req = random.choice(templates)
            state["n"] += 1
            try:
                res = await execute_plan(route(req), civ)
                stages = res.get("stages", [])
                stages_ok = bool(stages) and all(s.get("ok") for s in stages)
                truth_ok, desc = live.verify(req, stages) if stages_ok else (False, None)
                ok = stages_ok and truth_ok
                state["verified" if ok else "fail"] += 1
                if not ok:
                    record("stage" if not stages_ok else "truth", req, stages,
                           err=None if stages else "no stages", truth=None if not stages_ok else desc)
            except Exception as e:  # noqa: BLE001
                state["exc"] += 1
                record("exception", req, err=f"{type(e).__name__}: {e}")
            await asyncio.sleep(args.interval_s)

    async def snaps():
        while time.monotonic() < deadline:
            await asyncio.sleep(min(args.snap_s, max(0.5, deadline - time.monotonic())))
            s = sample(t0, state["n"], experience)
            s.update(verified=state["verified"], fail=state["fail"], exc=state["exc"],
                     llm=dict(llm_mod.keyless_stats), fail_by_reason=_by_reason(failures))
            samples.append(s)
            print(f"[{args.label}] " + json.dumps(s, ensure_ascii=False), flush=True)

    def _by_reason(fs):
        out = {}
        for f in fs:
            k = f"{f['kind']}:{f['stage'] or '-'}:{(f['reason'] or '')[:60]}"
            out[k] = out.get(k, 0) + 1
        return out

    await asyncio.gather(snaps(), *(loop() for _ in range(args.concurrency)))
    top = tracemalloc.take_snapshot().statistics("lineno")[:8]
    print(f"[{args.label}] top traced allocation sites at end:", flush=True)
    for st in top:
        print(f"[{args.label}]   {st}", flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps({"label": args.label, "env": {
            "MALLOC_ARENA_MAX": os.environ.get("MALLOC_ARENA_MAX"),
            "AGOS_MEMORY_MAX": os.environ.get("AGOS_MEMORY_MAX")}, "templates": args.templates,
            "samples": samples, "failures": failures}, indent=1),
            encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
