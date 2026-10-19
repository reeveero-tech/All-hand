#!/usr/bin/env python3
"""R-167: a small repeatable measurement of the two IMPLEMENTED strategies
(in-process vs file-backed — the same engine, one volatile, one with an
explicit JSON snapshot), with representative data.

Per the D-089 review directives:
- write/search latency is measured SEPARATELY from persist/load cost (the
  file strategy writes in-memory like the process one; persistence is an
  explicit snapshot, so its cost must not be blended into search numbers).
- the capacity is set to fit the whole sample; the actual record count is
  recorded with every number.
- these numbers cover the two implemented strategies ONLY; no claim is made
  about a DB or a vector index (both are documented options, not implemented).
"""
import asyncio
import os
import sys
import tempfile
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../.."))
sys.path.insert(0, REPO_ROOT)

from agos_kernel.agent_production.memory_store import FileBackedStore, InProcessStore, store_for
from agos_kernel.agent_production.memory_system import MemoryQuery, MemorySystem, MemoryType


def make_entries(n):
    return [
        (f"entry {i}: analyze the quarterly dataset and summarize the findings",
         MemoryType.EPISODIC,
         {f"spec:analyst", "kind:text", f"tenant:t{i % 7}"},
         {"layer": "experience", "tenant_id": f"t{i % 7}", "specialization": "analyst",
          "prompt": f"request {i}", "words": [f"w{i % 500}", "analyze", "data"]})
        for i in range(n)
    ]


async def bench(strategy, n, tmp):
    if strategy == "process":
        st = InProcessStore(MemorySystem(max_entries=n + 1000))
    else:
        st = FileBackedStore(MemorySystem(max_entries=n + 1000,
                                          persistence_file=os.path.join(tmp, f"m{n}.json")))
    entries = make_entries(n)
    t0 = time.perf_counter()
    for content, mtype, tags, md in entries:
        await st.write(content, mtype, tags=tags, metadata=md, importance=0.5)
    write_ms = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    hits = await st.search(MemoryQuery(query="analyze", tags={"spec:analyst"}, limit=None))
    search_ms = (time.perf_counter() - t0) * 1000

    if strategy == "file":
        t0 = time.perf_counter()
        ok_p = await st.persist()
        persist_ms = (time.perf_counter() - t0) * 1000
        st2 = FileBackedStore(MemorySystem(max_entries=n + 1000,
                                          persistence_file=os.path.join(tmp, f"m{n}.json")))
        t0 = time.perf_counter()
        ok_l = await st2.load()
        load_ms = (time.perf_counter() - t0) * 1000
        loaded = len(st2.entries())
    else:
        persist_ms = load_ms = None
        ok_p = ok_l = None
        loaded = None
    return {"strategy": strategy, "records": n, "actual_entries": len(st.entries()),
            "write_ms": round(write_ms, 1), "search_ms": round(search_ms, 1),
            "search_hits": len(hits), "persist_ms": persist_ms, "load_ms": load_ms,
            "persist_ok": ok_p, "load_ok": ok_l, "loaded": loaded}

    return {"strategy": strategy, "records": n, "actual_entries": len(st.entries()),
            "write_ms": round(write_ms, 1), "search_ms": round(search_ms, 1),
            "search_hits": len(hits), "persist_ms": persist_ms, "load_ms": load_ms,
            "persist_ok": ok_p, "load_ok": ok_l, "loaded_entries": loaded}


async def main():
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for n in (100, 1000, 10000):
            for strategy in ("process", "file"):
                rows.append(await bench(strategy, n, tmp))
    print(f"{'strategy':<9} {'records':>7} {'entries':>7} {'write_ms':>9} {'search_ms':>9} "
          f"{'hits':>6} {'persist_ms':>10} {'load_ms':>9} {'loaded':>7}")
    for r in rows:
        print(f"{r['strategy']:<9} {r['records']:>7} {r['actual_entries']:>7} "
              f"{r['write_ms']:>9} {r['search_ms']:>9} {r['search_hits']:>6} "
              f"{str(r['persist_ms']):>10} {str(r['load_ms']):>9} {str(r['loaded']):>7}")
    print("\nnotes: capacity set to n+1000 for every run (no pruning during the run);")
    print("persist/load are measured SEPARATELY (snapshot model — writes are in-memory")
    print("for both strategies); search is the tag-intersection + substring query.")


if __name__ == "__main__":
    asyncio.run(main())
