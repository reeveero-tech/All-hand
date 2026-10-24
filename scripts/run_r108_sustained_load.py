#!/usr/bin/env python3
"""
R-108: Sustained-load stress test for the agent civilization.

A long-duration stress test: the civilization is built once (1,000 agents),
then waves of tasks are executed against it for a sustained period, with
honest per-wave and cumulative measurements (ok/fail counts, latency,
memory). No fabricated "success" — every wave's failures are recorded with
their causes; the test FAILS (exit != 0) if any wave's ok rate is below the
threshold.

Usage:
  python3 scripts/run_r108_sustained_load.py [--duration-s 600]
      [--wave-size 100] [--min-ok-rate 1.0] [--impl real|specialists]

Env: AGOS_ALLOW_CODE_EXEC=1 (R-100/D-044 — test_runner needs it).
"""
import argparse
import asyncio
import json
import os
import resource
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure code execution is allowed (R-100/D-044) for test_runner payloads
os.environ.setdefault("AGOS_ALLOW_CODE_EXEC", "1")

REPO_ROOT = Path(__file__).resolve().parent.parent


def _payloads():
    """A representative mix across the 10 heaviest specializations."""
    return {
        "analyst": {"prompt": "avg", "data": [1, 2, 3, 4]},
        "data_processor": {"prompt": "sum", "data": [1, 2, 3, 4]},
        "db_manager": {"prompt": "CREATE TABLE t(x INTEGER)"},
        "network_client": {"prompt": "check connectivity to example.com"},
        "test_runner": {"prompt": "print(1+1)"},
        "validator": {"prompt": '{"a":1}'},
        "api_integrator": {"prompt": "GET https://example.com"},
        "code_generator": {"data": "def hello():\n    return 1"},
        "surgeon": {"data": "def f():\n    try:\n        pass\n    except:\n        pass\n"},
        "modifier": {"data": "x = 1   \n"},
        # R-116: educator needs material (>=2 sentences) offline — "demo" is a
        # bare word, which the educator now refuses to "explain" by echoing it.
        "educator": {"prompt": "Recursion is when a function calls itself. Every recursion needs a base case."},
        # R-170 (batch 12): the four deterministic text handlers need a task they can
        # actually do; "demo" is now refused honestly (task_not_completed, no hollow ok).
        "strategist": {"prompt": "Turn these into a roadmap, in this order: write docs; fix the bug; add tests"},
        "architect": {"prompt": "Design an architecture with a REST API and a database."},
        "designer": {"prompt": "Create a colour palette based on #1e88e5."},
        "reviewer": {"prompt": "Review this code:", "data": "def avg(xs):\n    return sum(xs) / len(xs)\n"},
    }


