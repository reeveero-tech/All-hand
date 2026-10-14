"""R-114..R-117 evidence: run every R-109/R-113 request template ONCE through
route() -> execute_plan on a 1,000-agent civilization (live services allowed)
and print each stage: ok, specialist, agent, output. Usage:
  python3 docs/audit/M6/scripts/r114_live_types_check.py [substring ...]
"""
import asyncio, os, sys, time
sys.path.insert(0,"."); os.environ["AGOS_ALLOW_CODE_EXEC"]="1"
ONLY=sys.argv[1:]  # optional substring filters
import importlib.util
spec=importlib.util.spec_from_file_location("s","scripts/run_r119_live_hour.py");m=importlib.util.module_from_spec(spec);sys.argv=["x"];spec.loader.exec_module(m)
from agent_civilization.agents.real import create_civilization
from agent_civilization.pipeline.orchestrator import execute_plan
from agent_civilization.routing.auto_router import route
async def main():
    civ=create_civilization(50)
    only=ONLY
    for t in m.REQUEST_TEMPLATES:
        if only and not any(o in t for o in only): continue
        t0=time.time(); r=await execute_plan(route(t),civ)
        ok=all(s["ok"] for s in r["stages"])
        print(("OK  " if ok else "FAIL"), f"{time.time()-t0:5.1f}s", t[:50])
        for s in r["stages"]:
            print("    ", "✓" if s["ok"] else "✗", s["name"], s["specialization"], s["agent"], "|", (s["output"] or "")[:160].replace("\n"," "))
asyncio.run(main())
