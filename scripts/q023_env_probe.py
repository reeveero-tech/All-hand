"""Q-023 (D-140): measure a candidate environment and classify it HONESTLY
against the isolated-environment spec of OPEN_QUESTIONS.md Q-023.

Why: until D-139 every free alternative was judged on paper (vendor pages).
This probe gives measured numbers for any machine it runs on (the agent
sandbox, a GitHub-hosted runner, a Codespace, a VM the owner provides):

  * resources   - visible CPUs / affinity / cgroup quota, RAM, disk, arch;
  * CPU steadiness - a FIXED single-thread workload repeated N times
    (median + coefficient of variation: noisy neighbours show up here);
  * parallel scaling - the same workload on every CPU at once
    (effective cores = parallel throughput / single throughput);
  * steal time  - /proc/stat ``steal`` share during the parallel phase;
  * loopback HTTP - a few hundred SEQUENTIAL GETs to a local stdlib server
    (nearest-rank p50/p95/p99/max). This is a latency floor, NOT a load
    test: one client, one connection at a time, a few seconds in total.

Nothing here is stress/spike/soak (D-134 / Q-023): bounded CPU work for
a few seconds and a single-client loopback probe. The verdict never says
``qualified`` for the full spec unless EVERY requirement is met, and a
single machine can never meet the THREE-SEPARATE-NODES requirement — the
probe states that explicitly instead of letting a big box pass.

Stdlib only (runs before any dependency install, on x86_64 and arm64).

Usage:
  python3 scripts/q023_env_probe.py --label sandbox --json out.json [--quick]
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import math
import multiprocessing as mp
import os
import pathlib
import platform
import shutil
import statistics
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

# Q-023 isolated-environment minimum (OPEN_QUESTIONS.md, D-134/D-136):
# three SEPARATE nodes on one private network.
SPEC = {
    "sut": {"vcpu": 16, "ram_gib": 32},
    "generator": {"vcpu": 8, "ram_gib": 16},
    "storage": {"vcpu": 8, "ram_gib": 32},
    "separate_nodes": 3,
}
# R-177 reduced level (D-139 addendum): what a free option can at most give —
# methodology proof at a DECLARED reduced level, never a capacity figure.
REDUCED_R177_MIN = {"vcpu": 2, "ram_gib": 8}

WORK_ITERS_FULL = 200_000
WORK_ITERS_QUICK = 20_000


# ------------------------------------------------------------------ helpers

def nearest_rank(values: List[float], q: float) -> Optional[float]:
    """Nearest-rank (ceil) percentile — same rule as benchmarks/p6/metrics.py."""
    if not values:
        return None
    s = sorted(values)
    k = max(1, math.ceil(q * len(s)))
    return s[k - 1]


def _read(path: str) -> Optional[str]:
    try:
        return pathlib.Path(path).read_text().strip()
    except OSError:
        return None


def _meminfo_kib(field: str) -> Optional[int]:
    txt = _read("/proc/meminfo") or ""
    for line in txt.splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1])
    return None


def cgroup_cpu_quota() -> Optional[float]:
    """CPUs allowed by cgroup v2 cpu.max (None = unlimited/unknown)."""
    raw = _read("/sys/fs/cgroup/cpu.max")
    if not raw:
        return None
    quota, _, period = raw.partition(" ")
    if quota == "max" or not period:
        return None
    try:
        return round(int(quota) / int(period), 2)
    except ValueError:
        return None


def cgroup_mem_limit_bytes() -> Optional[int]:
    raw = _read("/sys/fs/cgroup/memory.max")
    if not raw or raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def cpu_model() -> Optional[str]:
    txt = _read("/proc/cpuinfo") or ""
    for line in txt.splitlines():
        low = line.lower()
        if low.startswith("model name") or low.startswith("cpu part") or low.startswith("hardware"):
            return line.split(":", 1)[1].strip()
    return platform.processor() or None


def resources() -> Dict[str, Any]:
    try:
        affinity = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        affinity = None
    total, _, free = shutil.disk_usage(".")
    mem_kib = _meminfo_kib("MemTotal")
    avail_kib = _meminfo_kib("MemAvailable")
    return {
        "arch": platform.machine(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "cpu_model": cpu_model(),
        "cpu_count": os.cpu_count(),
        "cpu_affinity": affinity,
        "cgroup_cpu_quota": cgroup_cpu_quota(),
        "ram_total_gib": round(mem_kib / 1048576, 2) if mem_kib else None,
        "ram_available_gib": round(avail_kib / 1048576, 2) if avail_kib else None,
        "cgroup_mem_limit_gib": (round(cgroup_mem_limit_bytes() / 2**30, 2)
                                 if cgroup_mem_limit_bytes() else None),
        "disk_total_gib": round(total / 2**30, 1),
        "disk_free_gib": round(free / 2**30, 1),
    }


def effective_vcpu(res: Dict[str, Any]) -> Optional[float]:
    cands = [v for v in (res.get("cpu_count"), res.get("cpu_affinity"), res.get("cgroup_cpu_quota"))
             if isinstance(v, (int, float)) and v > 0]
    return min(cands) if cands else None


def effective_ram_gib(res: Dict[str, Any]) -> Optional[float]:
    cands = [v for v in (res.get("ram_total_gib"), res.get("cgroup_mem_limit_gib"))
             if isinstance(v, (int, float)) and v > 0]
    return min(cands) if cands else None


# ------------------------------------------------------------- CPU probes

def _work(iters: int) -> float:
    """Fixed CPU-bound unit (sha256 chain). Returns seconds."""
    t0 = time.perf_counter()
    h = b"q023"
    for _ in range(iters):
        h = hashlib.sha256(h).digest()
    return time.perf_counter() - t0


def _proc_stat() -> Optional[List[int]]:
    line = (_read("/proc/stat") or "").splitlines()[:1]
    if not line or not line[0].startswith("cpu "):
        return None
    return [int(x) for x in line[0].split()[1:]]


def cpu_steadiness(iters: int, reps: int) -> Dict[str, Any]:
    times = [_work(iters) for _ in range(reps)]
    med = statistics.median(times)
    cv = statistics.pstdev(times) / statistics.mean(times) if len(times) > 1 else 0.0
    return {"reps": reps, "iters": iters, "median_s": round(med, 5),
            "min_s": round(min(times), 5), "max_s": round(max(times), 5),
            "cv": round(cv, 4)}


def parallel_scaling(iters: int, workers: int) -> Dict[str, Any]:
    single = _work(iters)
    before = _proc_stat()
    t0 = time.perf_counter()
    ctx = mp.get_context("spawn") if platform.system() != "Linux" else mp.get_context("fork")
    with ctx.Pool(workers) as pool:
        pool.map(_work, [iters] * workers)
    wall = time.perf_counter() - t0
    after = _proc_stat()
    steal = None
    if before and after and len(before) >= 8:
        d = [a - b for a, b in zip(after, before)]
        tot = sum(d[:8])
        steal = round(d[7] / tot, 4) if tot else None
    eff = (workers * single) / wall if wall > 0 else None
    return {"workers": workers, "single_s": round(single, 5), "parallel_wall_s": round(wall, 5),
            "effective_cores": round(eff, 2) if eff else None,
            "scaling_efficiency": round(eff / workers, 3) if eff else None,
            "steal_share": steal}


# ------------------------------------------------------- loopback HTTP floor

class _H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — stdlib API
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence
        pass


def loopback_http(n: int) -> Dict[str, Any]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _H)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    lat: List[float] = []
    errors = 0
    try:
        for _ in range(n):
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
                    r.read()
                lat.append((time.perf_counter() - t0) * 1000.0)
            except OSError:
                errors += 1
    finally:
        srv.shutdown()
        srv.server_close()
    return {"requests": n, "errors": errors, "sequential_single_client": True,
            "p50_ms": _r(nearest_rank(lat, 0.50)), "p95_ms": _r(nearest_rank(lat, 0.95)),
            "p99_ms": _r(nearest_rank(lat, 0.99)), "max_ms": _r(max(lat) if lat else None)}


def _r(v: Optional[float]) -> Optional[float]:
    return round(v, 3) if v is not None else None


# ---------------------------------------------------------------- verdict

def classify(res: Dict[str, Any], *, nodes: int = 1) -> Dict[str, Any]:
    """Compare ONE measured machine (or ``nodes`` identical ones) with the
    Q-023 spec. Rules (never generous):
      * full spec needs 3 SEPARATE nodes, each meeting its role minimum;
        a single machine NEVER qualifies, whatever its size;
      * "reduced_R177_only" = at least REDUCED_R177_MIN — methodology at a
        declared reduced level, not a capacity number, never closes R-178/R-179;
      * anything smaller = not_qualified.
    """
    vcpu = effective_vcpu(res)
    ram = effective_ram_gib(res)
    gaps: List[str] = []
    if nodes < SPEC["separate_nodes"]:
        gaps.append(f"separate_nodes: have {nodes}, need {SPEC['separate_nodes']} "
                    "(SUT / load generator / storage on separate machines)")
    role_ok = {}
    for role in ("sut", "generator", "storage"):
        need = SPEC[role]
        ok = (vcpu is not None and ram is not None
              and vcpu >= need["vcpu"] and ram >= need["ram_gib"] * 0.95)
        role_ok[role] = ok
        if not ok:
            gaps.append(f"{role}: have {vcpu} vCPU / {ram} GiB, need "
                        f"{need['vcpu']} vCPU / {need['ram_gib']} GiB")
    if not gaps:
        verdict = "qualified_full_spec"
    elif (vcpu or 0) >= REDUCED_R177_MIN["vcpu"] and (ram or 0) >= REDUCED_R177_MIN["ram_gib"] * 0.9:
        verdict = "reduced_R177_only"
    else:
        verdict = "not_qualified"
    return {"verdict": verdict, "effective_vcpu": vcpu, "effective_ram_gib": ram,
            "nodes_assumed": nodes, "role_minimum_met": role_ok, "gaps": gaps,
            "policy_note": ("resource fit only: an owner ban (e.g. D-134: no stress/spike/soak "
                            "on the shared sandbox) or a provider ToS still applies on top"),
            "closes": {"R-177_full": verdict == "qualified_full_spec",
                       "R-178": verdict == "qualified_full_spec",
                       "R-179": verdict == "qualified_full_spec"}}


def run(label: str, quick: bool) -> Dict[str, Any]:
    iters = WORK_ITERS_QUICK if quick else WORK_ITERS_FULL
    res = resources()
    workers = max(1, int(effective_vcpu(res) or 1))
    out = {
        "probe": "q023_env_probe", "version": 1, "label": label, "quick": quick,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "github": {k: os.environ.get(k) for k in ("GITHUB_RUN_ID", "GITHUB_JOB", "RUNNER_NAME",
                                                  "RUNNER_ARCH", "RUNNER_OS", "ImageOS",
                                                  "GITHUB_SHA") if os.environ.get(k)},
        "resources": res,
        "cpu_steadiness": cpu_steadiness(iters, 5 if quick else 15),
        "parallel": parallel_scaling(iters, workers),
        "loopback_http": loopback_http(50 if quick else 400),
        "not_a_load_test": ("bounded CPU work (seconds) + single-client sequential loopback "
                            "GETs; no stress/spike/soak (D-134, Q-023)"),
    }
    out["classification"] = classify(res)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--label", required=True)
    ap.add_argument("--json", required=True)
    ap.add_argument("--quick", action="store_true", help="small iteration counts (tests)")
    a = ap.parse_args(argv)
    out = run(a.label, a.quick)
    pathlib.Path(a.json).write_text(json.dumps(out, indent=1, ensure_ascii=False))
    c = out["classification"]
    print(json.dumps({"label": a.label, "verdict": c["verdict"],
                      "effective_vcpu": c["effective_vcpu"], "ram_gib": c["effective_ram_gib"],
                      "cpu_cv": out["cpu_steadiness"]["cv"],
                      "effective_cores": out["parallel"]["effective_cores"],
                      "steal": out["parallel"]["steal_share"],
                      "loopback_p95_ms": out["loopback_http"]["p95_ms"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
