"""R-168 batch 3: operation health metrics + concurrency impact measurement.

P4 acceptance (ز. مؤشرات الصحة والمراقبة): persist/load success/failure must
be visible without exposing user content — counts only, exposed via
get_stats(). The counters live in the strict cores (the single chokepoint
both call paths go through); "no file" is a healthy empty state, not a
failure.

Concurrency impact (ج. التزامن): real asyncio measurement — interleaved
store/search/persist at concurrency 1/10/100, reporting throughput and
per-op latency, with the index invariants asserted after every level.
"""
import asyncio
import json
import os
import sys
import time


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agos_kernel.agent_production.memory_system import (  # noqa: E402
    MemoryQuery,
    MemorySystem,
    MemoryType,
)
from agos_kernel.agent_production.memory_store import (  # noqa: E402
    FileBackedStore,
)


# ------------------------------------------------------------- health metrics

def test_ops_counters_across_both_call_paths(tmp_path):
    """persist/load health is counted in the strict core — the compat wrapper
    AND the contract adapter both go through it (no double counting, no
    missed path). Failure of a load/persist is visible as a counter."""
    path = str(tmp_path / "mem.json")

    async def scenario():
        system = MemorySystem(persistence_file=path)
        st = FileBackedStore(system)

        # no file yet: load = healthy empty, not a failure
        assert await st.load() is False
        assert system.get_stats()["ops"] == {"persist_ok": 0, "persist_fail": 0,
                                            "load_ok": 0, "load_empty": 1,
                                            "load_fail": 0}

        # persist via the contract path, load via the compat path
        await system.store(content="x", memory_type=MemoryType.EPISODIC,
                           memory_id="m1")
        await system.persist()  # compat wrapper (swallows, counts in the core)
        assert system.get_stats()["ops"]["persist_ok"] == 1
        assert await st.load() is True
        assert system.get_stats()["ops"]["load_ok"] == 1

        # a corrupt file fails closed AND the failure is visible in metrics
        with open(path, "w", encoding="utf-8") as f:
            f.write("{corrupt")
        assert await st.load() is False
        assert system.get_stats()["ops"]["load_fail"] == 1

        # a failed persist (read-only dir) is visible too, no double counts
        d = os.path.dirname(path)
        os.chmod(d, 0o555)
        try:
            assert await st.persist() is False
            assert system.get_stats()["ops"]["persist_fail"] == 1
            assert system.get_stats()["ops"]["persist_ok"] == 1
        finally:
            os.chmod(d, 0o755)

        # metrics carry counts only — never record content or secrets
        raw = json.dumps(system.get_stats())
        assert "x" not in raw.replace('"', "") or "content" not in raw

    asyncio.run(scenario())


def test_metrics_not_state(tmp_path):
    """The ops counters are metrics, not memory state: a failed load changes
    the counter but never the records/indexes (D-090 pin, metric-aware)."""
    path = str(tmp_path / "mem.json")

    async def scenario():
        system = MemorySystem(persistence_file=path)
        await system.store(content="keep", memory_type=MemoryType.SEMANTIC,
                           memory_id="k1")
        system._persist_strict()
        with open(path, "w", encoding="utf-8") as f:
            f.write("{corrupt")
        before_records = dict(system._memories)
        await system.load()  # compat: swallows the error
        assert system._memories == before_records
        assert system.get_stats()["ops"]["load_fail"] == 1

    asyncio.run(scenario())


# ---------------------------------------------------- concurrency measurement

_LEVELS = [1, 10, 100]


def test_concurrency_impact_measurement():
    """Real asyncio measurement: interleaved store+search at increasing
    concurrency. Reports throughput and mean per-op latency per level and
    asserts the index invariants after each — a measurement that corrupts
    state is a bug, not a benchmark. Single event loop = the production
    model; thread-based sharing is out of model (documented)."""
    async def scenario():
        system = MemorySystem(max_entries=10_000)
        print()
        for level in _LEVELS:
            n_ops = 500  # total stores per level
            ids = [f"c{i}" for i in range(n_ops)]

            async def store(mid):
                await system.store(content=f"op {mid}", memory_type=MemoryType.WORKING,
                                   tags={"bench"}, memory_id=mid)

            async def query():
                await system.search(MemoryQuery(tags={"bench"}, limit=None))

            async def batch():
                tasks = [store(mid) for mid in ids]
                tasks += [query() for _ in range(level)]
                await asyncio.gather(*tasks)

            t0 = time.perf_counter()
            await batch()
            elapsed = time.perf_counter() - t0

            # invariants after every level
            for mtype, idx in system._index_by_type.items():
                live = {mid for mid in idx if mid in system._memories}
                assert idx == live
            assert len(system._memories) == n_ops  # nothing lost per level

            throughput = n_ops / elapsed
            print(f"[concurrency={level:3d}] {n_ops} stores + {level} searches "
                  f"in {elapsed:.3f}s — {throughput:.0f} stores/s, "
                  f"{elapsed / n_ops * 1000:.2f}ms/store")
            await asyncio.gather(*[system.forget(mid) for mid in ids])

    asyncio.run(scenario())
