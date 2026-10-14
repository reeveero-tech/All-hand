"""R-117 evidence: memory diagnosis. 3,000 offline requests (no LLM / web
templates), printing CURRENT RSS (VmRSS), peak (maxrss), Python-traced memory
and experience-store size every 500 requests, then the top allocation growth
sites between request 300 and 3,000."""
import asyncio, os, sys, tracemalloc, random, gc, resource
sys.path.insert(0,".")
os.environ.setdefault("AGOS_ALLOW_CODE_EXEC","1")
sys.argv=["x", "--out", "/dev/null"]
import importlib.util
spec=importlib.util.spec_from_file_location("soak","scripts/run_r119_live_hour.py"); soak=importlib.util.module_from_spec(spec); spec.loader.exec_module(soak)
from agent_civilization.agents.real import create_civilization
from agent_civilization.pipeline.orchestrator import execute_plan
from agent_civilization.routing.auto_router import route
from agent_civilization.core import experience
def cur_rss():
    for l in open("/proc/self/status"):
        if l.startswith("VmRSS"): return int(l.split()[1])/1024
async def main():
    civ=create_civilization(50)
    tracemalloc.start(15)
    snap0=None
    reqs=[t for t in soak.REQUEST_TEMPLATES if not any(k in t for k in ("research", "explain", "draft", "translate", "nth prime", "python class", "architecture", "reachable"))]
    for i in range(1,3001):
        await execute_plan(route(random.choice(reqs)), civ)
        if i==300: gc.collect(); snap0=tracemalloc.take_snapshot()
        if i%500==0:
            gc.collect(); print(i,"rss_now",round(cur_rss(),1),"maxrss",round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1),"traced",round(tracemalloc.get_traced_memory()[0]/1e6,2),"MB","mem_entries",experience.stats(),flush=True)
    gc.collect(); s=tracemalloc.take_snapshot()
    for st in s.compare_to(snap0,"traceback")[:6]:
        print(st); print("\n".join(st.traceback.format()[-8:]))
asyncio.run(main())
