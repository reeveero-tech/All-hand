"""R-168 scale: 10 / 100 / 1k / 10k / 100k entries, measured separately.

P4 acceptance: latency/memory reported for the file-backed backend. Every
step asserts CORRECTNESS too (loaded count, indexes, search results) — a
measurement that loses data is a bug, not a benchmark. The numbers are
printed per scale and captured in the raw evidence log; they are real
measurements on this machine (not simulations), and the 100k step uses
100k REAL engine records (not 100k logical placeholders).
"""
import asyncio
import json
import os
import resource
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agos_kernel.agent_production.memory_system import (  # noqa: E402
    MemoryQuery,
    MemorySystem,
    MemoryType,
)

SCALES = [10, 100, 1_000, 10_000, 100_000]


def _rss_mb() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF)
    return ru.ru_maxrss / 1024.0  # linux: KiB -> MB


@pytest.mark.parametrize("n", SCALES)
def test_scale_write_persist_load_search(tmp_path, n):
    """For each scale: write n real records, persist, reload in a fresh
    engine, verify everything survived, and measure each phase."""
    async def scenario():
        path = str(tmp_path / f"scale_{n}.json")
        system = MemorySystem(max_entries=max(n, 1000), persistence_file=path)

        t0 = time.perf_counter()
        for i in range(n):
            await system.store(
                content=f"scale entry {i} searchable token shared{i % 100}",
                memory_type=MemoryType.EPISODIC if i % 2 else MemoryType.SEMANTIC,
                tags={f"t{i % 10}"}, metadata={"tenant_id": f"tn{i % 5}"},
                importance=0.5, memory_id=f"s{i}")
        t_write = time.perf_counter() - t0

        t0 = time.perf_counter()
        system._persist_strict()
        t_persist = time.perf_counter() - t0
        file_mb = os.path.getsize(path) / 1e6

        fresh = MemorySystem(max_entries=max(n, 1000), persistence_file=path)
        # load decomposed: parse (json) vs validate+commit (the rest)
        t0 = time.perf_counter()
        with open(path, encoding="utf-8") as f:
            json.load(f)
        t_parse = time.perf_counter() - t0
        t0 = time.perf_counter()
        assert fresh._load_strict() is True
        t_load = time.perf_counter() - t0
        t_validate_commit = t_load - t_parse  # includes one redundant parse; an upper bound

        t0 = time.perf_counter()
        hits = await fresh.search(MemoryQuery(tags={"t3"}, limit=None))
        t_search = time.perf_counter() - t0

        # correctness at scale: nothing lost, indexes complete, search exact
        assert len(fresh._memories) == n
        expect_t3 = {f"s{i}" for i in range(n) if i % 10 == 3}
        assert {e.memory_id for e in hits} == expect_t3
        for mtype in (MemoryType.EPISODIC, MemoryType.SEMANTIC):
            idx = fresh._index_by_type[mtype]
            live = {mid for mid in idx if mid in fresh._memories}
            assert idx == live  # no dead index entries at 100k either

        # rss is the WHOLE-PROCESS high-water mark (cumulative across the
        # earlier scales in this process) — labeled as such, not per-scale
        print(f"\n[n={n}] write={t_write:.3f}s persist={t_persist:.3f}s "
              f"load={t_load:.3f}s (parse={t_parse:.3f}s, "
              f"validate+commit<={t_validate_commit:.3f}s) "
              f"search={t_search:.4f}s file={file_mb:.1f}MB "
              f"rss_hwm={_rss_mb():.0f}MB (process-wide, cumulative)")

    asyncio.run(scenario())
