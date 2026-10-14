
import os, asyncio, time, resource, json
# R-105 scale measurement — the SAME methodology as docs/audit/M7/logs/08 (R-032):
# build the civilization, then execute 1000 tasks; the two timers are SEPARATE.
os.environ["AGOS_AGENT_IMPL"] = "specialists"
os.environ["AGOS_ALLOW_CODE_EXEC"] = "1"  # R-100/D-044 (as the R-053 script documents)

from agent_civilization.agents.real import create_civilization

PAYLOADS = {
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
}

# [1] BUILD (timer 1)
t0 = time.time()
civ = create_civilization(50)
build_s = time.time() - t0
total = sum(len(v) for v in civ.values())

# [2] EXECUTE 1000 tasks (timer 2 — starts AFTER the build)
async def run_all():
    tasks = []
    for spec, agents in civ.items():
        for a in agents:
            payload = PAYLOADS.get(spec, {"prompt": "demo"})
            tasks.append(a.execute(payload))
    return await asyncio.gather(*tasks)

t1 = time.time()
results = asyncio.run(run_all())
exec_s = time.time() - t1
ok_count = sum(1 for r in results if r.get("ok"))
maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
print(f"impl=specialists agents={total} build_s={build_s:.3f} exec_1000_s={exec_s:.2f} ok={ok_count}/{total} maxrss_mb={maxrss:.1f}")
print(f"env: AGOS_AGENT_IMPL=specialists AGOS_ALLOW_CODE_EXEC=1")
assert ok_count == 1000, f"expected 1000/1000, got {ok_count}/{total}"
print("SCALE-OK")
