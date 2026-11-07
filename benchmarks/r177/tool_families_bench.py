"""R-177 (local gap, D-136 batch): every registered tool family under concurrent load
through the REAL runtime gate — not just the 4 tools the HTTP mixes reach.

Why: log 368 §tools shows the HTTP load mixes exercise 4 of 100 registered tools
(analyst/designer/surgeon/validator). R-177's scope lists «each tool family».
This bench dispatches every tool that is safe to run on the shared environment
through ``tool_runtime.dispatch`` (the same gate the HTTP path uses: timeout,
network policy, scopes, declared-type check) at a fixed concurrency, and records
per-tool latency, ok/refused/error counts, gate refusals and RSS growth.

What it is NOT (declared, not hidden):
* not HTTP — the HTTP surface for tools is covered by the R-177 mixes; this is the
  tool runtime path in one process (no SUT/generator split is meaningful here);
* not the external-network tools (api_integrator, communicator, educator,
  network_client, researcher): they would load public third-party services from
  a shared sandbox — excluded and listed as `skipped` (R-180/R-182);
* no LLM: no provider key and AGOS_LLM_KEYLESS unset, so tools that need a model
  report their honest refusal (counted as `refused`, never as ok);
* not stress: low concurrency (default 8) and bounded iterations (Q-023).

Usage: python3 benchmarks/r177/tool_families_bench.py [--n 200] [--concurrency 8] [--json out.json]
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import os
import pathlib
import resource
import statistics
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

EXTERNAL_NETWORK = ("api_integrator", "communicator", "educator", "network_client", "researcher")
#: tools that spawn a subprocess per call (sandboxed runner / isolated child) — fewer iterations
HEAVY = {"test_runner": 20, "self_developer": 20, "modifier": 40}

FAMILY_OF_STDLIB = [
    ("text", ("text_",)),
    ("encoding_hash", ("base64_", "url_", "hex_", "sha256_", "md5_", "crc32_", "uuid4_", "roman_", "int_to_roman")),
    ("data_formats", ("json_", "csv_", "tsv_", "kv_", "html_", "xml_")),
    ("validation", ("validate_", "regex_")),
    ("math_stats", ("stats_", "list_sum", "list_minmax", "percentile", "math_", "gcd_", "base_convert")),
    ("collections", ("list_",)),
]


def family(spec: str, stdlib: set) -> str:
    if spec not in stdlib:
        return "specialist"
    for fam, prefixes in FAMILY_OF_STDLIB:
        if spec.startswith(prefixes):
            return fam
    return "stdlib_other"


def _pct(xs, p):
    xs = sorted(xs)
    k = max(0, min(len(xs) - 1, math.ceil(p / 100 * len(xs)) - 1))  # nearest-rank (no banker's rounding)
    return round(xs[k], 3)


def _rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


async def _bench_tool(tool_runtime, spec, work, n, conc):
    sem = asyncio.Semaphore(conc)
    lat, ok, refused, errors, err_samples = [], 0, 0, 0, set()

    async def one():
        nonlocal ok, refused, errors
        async with sem:
            t0 = time.perf_counter()
            try:
                r = await tool_runtime.dispatch(spec, dict(work))
            except Exception as e:  # noqa: BLE001 — an escaped exception is a finding
                errors += 1
                err_samples.add("raised " + type(e).__name__)
                return
            finally:
                lat.append((time.perf_counter() - t0) * 1000)
            if isinstance(r, dict) and r.get("ok") is True:
                ok += 1
            else:
                refused += 1
                err_samples.add(str((r or {}).get("error", r))[:90] if isinstance(r, dict) else type(r).__name__)

    t0 = time.perf_counter()
    await asyncio.gather(*(one() for _ in range(n)))
    wall = time.perf_counter() - t0
    return {"n": n, "ok": ok, "refused": refused, "raised": errors,
            "p50_ms": _pct(lat, 50), "p95_ms": _pct(lat, 95), "p99_ms": _pct(lat, 99),
            "max_ms": round(max(lat), 3), "throughput_per_s": round(n / wall, 1),
            "refusal_samples": sorted(err_samples)[:3]}


async def run(n: int, conc: int) -> dict:
    from agent_civilization.core import tool_runtime, tools_stdlib
    spec = importlib.util.spec_from_file_location("certify_phase1", REPO / "scripts" / "certify_phase1.py")
    runbook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runbook)
    smoke = runbook.SMOKE_WORK
    stdlib = set(tools_stdlib.HANDLERS)
    reg = tool_runtime.runtime_registry()
    records = {r.name: r for r in reg.search(limit=None)}
    rss0 = _rss_mb()
    stats0 = tool_runtime.stats()
    per_tool, skipped = {}, {}
    for name in sorted(records):
        if name in EXTERNAL_NETWORK:
            skipped[name] = "external network tool — not loaded from the shared sandbox (R-180/R-182)"
            continue
        iters = HEAVY.get(name, n)
        res = await _bench_tool(tool_runtime, name, smoke[name], iters, conc)
        res["family"] = family(name, stdlib)
        per_tool[name] = res
    rss1 = _rss_mb()
    # second identical pass on the stdlib only: RSS growth between passes = leak indicator
    for name in sorted(stdlib):
        await _bench_tool(tool_runtime, name, smoke[name], n, conc)
    rss2 = _rss_mb()
    fams = {}
    for name, r in per_tool.items():
        f = fams.setdefault(r["family"], {"tools": 0, "calls": 0, "ok": 0, "refused": 0, "raised": 0,
                                           "p95_ms_max": 0.0, "p95_ms_median": []})
        f["tools"] += 1
        f["calls"] += r["n"]
        f["ok"] += r["ok"]
        f["refused"] += r["refused"]
        f["raised"] += r["raised"]
        f["p95_ms_max"] = max(f["p95_ms_max"], r["p95_ms"])
        f["p95_ms_median"].append(r["p95_ms"])
    for f in fams.values():
        f["p95_ms_median"] = round(statistics.median(f["p95_ms_median"]), 3)
    return {"tool": "benchmarks/r177/tool_families_bench.py",
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "python": sys.version.split()[0], "n_per_tool": n, "concurrency": conc,
            "registered": len(records), "benched": len(per_tool), "skipped": skipped,
            "families": fams, "per_tool": per_tool,
            "gate_stats_before": stats0, "gate_stats_after": tool_runtime.stats(),
            "rss_max_mb": {"start": rss0, "after_pass1": rss1, "after_stdlib_pass2": rss2}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="r177_tools_")
    os.environ.setdefault("AGOS_ENV", "test")
    os.environ["AGOS_TOOL_REGISTRY_DB"] = os.path.join(tmp, "registry.sqlite3")  # isolated — never the operator's
    os.environ.setdefault("AGOS_API_KEY", "test:phase1")  # authenticated mode: sandboxed runner allowed
    os.environ.pop("AGOS_LLM_KEYLESS", None)
    os.environ.pop("OPENROUTER_API_KEY", None)
    rep = asyncio.run(run(a.n, a.concurrency))
    s = json.dumps(rep, indent=1, ensure_ascii=False, default=str)
    if a.json:
        pathlib.Path(a.json).write_text(s)
    print(s)


if __name__ == "__main__":
    main()
