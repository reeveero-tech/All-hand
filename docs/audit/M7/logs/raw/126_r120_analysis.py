#!/usr/bin/env python3
"""R-120: analysis of the third live hour (report JSON from scripts/run_r119_live_hour.py).

Read-only: computes throughput per 5-minute window, latency per request type,
latency drift over time, memory timeline, agent distribution and unused
specializations. Usage: python 126_r120_analysis.py <report.json>
"""
import json
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

path = Path(sys.argv[1])
d = json.loads(path.read_text(encoding="utf-8"))
s = d["summary"]
reqs = d["requests"]


def pct(vals, p):
    vals = sorted(vals)
    if not vals:
        return 0.0
    k = min(len(vals) - 1, int(round(p / 100.0 * (len(vals) - 1))))
    return vals[k]


def plan_s(r):
    return sum(float(x.get("elapsed_s") or 0.0) for x in r.get("stages", []))


print(f"report: {path.name}")
print(f"git_head: {d['git_head']} | started: {d['started_utc']} | target: {d['duration_min_target']} min "
      f"| actual: {s['duration_s_actual']} s | agents: {d['agents']} ({d['agents_per_spec']}/spec) "
      f"| concurrency: {d['concurrency']} | interval: {d['interval_s']} s | llm: {d['llm_provider']}")
print(f"requests: {s['total_requests']} | plan_ok: {s['plan_ok']} ({s['plan_ok_rate']:.3f}) "
      f"| verified_ok: {s['verified_ok']} ({s['verified_ok_rate']:.3f}) | exceptions: {s['exceptions']}")
print(f"stages: {s['total_stages']} ok={s['stage_ok']} ({s['stage_ok_rate']:.3f}) "
      f"| top_stage_failures: {s['top_stage_failures']} | unverified_examples: {s['unverified_examples']}")
print(f"stuck working agents at end: {s['stuck_working_agents_at_end']} | max in flight: {s['max_in_flight']}")
print(f"keyless llm: {s['keyless_llm']}")

print("\n== snapshots (every 300 s) ==")
prev = None
print("t_s     req  d_req  vmrss_MB  d_vmrss  maxrss_MB  exp   active_agents  llm_calls")
for sn in d["periodic_snapshots"]:
    dr = sn["requests"] - (prev["requests"] if prev else 0)
    dm = sn["vmrss_mb"] - (prev["vmrss_mb"] if prev else 0)
    print(f"{sn['t_s']:6.0f} {sn['requests']:5d} {dr:6d} {sn['vmrss_mb']:9.1f} {dm:+8.1f} {sn['maxrss_mb']:10.1f} "
          f"{sn['experience_entries']:5d} {sn['agents_with_activity']:14d} {sn['keyless_llm']['calls']:10d}")
    prev = sn

print("\n== latency per request type (sum of stage elapsed_s) ==")
by_type = defaultdict(list)
for r in reqs:
    by_type[r["request"]].append(plan_s(r))
print("  n   median   p95     max    type")
for t, v in sorted(by_type.items(), key=lambda kv: -st.median(kv[1])):
    print(f"{len(v):4d} {st.median(v):7.2f} {pct(v, 95):7.2f} {max(v):7.2f}  {t[:60]}")

print("\n== latency drift: first half vs second half of requests ==")
half = len(reqs) // 2
for name, part in (("first", reqs[:half]), ("second", reqs[half:])):
    v = [plan_s(r) for r in part]
    nonllm = [plan_s(r) for r in part if r["primary"] not in ("communicator", "educator", "code_generator")]
    print(f"{name:6s}: n={len(v)} mean={st.mean(v):.3f}s median={st.median(v):.3f}s "
          f"| non-LLM types mean={st.mean(nonllm):.4f}s median={st.median(nonllm):.4f}s")

print("\n== agent distribution ==")
dist = s["distribution"]
print(f"distinct agents used: {dist['distinct_agents_used']} | max stages on one agent: {dist['max_stages_on_one_agent']}")
print(f"per specialization (distinct agents): {dist['per_specialization']}")
stage_specs = Counter(x["spec"] for r in reqs for x in r.get("stages", []))
print(f"stages per specialization: {dict(stage_specs.most_common())}")
agents_per_spec = d["agents_per_spec"]
total_specs = d["agents"] // agents_per_spec if agents_per_spec else 0
print(f"specializations used: {len(dist['per_specialization'])} of {total_specs} "
      f"(the 19 request templates never route to the other {total_specs - len(dist['per_specialization'])})")

print("\n== primaries ==")
print(dict(Counter(r["primary"] for r in reqs).most_common()))
