#!/usr/bin/env python3
"""
R-119: live civilization hour WITH TRUTH VERIFICATION (R-118).

R-122: 24 templates since 2026-10-15 (the 19 of R-109/R-113/R-120 + one per
specialization those 19 never reached: builder, designer, data_processor,
api_integrator, self_developer) — every one of the 20 specializations is used.

Same workload as R-109/R-113 (the 19 realistic request templates, routed
through `route()` -> `execute_plan`, the production path, 1,000 real agents),
plus what R-113 lacked:

* verified_ok — per request type, an expected-outcome check on the actual
  outputs (translation contains "monde", sum=15, average=15, HTTP status
  present, research returns >=1 source, generated code passed its tests,
  schema executed, migration 0 mismatches, ...). `plan_ok` only means every
  stage returned ok=True; `verified_ok` means the answer is also right.
  R-113 counted "tone=neutral" for a translation as a success — this does not.
* distribution — distinct agents used, per specialization;
* keyless LLM stats (calls / ok / rate-limited / errors);
* memory — experience-store size and the REAL resident set (VmRSS) besides
  the peak (maxrss);
* --concurrency N — N request loops share the civilization (real
  coordination: concurrent plans must not grab the same agent).
"""
import argparse
import asyncio
import json
import os
import random
import re
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


def vmrss_mb():
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return None


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
    # R-122: the five specializations no template reached in R-120 (250 idle agents)
    "scaffold a starter project for a todo web service",            # builder
    "pick a dark mode color palette from #3366ff for the dashboard",  # designer
    "aggregate these sales figures: [120, 80, 95, 300, 150]",         # data_processor
    "integrate with the public endpoint https://example.com",       # api_integrator
    "self improve: inspect your own specializations and evolve",     # self_developer
]

# R-122: the specialization each template must be routed to (primary stage).
# 20/20 specializations covered — test_r122_* asserts it against route().
EXPECTED_PRIMARY = {
    "scaffold a starter project for a todo web service": "builder",
    "pick a dark mode color palette from #3366ff for the dashboard": "designer",
    "aggregate these sales figures: [120, 80, 95, 300, 150]": "data_processor",
    "integrate with the public endpoint https://example.com": "api_integrator",
    "self improve: inspect your own specializations and evolve": "self_developer",
}




def _out(stages, name):
    for s in stages:
        if s.get("name") == name:
            return str(s.get("output") or "")
    return ""


_TESTS_GREEN = re.compile(r"[1-9]\d* test\(s\) passed, 0 failed")

