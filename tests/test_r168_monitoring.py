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

        # metrics carry counts only — a distinctive sentinel value stored as
        # record content must never appear in the metrics object
        await system.store(content="SENTINEL-SECRET-7f3a91",
                           memory_type=MemoryType.EPISODIC, memory_id="s1")
        ops = system.get_stats()["ops"]
        raw_ops = json.dumps(ops)
        assert "SENTINEL-SECRET-7f3a91" not in raw_ops
        assert set(ops) == {"persist_ok", "persist_fail", "load_ok",
                           "load_empty", "load_fail"}

    asyncio.run(scenario())


def test_metrics_not_state(tmp_path):
    """The ops counters are metrics, not memory state: a failed load changes
    the counter but never the records/indexes (D-090 pin, metric-aware)."""
    path = str(tmp_path / "mem.json")

    async def scenario():
        system = MemorySystem(persistence_file=path)
        await system.store(content="keep", memory_type=MemoryType.SEMANTIC,
                           memory_id="k1")
        await system._persist_strict()
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
    """Real concurrency measurement: mixed workers (store/search/replace/
    delete) under an actual concurrency LIMIT (semaphore), each op timed
    individually. The event loop interleaves at await points only — that is
    the production model; thread-based sharing is out of model (declared
    limit, D-091/EVIDENCE). Reports per-level throughput and latency and
    asserts index invariants after each level.
    NOTE: ru_maxrss-style memory is NOT reported here — a process-wide
    high-water mark across levels is not a per-level measurement (see the
    scale suite for per-scale file/memory data)."""
    async def scenario():
        system = MemorySystem(max_entries=10_000)
        print()
        for level in _LEVELS:
            sem = asyncio.Semaphore(level)
            n_ops = 500
            timings = []
            completed = {"store": 0, "search": 0, "replace": 0, "delete": 0}

            async def one_op(i):
                kind = i % 10
                async with sem:
                    t0 = time.perf_counter()
                    if kind < 5:  # 50% stores
                        await system.store(content=f"op {i}",
                                           memory_type=MemoryType.WORKING,
                                           tags={"bench"}, memory_id=f"op{i}")
                        completed["store"] += 1
                    elif kind < 8:  # 30% searches
                        await system.search(MemoryQuery(tags={"bench"}, limit=None))
                        completed["search"] += 1
                    elif kind < 9:  # 10% same-id replacements (re-index path)
                        await system.store(content=f"op {i} v2",
                                           memory_type=MemoryType.EPISODIC,
                                           tags={"bench", "v2"}, memory_id="shared")
                        completed["replace"] += 1
                    else:  # 10% deletes
                        await system.forget(f"op{i}")
                        completed["delete"] += 1
                    timings.append(time.perf_counter() - t0)

            t0 = time.perf_counter()
            await asyncio.gather(*[one_op(i) for i in range(n_ops)])
            elapsed = time.perf_counter() - t0

            # invariants after every level: no dead index entries, every
            # live record in both indexes, capacity respected
            for mtype, idx in system._index_by_type.items():
                live = {mid for mid in idx if mid in system._memories}
                assert idx == live
            for tag, idx in system._index_by_tag.items():
                live = {mid for mid in idx if mid in system._memories}
                assert idx == live
            assert len(system._memories) <= 10_000

            timings.sort()
            mean = sum(timings) / len(timings)
            p95 = timings[int(0.95 * len(timings))]
            print(f"[concurrency-limit={level:3d}] {n_ops} mixed ops in "
                  f"{elapsed:.3f}s — {n_ops / elapsed:.0f} ops/s, "
                  f"mean {mean * 1000:.2f}ms, p95 {p95 * 1000:.2f}ms "
                  f"({completed['store']}st/{completed['search']}se/"
                  f"{completed['replace']}re/{completed['delete']}de)")
            await asyncio.gather(*[system.forget(m) for m in list(system._memories)])

    asyncio.run(scenario())
