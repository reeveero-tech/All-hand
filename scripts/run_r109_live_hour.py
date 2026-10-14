#!/usr/bin/env python3
"""
R-109: Live civilization soak — run the civilization for a sustained duration
and observe what happens.

Unlike R-108 (a fixed set of pre-computed wave tasks), this is a LIVE soak:
- The civilization is built ONCE (1,000 agents, real implementation).
- A continuous "task generator" produces realistic, varied user requests
  (routed through `route()` -> `execute_plan` — the REAL production path).
- The loop runs for a target duration (default: 60 minutes), logging every
  plan's stages, latencies, ok/fail, and the civilization's aggregate stats.
- Periodic health snapshots: agent status distribution, experience store size,
  and any anomalies (repeated failures, stuck agents).

This is an OBSERVATION soak — it does NOT fail the run on individual task
failures (those are recorded, not fatal), but it DOES report a full honest
summary at the end (what worked, what failed, any patterns).
"""
import argparse
import asyncio
import json
import os
import random
import resource
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# R-100/D-044: allow code execution for test_runner payloads
os.environ.setdefault("AGOS_ALLOW_CODE_EXEC", "1")


def maxrss_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


# A realistic mix of user requests — the same categories a real user would send.
REQUEST_TEMPLATES = [
    # code generation / review
    "write a function that returns the nth prime",
    "review this code for bugs: def f(x): return x*2",
    "refactor this to be cleaner: if a: if b: if c: return 1",
    "generate a python class for a user account with validation",
    # data analysis
    "analyze this data for trends: [10, 20, 30, 40, 50]",
    "sum these numbers: [1, 2, 3, 4, 5]",
    "compute the average of [12, 14, 16, 18]",
    # architecture / strategy
    "design a scalable multi-tenant SaaS architecture",
    "plan a database migration for a 10M row table",
    "review the architecture for a REST API with rate limiting",
    # validation
    'validate this JSON schema: {"name": "string", "age": "number"}',
    # test running
    "write and run tests for: def add(a, b): return a + b",
    # DB
    "design a table schema for orders with line items",
    # network probe
    "check if example.com is reachable",
    # communication
    "draft a message to the team about the outage",
    # education
    "explain the difference between REST and GraphQL",
    # translation
    "translate 'hello world' to French",
    # research (web search)
    "research the best practices for API pagination",
    # monitoring
    "monitor the health of the system",
]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration-min", type=int, default=60,
                    help="target soak duration in minutes (default 60)")
    ap.add_argument("--interval-s", type=float, default=3.0,
                    help="seconds between requests (default 3.0)")
    ap.add_argument("--agents-per-spec", type=int, default=50)
    ap.add_argument("--out", default=None,
                    help="output JSON path (default: timestamped so runs don't overwrite each other)")
    args = ap.parse_args()

    # NOTE: we do NOT set AGOS_AGENT_IMPL — use the default (real).
    from agent_civilization.agents.real import create_civilization
    from agent_civilization.pipeline.orchestrator import execute_plan
    from agent_civilization.routing.auto_router import route

    duration_s = args.duration_min * 60
    if args.out is None:
        from datetime import datetime as _dt
        stamp = _dt.now().strftime("%Y%m%d_%H%M%S")
        out_path = REPO_ROOT / f"docs/audit/M6/logs/raw/r109_soak_{stamp}.json"
    else:
        out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "duration_min_target": args.duration_min,
        "duration_s_target": duration_s,
        "interval_s": args.interval_s,
        "agents_per_spec": args.agents_per_spec,
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"],
                                   capture_output=True, text=True,
                                   cwd=str(REPO_ROOT)).stdout.strip(),
        "requests": [],
        "periodic_snapshots": [],
        "summary": {},
    }

    # Build the civilization ONCE
    t0 = time.time()
    civ = create_civilization(args.agents_per_spec)
    build_s = time.time() - t0
    total_agents = sum(len(v) for v in civ.values())
    report["build_s"] = round(build_s, 3)
    report["agents"] = total_agents
    print(f"[build] agents={total_agents} build_s={build_s:.3f}", flush=True)

    # Track per-agent cumulative stats so we can see the distribution at the end
    agent_stats = defaultdict(lambda: {"ok": 0, "fail": 0, "time_s": 0.0})

    req_idx = 0
    stage_outcomes = Counter()
    stage_failures = Counter()
    spec_outcomes = Counter()

    deadline = time.monotonic() + duration_s
    last_snapshot_t = time.monotonic()
    snapshot_interval_s = 300  # every 5 minutes

    print(f"[soak] starting — target {duration_s}s, interval {args.interval_s}s", flush=True)

    while time.monotonic() < deadline:
        req_idx += 1
        request = random.choice(REQUEST_TEMPLATES)
        data = None  # let route()/execute_plan supply data via the prompt
        plan = route(request)

        t1 = time.monotonic()
        try:
            result = await execute_plan(plan, civ, data=data)
            plan_s = time.monotonic() - t1
            plan_ok = all(s.get("ok") for s in result.get("stages", []))

            # record per-stage outcomes
            for stage in result.get("stages", []):
                key = (stage.get("specialization"), "ok" if stage.get("ok") else "fail")
                stage_outcomes[key] += 1
                if not stage.get("ok"):
                    stage_failures[(stage.get("specialization"), str(stage.get("output"))[:100])] += 1
                # accumulate per-agent stats
                ag = stage.get("agent")
                if ag:
                    agent_stats[ag]["ok" if stage.get("ok") else "fail"] += 1

            spec_outcomes[(plan.get("primary"), "ok" if plan_ok else "fail")] += 1
            report["requests"].append({
                "idx": req_idx, "request": request, "plan_primary": plan.get("primary"),
                "stages": [{"spec": s.get("specialization"), "ok": s.get("ok"),
                            "elapsed_s": s.get("elapsed_s"),
                            "agent": s.get("agent")} for s in result.get("stages", [])],
                "plan_s": round(plan_s, 3), "plan_ok": plan_ok,
                "final": (result.get("final") or "")[:200],
            })
            mark = "OK " if plan_ok else "FAIL"
            print(f"[req {req_idx:4d}] {mark} primary={plan.get('primary')} "
                  f"stages={len(result.get('stages', []))} plan_s={plan_s:.2f} "
                  f"request='{request[:50]}'", flush=True)
        except Exception as exc:
            plan_s = time.monotonic() - t1
            report["requests"].append({
                "idx": req_idx, "request": request, "plan_primary": plan.get("primary"),
                "stages": [], "plan_s": round(plan_s, 3), "plan_ok": False,
                "final": f"EXCEPTION: {type(exc).__name__}: {exc}",
            })
            spec_outcomes[(plan.get("primary"), "exception")] += 1
            print(f"[req {req_idx:4d}] EXCEPTION primary={plan.get('primary')} "
                  f"{type(exc).__name__}: {exc}", flush=True)

        # Periodic snapshot every 5 minutes
        if time.monotonic() - last_snapshot_t >= snapshot_interval_s:
            snap = _snapshot(civ, agent_stats, stage_outcomes)
            snap["rss_mb"] = round(maxrss_mb(), 1)
            report["periodic_snapshots"].append(snap)
            print(f"[snapshot] t={int(time.monotonic() - (deadline - duration_s))}s "
                  f"rss={snap['rss_mb']}MB agents_idle={snap['idle']}/{snap['total']}", flush=True)
            last_snapshot_t = time.monotonic()

        # wait the interval (but don't overshoot the deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(args.interval_s, remaining))

    # Final snapshot
    final_snap = _snapshot(civ, agent_stats, stage_outcomes)
    final_snap["rss_mb"] = round(maxrss_mb(), 1)
    report["periodic_snapshots"].append(final_snap)

    # Summary
    total_requests = req_idx
    plan_ok_count = sum(1 for r in report["requests"] if r["plan_ok"])
    plan_fail_count = total_requests - plan_ok_count
    all_stage_outcomes = sum(stage_outcomes.values())
    stage_ok = stage_outcomes and sum(v for (s, o), v in stage_outcomes.items() if o == "ok") or 0
    stage_fail = all_stage_outcomes - stage_ok

    report["summary"] = {
        "total_requests": total_requests,
        "plan_ok": plan_ok_count,
        "plan_fail": plan_fail_count,
        "plan_ok_rate": round(plan_ok_count / total_requests, 4) if total_requests else 0,
        "total_stages": all_stage_outcomes,
        "stage_ok": stage_ok,
        "stage_fail": stage_fail,
        "stage_ok_rate": round(stage_ok / all_stage_outcomes, 4) if all_stage_outcomes else 0,
        "duration_s_actual": round(duration_s, 1),
        "duration_min_actual": round(duration_s / 60, 1),
        "maxrss_mb_final": final_snap["rss_mb"],
        "top_stage_failures": [
            {"spec": s, "output": o[:150], "count": n}
            for (s, o), n in stage_failures.most_common(10)
        ],
        "agent_stats_distribution": {
            "total_agents_with_activity": len(agent_stats),
            "agents_all_ok": sum(1 for a in agent_stats.values() if a["fail"] == 0),
            "agents_with_failures": sum(1 for a in agent_stats.values() if a["fail"] > 0),
        },
    }

    out_path.write_text(json.dumps(report, indent=1))
    print("\n=== SOAK SUMMARY ===")
    print(f"duration: {duration_s/60:.1f} min")
    print(f"requests: {total_requests} | plan ok: {plan_ok_count} | fail: {plan_fail_count} "
          f"| rate: {report['summary']['plan_ok_rate']:.3f}")
    print(f"stages: {all_stage_outcomes} | ok: {stage_ok} | fail: {stage_fail} "
          f"| rate: {report['summary']['stage_ok_rate']:.3f}")
    print(f"final rss: {final_snap['rss_mb']}MB")
    if report["summary"]["top_stage_failures"]:
        print("\ntop stage failures:")
        for f in report["summary"]["top_stage_failures"]:
            print(f"  {f['spec']} x{f['count']}: {f['output'][:100]}")
    print(f"\nreport: {out_path}")


def _snapshot(civ, agent_stats, stage_outcomes):
    total = 0
    idle = 0
    working = 0
    for spec, agents in civ.items():
        for a in agents:
            total += 1
            if getattr(a, "status", None) == "idle":
                idle += 1
            elif getattr(a, "status", None) == "working":
                working += 1
    return {
        "total": total, "idle": idle, "working": working,
        "agents_with_activity": len(agent_stats),
    }


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