# request template -> (description, check(stages) -> bool)
TRUTH = {
    "write a function that returns the nth prime":
        ("LLM code generated AND its tests passed",
         lambda st: "generated" in _out(st, "execute") and bool(_TESTS_GREEN.search(_out(st, "test")))),
    "review this code for bugs: def f(x): return x*2":
        ("diagnosis ran and the code's tests passed",
         lambda st: "issue(s)" in _out(st, "execute") and bool(_TESTS_GREEN.search(_out(st, "test")))),
    "refactor this to be cleaner: if a: if b: if c: return 1":
        ("nested ifs merged, equivalence proven, tests passed",
         lambda st: "behaviour identical" in _out(st, "execute") and bool(_TESTS_GREEN.search(_out(st, "test")))),
    "generate a python class for a user account with validation":
        ("LLM class generated AND smoke-tested",
         lambda st: "generated" in _out(st, "execute") and bool(_TESTS_GREEN.search(_out(st, "test")))),
    "analyze this data for trends: [10, 20, 30, 40, 50]":
        ("trend=up detected and integrity checked",
         lambda st: "trend=up" in _out(st, "execute") and "checks passed" in _out(st, "validate")),
    "sum these numbers: [1, 2, 3, 4, 5]":
        ("sum=15", lambda st: "sum=15" in _out(st, "execute")),
    "compute the average of [12, 14, 16, 18]":
        ("average=15", lambda st: "average=15" in _out(st, "execute")),
    "design a scalable multi-tenant SaaS architecture":
        ("tenancy component + >=1 live source",
         lambda st: "tenancy" in _out(st, "execute") and "source(s)" in _out(st, "ground")),
    "plan a database migration for a 10M row table":
        ("rehearsal verified with 0 mismatches", lambda st: "0 mismatches" in _out(st, "execute")),
    "review the architecture for a REST API with rate limiting":
        ("rate_limiting component + >=1 live source",
         lambda st: "rate_limiting" in _out(st, "execute") and "source(s)" in _out(st, "ground")),
    'validate this JSON schema: {"name": "string", "age": "number"}':
        ("embedded JSON parsed", lambda st: "valid JSON" in _out(st, "execute")),
    "write and run tests for: def add(a, b): return a + b":
        ("tests ran and passed", lambda st: bool(_TESTS_GREEN.search(_out(st, "execute")))),
    "design a table schema for orders with line items":
        ("DDL executed in SQLite", lambda st: "executed in SQLite" in _out(st, "execute")),
    "check if example.com is reachable":
        ("real HTTP status returned", lambda st: bool(re.search(r"reachable: HTTP [1-5]\d\d", _out(st, "execute")))),
    "draft a message to the team about the outage":
        (">=30-word draft that mentions the outage",
         lambda st: len(_out(st, "execute").split()) >= 30 and "outage" in _out(st, "execute").lower()),
    "explain the difference between REST and GraphQL":
        (">=40-word explanation naming both",
         lambda st: len(_out(st, "execute").split()) >= 40
         and "rest" in _out(st, "execute").lower() and "graphql" in _out(st, "execute").lower()),
    "translate 'hello world' to French":
        ("French translation (contains 'monde')", lambda st: "monde" in _out(st, "execute").lower()),
    "research the best practices for API pagination":
        (">=1 live source", lambda st: bool(re.search(r"[1-9]\d* source\(s\)", _out(st, "execute")))),
    "monitor the health of the system":
        ("measured health verdict", lambda st: bool(re.search(r"system (healthy|degraded): rss=", _out(st, "execute")))),
    # R-122
    "scaffold a starter project for a todo web service":
        ("all scaffolded files verified, main module reviewed AND its tests passed",
         lambda st: bool(re.search(r"scaffolded (\d+) file\(s\).*; \1/\1 passed verification", _out(st, "execute")))
         and "reviewed" in _out(st, "review") and bool(_TESTS_GREEN.search(_out(st, "test")))),
    "pick a dark mode color palette from #3366ff for the dashboard":
        ("dark palette from #3366ff, all WCAG contrast targets met",
         lambda st: "dark palette from hex #3366ff" in _out(st, "execute")
         and "all WCAG targets met" in _out(st, "execute")),
    "aggregate these sales figures: [120, 80, 95, 300, 150]":
        ("sum=745 median=120, integrity checked",
         lambda st: "sum=745" in _out(st, "execute") and "median=120" in _out(st, "execute")
         and "checks passed" in _out(st, "validate")),
    "integrate with the public endpoint https://example.com":
        ("real HTTP response on GET + live probe",
         lambda st: bool(re.search(r"-> HTTP [1-5]\d\d \(", _out(st, "execute")))
         and bool(re.search(r"HTTP [1-5]\d\d", _out(st, "probe")))),
    "self improve: inspect your own specializations and evolve":
        ("self-inspection lists the 20 real handlers",
         lambda st: "20 specializations backed by real handlers" in _out(st, "execute")),
}