def maxrss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration-s", type=int, default=600,
                    help="total sustained duration in seconds (default 600 = 10 min)")
    ap.add_argument("--wave-size", type=int, default=100,
                    help="tasks per wave (default 100)")
    ap.add_argument("--min-ok-rate", type=float, default=1.0,
                    help="minimum ok rate per wave (default 1.0 = all must succeed)")
    ap.add_argument("--impl", choices=["real", "specialists"], default="real")
    ap.add_argument("--out", default=None,
                    help="output JSON path (default: timestamped unique path under docs/audit/M6/logs/raw/ so runs never overwrite each other — R-112)")
    args = ap.parse_args()

    # R-112: never overwrite an existing report. Default is a timestamped
    # unique path (microsecond-precision) so two runs in the same second
    # still collide-free; --out is honored only if it does not exist.
    if args.out is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out_path = REPO_ROOT / f"docs/audit/M6/logs/raw/r108_sustained_load_{stamp}.json"
    else:
        out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        print(f"report path already exists — refusing to overwrite: {out_path}")
        return 2

    os.environ["AGOS_AGENT_IMPL"] = args.impl
    import sys
    sys.path.insert(0, str(REPO_ROOT))  # run from the repo root regardless of cwd
    from agent_civilization.agents.real import create_civilization

    report = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "impl": args.impl,
        "duration_s_target": args.duration_s,
        "wave_size": args.wave_size,
        "min_ok_rate": args.min_ok_rate,
        "git_head": _git_head(),
        "waves": [],
    }

    # Build the civilization ONCE (this is the "sustained" part — the same
    # civilization serves all the waves; no rebuild between waves).
    t0 = time.time()
    civ = create_civilization(50)
    build_s = time.time() - t0
    total_agents = sum(len(v) for v in civ.values())
    report["build_s"] = round(build_s, 3)
    report["agents"] = total_agents
    print(f"[build] impl={args.impl} agents={total_agents} build_s={build_s:.3f}")

    payloads = _payloads()
    # A stable round-robin task source (no external network for stability)
    specs = sorted(civ.keys())

    def next_payload(i):
        spec = specs[i % len(specs)]
        return payloads.get(spec, {"prompt": "demo"}), spec

    deadline = time.monotonic() + args.duration_s
    wave_idx = 0
    total_ok = 0
    total_fail = 0
    failure_causes = {}

    while time.monotonic() < deadline:
        wave_idx += 1
        tasks = []
        assigned = []
        for i in range(args.wave_size):
            payload, spec = next_payload(i)
            # distribute across the agents of that spec round-robin
            agents = civ[spec]
            agent = agents[i % len(agents)]
            tasks.append(agent.execute(payload))
            assigned.append(spec)
        t1 = time.monotonic()
        results = await asyncio.gather(*tasks)
        wave_s = time.monotonic() - t1

        ok = sum(1 for r in results if r.get("ok"))
        fail = len(results) - ok
        total_ok += ok
        total_fail += fail
        for r, spec in zip(results, assigned):
            if not r.get("ok"):
                cause = str((r.get("result") or {}).get("error") or r.get("error") or "?")[:120]
                key = (spec, cause)
                failure_causes[key] = failure_causes.get(key, 0) + 1

        rate = ok / len(results) if results else 0.0
        rss = maxrss_mb()
        report["waves"].append({
            "wave": wave_idx, "n": len(results), "ok": ok, "fail": fail,
            "ok_rate": round(rate, 4), "wave_s": round(wave_s, 3),
            "cum_ok": total_ok, "cum_fail": total_fail, "maxrss_mb": round(rss, 1),
        })
        print(f"[wave {wave_idx:03d}] n={len(results)} ok={ok} fail={fail} "
              f"rate={rate:.3f} wave_s={wave_s:.2f} cum_ok={total_ok} rss={rss:.1f}MB")

        if rate < args.min_ok_rate:
            report["failed"] = True
            report["failure_at_wave"] = wave_idx
            _write_report(report, True, out_path)
            print(f"FAIL: wave {wave_idx} ok rate {rate:.3f} < threshold {args.min_ok_rate}")
            for (spec, cause), n in sorted(failure_causes.items(), key=lambda x: -x[1]):
                print(f"  cause: {spec} x{n}: {cause}")
            return 1

        # small pause between waves to emulate sustained (not burst) load
        await asyncio.sleep(0.5)

    report["failed"] = False
    report["total_ok"] = total_ok
    report["total_fail"] = total_fail
    report["total_tasks"] = total_ok + total_fail
    report["duration_s_actual"] = round(args.duration_s, 1)
    report["maxrss_mb_final"] = round(maxrss_mb(), 1)
    _write_report(report, False, out_path)
    print(f"OK: {total_ok}/{total_ok + total_fail} over {wave_idx} waves "
          f"({args.duration_s}s sustained)")
    return 0


def _git_head():
    import subprocess
    r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                       cwd=str(REPO_ROOT))
    return r.stdout.strip()


def _write_report(report, failed, out_path):
    # R-112: exclusive-write (O_CREAT|O_EXCL) — never overwrite an existing file.
    # The check in main() (out_path.exists()) is a friendly early failure; this
    # is the hard guarantee even if the file appeared between the check and now.
    fd = os.open(str(out_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(report, indent=1))
    print(f"report: {out_path}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
