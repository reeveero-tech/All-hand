# PREP audit probe (read-only characterization of current behaviour; not a test gate, not R-176..R-181 work).
# Run from repo root: AGOS_POOL_LOCAL=2 python3 docs/audit/M8/prep/probe_pools.py
import asyncio, os, sys, time
sys.path.insert(0, '.')
os.environ.setdefault('AGOS_POOL_LOCAL', '2')
from agent_civilization.core import pools


async def main():
    n = pools.size('local')
    # P1: a caller that times out (asyncio.wait_for) does not stop a RUNNING thread call
    started = []
    def slow():
        started.append(time.perf_counter()); time.sleep(1.0); return 'slow'
    for _ in range(n):
        try:
            await asyncio.wait_for(pools.run('local', slow), timeout=0.05)
        except asyncio.TimeoutError:
            pass
    t0 = time.perf_counter()
    await pools.run('local', lambda: 'fast')
    wait = time.perf_counter() - t0
    print(f"P1 pool=local size={n}: {n} callers timed out at 50 ms, their threads kept running; "
          f"next fast call waited {wait*1000:.0f} ms (thread occupancy outlives caller timeout)")
    # P2: in-flight counter after the occupying calls finish
    await asyncio.sleep(1.1)
    print(f"P2 in-flight counter after completion: waiting={pools.waiting('local')} inflight={getattr(pools.get('local'), '_agos_inflight', None)} (expected 0)")
    # P3: a call cancelled while still QUEUED never runs (documented contract)
    ran = []
    blockers = [asyncio.ensure_future(pools.run('local', time.sleep, 0.3)) for _ in range(n)]
    await asyncio.sleep(0.02)
    q = asyncio.ensure_future(pools.run('local', lambda: ran.append(1)))
    await asyncio.sleep(0.02); q.cancel()
    await asyncio.gather(*blockers); await asyncio.sleep(0.05)
    print(f"P3 queued-then-cancelled call executed: {bool(ran)} (contract: never runs); inflight={pools.get('local')._agos_inflight}")
    # P4: no shutdown hook — executors stay alive until interpreter exit
    print(f"P4 live executors at end of probe: {sorted(pools.snapshot())}; any module-level shutdown function: "
          f"{[k for k in dir(pools) if 'shutdown' in k.lower() or 'close' in k.lower()]}")

asyncio.run(main())
