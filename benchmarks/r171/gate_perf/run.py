"""R-171 gate performance benchmark (batch 4, D-114).

Measures, on a REAL shared store, the things the R-171 closure claims:

1. gate CHECK latency — the per-dispatch fixed cost every handler pays
   (registry lookup + static checks; p50/p99 over N checks);
2. concurrent GATED dispatch throughput — M concurrent probe executions of
   a safe tool through ``tool_runtime.dispatch`` (the real path), ops/s;
3. full health-check run duration — every tool, static + execution probes.

Usage::

    python3 benchmarks/r171/gate_perf/run.py [--json out.json]

Honest limits: this is a single-host, single-store benchmark on the machine
that runs it — it measures the gate's own overhead, not provider latency
(model/network tools are never executed here; the probe tool is the
deterministic analyst). Numbers are for the audit log, not a load test.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import tempfile
import time
from typing import Any, Dict, List

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
os.chdir(ROOT)
sys.path.insert(0, ROOT)   # the repo root is not on sys.path when run as a script

CHECK_N = 2_000        # gate check latency samples
CONCURRENCY = [10, 50, 200]
DISPATCH_N = 200       # gated dispatches per concurrency level
PROBE_SPEC = "analyst"  # deterministic, read-only, low risk, no side effects
PROBE_WORK = {"prompt": "gate perf probe", "goal": "gate perf probe"}


async def _measure_checks() -> Dict[str, Any]:
    from agent_civilization.core import tool_runtime
    tool_runtime.runtime_registry()   # seed once, outside the measured loop
    samples: List[float] = []
    for _ in range(CHECK_N):
        t0 = time.perf_counter()
        ok, reason = tool_runtime.check(PROBE_SPEC)
        dt = time.perf_counter() - t0
        assert ok, reason
        samples.append(dt * 1000.0)
    samples.sort()
    return {"n": len(samples),
            "p50_ms": round(statistics.median(samples), 3),
            "p99_ms": round(samples[int(0.99 * len(samples)) - 1], 3),
            "mean_ms": round(statistics.fmean(samples), 3)}


async def _measure_dispatch() -> Dict[str, Any]:
    from agent_civilization.core import tool_runtime
    out: Dict[str, Any] = {}
    for c in CONCURRENCY:
        lat: List[float] = []
        done = 0

        async def one() -> None:
            nonlocal done
            t0 = time.perf_counter()
            r = await tool_runtime.dispatch(PROBE_SPEC, dict(PROBE_WORK))
            lat.append(time.perf_counter() - t0)
            assert r.get("ok") is True, r
            done += 1

        batch = DISPATCH_N // c
        t0 = time.perf_counter()
        for _ in range(batch):
            await asyncio.gather(*(one() for _ in range(c)))
        wall = time.perf_counter() - t0
        lat.sort()
        out[f"concurrency_{c}"] = {
            "dispatches": done, "wall_s": round(wall, 3),
            "ops_per_s": round(done / wall, 1),
            "latency_p50_ms": round(statistics.median(lat) * 1000, 3),
            "latency_p99_ms": round(lat[int(0.99 * len(lat)) - 1] * 1000, 3)}
    return out


async def _measure_health() -> Dict[str, Any]:
    from agent_civilization.core import tool_health
    t0 = time.perf_counter()
    report = await tool_health.run_health_check()
    wall = time.perf_counter() - t0
    return {"wall_s": round(wall, 3), "summary": report["summary"]}


async def main_async() -> Dict[str, Any]:
    from agent_civilization.core import tool_runtime
    checks = await _measure_checks()
    dispatch = await _measure_dispatch()
    health = await _measure_health()
    return {"tool": {"tool_id": tool_runtime.tool_id_for(PROBE_SPEC)},
            "gate_check": checks, "gated_dispatch": dispatch,
            "full_health_check": health,
            "note": "single host, single shared sqlite store; probe tool is "
                    "deterministic (analyst); model/network tools never executed"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="write the report here too")
    args = ap.parse_args()
    # an isolated store so the benchmark never touches an operator's registry
    with tempfile.TemporaryDirectory() as td:
        os.environ["AGOS_TOOL_REGISTRY_DB"] = os.path.join(td, "bench.sqlite3")
        from agent_civilization.core import tool_runtime
        tool_runtime.reset_runtime_registry()
        report = asyncio.run(main_async())
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