def verify(request, stages):
    desc, check = TRUTH[request]
    try:
        return bool(check(stages)), desc
    except Exception:  # noqa: BLE001
        return False, desc


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration-min", type=float, default=60)
    ap.add_argument("--interval-s", type=float, default=3.0,
                    help="seconds between requests, per loop (default 3.0)")
    ap.add_argument("--concurrency", type=int, default=2,
                    help="concurrent request loops sharing the civilization (default 2)")
    ap.add_argument("--agents-per-spec", type=int, default=50)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--out", required=True, help="output JSON path")
    args = ap.parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    assert set(TRUTH) == set(REQUEST_TEMPLATES), "every template needs a truth check"

    from agent_civilization.agents.real import create_civilization
    from agent_civilization.core import experience, llm as llm_mod
    from agent_civilization.pipeline.orchestrator import execute_plan
    from agent_civilization.routing.auto_router import route

    duration_s = args.duration_min * 60
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "duration_min_target": args.duration_min, "interval_s": args.interval_s,
        "concurrency": args.concurrency, "agents_per_spec": args.agents_per_spec,
        "llm_provider": llm_mod.get_llm().provider,
        "git_head": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                   cwd=str(REPO_ROOT)).stdout.strip(),
        "requests": [], "periodic_snapshots": [], "summary": {},
    }
    t0 = time.time()
    civ = create_civilization(args.agents_per_spec)
    report["build_s"] = round(time.time() - t0, 3)
    report["agents"] = sum(len(v) for v in civ.values())
    print(f"[build] agents={report['agents']} build_s={report['build_s']} "
          f"llm_provider={report['llm_provider']} concurrency={args.concurrency}", flush=True)

    agent_stats = defaultdict(lambda: {"ok": 0, "fail": 0})
    stage_outcomes, stage_failures = Counter(), Counter()
    per_type = defaultdict(lambda: Counter())
    unverified_examples = defaultdict(list)
    counter = {"idx": 0, "max_in_flight": 0, "in_flight": 0}
    start = time.monotonic()
    deadline = start + duration_s

    def snapshot():
        idle = sum(1 for p in civ.values() for a in p if getattr(a, "status", None) == "idle")
        working = sum(1 for p in civ.values() for a in p if getattr(a, "status", None) == "working")
        try:
            mem_entries = len(experience.memory()._memories)
        except Exception:  # noqa: BLE001
            mem_entries = None
        return {"t_s": round(time.monotonic() - start, 1), "requests": counter["idx"],
                "idle": idle, "working": working, "total": report["agents"],
                "agents_with_activity": len(agent_stats),
                "vmrss_mb": round(vmrss_mb() or 0, 1), "maxrss_mb": round(maxrss_mb(), 1),
                "experience_entries": mem_entries, "keyless_llm": dict(llm_mod.keyless_stats)}

    async def loop(worker):
        while time.monotonic() < deadline:
            counter["idx"] += 1
            idx = counter["idx"]
            request = random.choice(REQUEST_TEMPLATES)
            plan = route(request)
            t1 = time.monotonic()
            counter["in_flight"] += 1
            counter["max_in_flight"] = max(counter["max_in_flight"], counter["in_flight"])
            try:
                result = await execute_plan(plan, civ)
                stages = result.get("stages", [])
                plan_ok = all(s.get("ok") for s in stages)
                ok_v, _ = verify(request, stages)
                verified_ok = plan_ok and ok_v
                for s in stages:
                    stage_outcomes[(s.get("specialization"), "ok" if s.get("ok") else "fail")] += 1
                    if not s.get("ok"):
                        stage_failures[(s.get("specialization"), str(s.get("output"))[:120])] += 1
                    if s.get("agent"):
                        agent_stats[s["agent"]]["ok" if s.get("ok") else "fail"] += 1
                per_type[request]["n"] += 1
                per_type[request]["plan_ok"] += plan_ok
                per_type[request]["verified_ok"] += verified_ok
                if plan_ok and not ok_v and len(unverified_examples[request]) < 3:
                    unverified_examples[request].append({s["name"]: str(s.get("output"))[:200] for s in stages})
                report["requests"].append({
                    "idx": idx, "worker": worker, "request": request, "primary": plan.get("primary"),
                    "stages": [{"name": s.get("name"), "spec": s.get("specialization"), "ok": s.get("ok"),
                                "agent": s.get("agent"), "elapsed_s": s.get("elapsed_s"),
                                "output": str(s.get("output"))[:240]} for s in stages],
                    "plan_s": round(time.monotonic() - t1, 3), "plan_ok": plan_ok, "verified_ok": verified_ok})
                mark = "VERIFIED" if verified_ok else ("OK-UNVERIFIED" if plan_ok else "FAIL")
                print(f"[req {idx:4d} w{worker}] {mark} primary={plan.get('primary')} "
                      f"plan_s={time.monotonic() - t1:.2f} request='{request[:48]}'", flush=True)
            except Exception as exc:  # noqa: BLE001
                per_type[request]["n"] += 1
                per_type[request]["exception"] += 1
                report["requests"].append({"idx": idx, "worker": worker, "request": request,
                                           "primary": plan.get("primary"), "stages": [],
                                           "plan_ok": False, "verified_ok": False,
                                           "exception": f"{type(exc).__name__}: {exc}"})
                print(f"[req {idx:4d} w{worker}] EXCEPTION {type(exc).__name__}: {exc}", flush=True)
            finally:
                counter["in_flight"] -= 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(args.interval_s, remaining))

    async def snapshots():
        while time.monotonic() < deadline:
            await asyncio.sleep(min(300, max(1, deadline - time.monotonic())))
            snap = snapshot()
            report["periodic_snapshots"].append(snap)
            print(f"[snapshot] t={snap['t_s']}s req={snap['requests']} vmrss={snap['vmrss_mb']}MB "
                  f"maxrss={snap['maxrss_mb']}MB idle={snap['idle']}/{snap['total']} "
                  f"active_agents={snap['agents_with_activity']} exp={snap['experience_entries']} "
                  f"llm={snap['keyless_llm']}", flush=True)

    await asyncio.gather(snapshots(), *(loop(w) for w in range(args.concurrency)))
    final = snapshot()
    report["periodic_snapshots"].append(final)
    actual_s = time.monotonic() - start

    reqs = report["requests"]
    n = len(reqs)
    plan_ok_n = sum(1 for r in reqs if r["plan_ok"])
    ver_n = sum(1 for r in reqs if r["verified_ok"])
    st_total = sum(stage_outcomes.values())
    st_ok = sum(v for (s, o), v in stage_outcomes.items() if o == "ok")
    by_spec = defaultdict(set)
    for name in agent_stats:
        by_spec[name.rsplit("_", 1)[0]].add(name)
    report["summary"] = {
        "total_requests": n, "plan_ok": plan_ok_n, "verified_ok": ver_n,
        "plan_ok_rate": round(plan_ok_n / n, 4) if n else 0,
        "verified_ok_rate": round(ver_n / n, 4) if n else 0,
        "exceptions": sum(1 for r in reqs if r.get("exception")),
        "total_stages": st_total, "stage_ok": st_ok,
        "stage_ok_rate": round(st_ok / st_total, 4) if st_total else 0,
        "duration_s_actual": round(actual_s, 1), "max_in_flight": counter["max_in_flight"],
        "per_type": {t: {"n": c["n"], "plan_ok": c["plan_ok"], "verified_ok": c["verified_ok"],
                         "exceptions": c["exception"], "truth_check": TRUTH[t][0]}
                     for t, c in sorted(per_type.items())},
        "unverified_examples": dict(unverified_examples),
        "top_stage_failures": [{"spec": s, "output": o, "count": k}
                               for (s, o), k in stage_failures.most_common(15)],
        "distribution": {"distinct_agents_used": len(agent_stats),
                         "per_specialization": {s: len(v) for s, v in sorted(by_spec.items())},
                         "max_stages_on_one_agent": max((a["ok"] + a["fail"] for a in agent_stats.values()), default=0)},
        "memory": {"vmrss_mb_final": final["vmrss_mb"], "maxrss_mb_final": final["maxrss_mb"],
                   "experience_entries_final": final["experience_entries"]},
        "keyless_llm": dict(llm_mod.keyless_stats),
        "stuck_working_agents_at_end": final["working"],
    }
    out_path.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    s = report["summary"]
    print("\n=== R-119 SUMMARY ===")
    print(f"duration: {actual_s / 60:.1f} min | requests: {n} | plan_ok: {plan_ok_n} ({s['plan_ok_rate']:.3f}) "
          f"| VERIFIED: {ver_n} ({s['verified_ok_rate']:.3f}) | exceptions: {s['exceptions']}")
    print(f"stages: {st_total} ok={st_ok} ({s['stage_ok_rate']:.3f}) | distinct agents: "
          f"{s['distribution']['distinct_agents_used']} | max in flight: {counter['max_in_flight']}")
    print(f"memory: vmrss={final['vmrss_mb']}MB maxrss={final['maxrss_mb']}MB exp={final['experience_entries']}")
    print(f"keyless llm: {s['keyless_llm']}")
    for t, c in s["per_type"].items():
        print(f"  {c['verified_ok']:4d}/{c['n']:<4d} verified ({c['plan_ok']} plan_ok)  {t[:60]}")
    print(f"\nreport: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
