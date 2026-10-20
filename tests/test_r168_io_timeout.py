"""R-168 (storage-timeout criterion): configurable I/O timeout for persist/load — RED test.

The P4 scope for R-168 includes «مهلات وفشل التخزين» (storage timeouts AND
failures). Storage *failures* are covered (read-only dir, injected ENOSPC,
os.replace failure, unreadable file, leftover .tmp). Storage *timeouts* are
not: the persist/load cores run synchronous file I/O on the event loop with
no timeout mechanism at all.

This suite was written BEFORE the fix and run on the unmodified tree (its
result recorded in the audit evidence). It exercises a new, explicit
configuration surface:

- ``MemorySystem(io_timeout=...)`` — optional, ``None`` by default (no
  limit; exact pre-change behavior).
- With a timeout set, a storage operation that exceeds it raises
  ``asyncio.TimeoutError`` and is counted as a failure.
- Timeout semantics are FAIL-CLOSED and ATOMIC:
  - persist timeout: the previous snapshot survives (an in-flight atomic
    write may still land later — it is atomic and valid, never partial).
  - load timeout: NO state change at all — the read/parse/validate phase
    runs before any commit, so a timeout leaves the engine exactly as it
    was.
"""
import asyncio
import json
import time

import pytest

from agos_kernel.agent_production.memory_system import (  # noqa: E402
    MemorySystem,
    MemoryType,
)

pytestmark = pytest.mark.asyncio


def _path(tmp_path):
    return str(tmp_path / "mem.json")


async def _make_system(tmp_path, io_timeout=None, entries=()):
    system = MemorySystem(persistence_file=_path(tmp_path), io_timeout=io_timeout)
    for i, (mid, content, mtype, tags) in enumerate(entries):
        await system.store(
            content=content,
            memory_type=mtype,
            tags=tags,
            metadata={"tenant_id": "t1"},
            memory_id=mid,
        )
    return system


def _ids_in_file(path):
    with open(path, encoding="utf-8") as f:
        return set(json.load(f).keys())


async def test_persist_timeout_raises_and_keeps_last_snapshot(tmp_path):
    system = await _make_system(tmp_path, io_timeout=30.0)
    await system.store(content="first", memory_type=MemoryType.EPISODIC, memory_id="m1")
    await system.persist()  # normal write inside the generous timeout
    assert _ids_in_file(_path(tmp_path)) == {"m1"}

    await system.store(content="second", memory_type=MemoryType.SEMANTIC, memory_id="m2")

    def slow_write(data, seq=None):  # seq: D-094 seam signature
        time.sleep(0.5)  # simulates hung/slow storage I/O

    system._write_snapshot_atomic = slow_write  # noqa: SLF001 — test seam
    system._io_timeout = 0.05  # noqa: SLF001 — now far below the injected delay

    with pytest.raises(asyncio.TimeoutError):
        await system._persist_strict()

    # FAIL-CLOSED: the last good snapshot survives the timed-out persist.
    assert _ids_in_file(_path(tmp_path)) == {"m1"}
    # The timeout is a persist failure in the ops metrics.
    assert system._ops["persist_fail"] == 1  # noqa: SLF001


async def test_load_timeout_changes_nothing(tmp_path):
    # Build a file with one entry.
    writer = await _make_system(tmp_path)
    await writer.store(content="on-disk", memory_type=MemoryType.EPISODIC, memory_id="disk1")
    await writer.persist()

    # A second engine holds DIFFERENT live state that a failed load must keep.
    system = MemorySystem(persistence_file=_path(tmp_path), io_timeout=0.05)
    await system.store(content="live-only", memory_type=MemoryType.SEMANTIC, memory_id="live1")
    before_memories = dict(system._memories)  # noqa: SLF001
    before_type_index = {k: set(v) for k, v in system._index_by_type.items()}  # noqa: SLF001

    def slow_read():
        time.sleep(0.5)  # simulates hung/slow storage I/O
        raise AssertionError("must not complete")

    system._read_snapshot = slow_read  # noqa: SLF001 — test seam

    with pytest.raises(asyncio.TimeoutError):
        await system._load_strict()

    # FAIL-CLOSED and ATOMIC: nothing from the file was committed.
    assert dict(system._memories) == before_memories  # noqa: SLF001
    assert {k: set(v) for k, v in system._index_by_type.items()} == before_type_index  # noqa: SLF001
    assert system._ops["load_fail"] == 1  # noqa: SLF001


async def test_default_is_no_timeout_backward_compatible(tmp_path):
    # No io_timeout configured: persist/load behave exactly as before.
    system = await _make_system(tmp_path)  # io_timeout=None
    assert system._io_timeout is None  # noqa: SLF001
    await system.store(content="compat", memory_type=MemoryType.EPISODIC, memory_id="c1")
    assert await system.persist() is None
    assert _ids_in_file(_path(tmp_path)) == {"c1"}

    fresh = MemorySystem(persistence_file=_path(tmp_path))
    assert await fresh._load_strict() is True  # noqa: SLF001
    assert set(fresh._memories.keys()) == {"c1"}  # noqa: SLF001


async def test_generous_timeout_allows_normal_io(tmp_path):
    # A timeout that is larger than the operation takes must not interfere.
    system = MemorySystem(persistence_file=_path(tmp_path), io_timeout=5.0)
    await system.store(content="ok", memory_type=MemoryType.EPISODIC, memory_id="ok1")
    await system._persist_strict()
    assert system._ops["persist_ok"] == 1  # noqa: SLF001

    fresh = MemorySystem(persistence_file=_path(tmp_path), io_timeout=5.0)
    await fresh._load_strict()
    assert set(fresh._memories.keys()) == {"ok1"}  # noqa: SLF001


async def test_concurrent_persists_are_serialized(tmp_path):
    # Two persists racing must not interleave writes to the same .tmp path.
    system = await _make_system(tmp_path, io_timeout=30.0)
    await system.store(content="a", memory_type=MemoryType.EPISODIC, memory_id="a")
    await system.store(content="b", memory_type=MemoryType.SEMANTIC, memory_id="b")

    results = await asyncio.gather(system.persist(), system.persist(), return_exceptions=True)
    assert all(r is None for r in results), results
    # The file is valid JSON and holds the full engine state.
    assert _ids_in_file(_path(tmp_path)) == {"a", "b"}
    assert system._ops["persist_ok"] == 2  # noqa: SLF001


async def test_timed_out_persist_cannot_overwrite_later_successful_persist(tmp_path):
    """D-094 (stale-write guard): an abandoned, timed-out persist must never
    erase the result of a NEWER persist that succeeded after it.

    Scenario (deterministic, no sleeps on the critical path):
    1. P1 captures the old state, then its write thread parks BEFORE the
       write lock; P1's io_timeout expires (caller sees TimeoutError).
    2. P2 captures the newer state and writes it successfully.
    3. P1's parked thread is released and runs to completion.

    The file must hold P2's (newer) snapshot afterwards — P1's abandoned
    write may not regress it. A threading.Lock alone only excludes writers;
    it does not order snapshots.
    """
    import threading

    system = await _make_system(tmp_path, io_timeout=0.05)
    await system.store(content="A", memory_type=MemoryType.EPISODIC, memory_id="A")
    await system.persist()
    assert _ids_in_file(_path(tmp_path)) == {"A"}

    real_write = system._write_snapshot_atomic  # noqa: SLF001 — test seam
    parked = threading.Event()  # set once P1's thread is inside the seam
    release = threading.Event()  # the test releases the parked thread
    finished = threading.Event()  # set when the parked call fully returns
    state = {"first": True}

    def _invoke_real(data, seq):
        # Works both pre-fix (signature: (data)) and post-fix ((data, seq)).
        if seq is None:
            return real_write(data)
        return real_write(data, seq)

    def stalling_write(data, seq=None):
        if state["first"]:
            state["first"] = False
            parked.set()
            assert release.wait(10.0), "test never released the parked write"
            try:
                return _invoke_real(data, seq)
            finally:
                finished.set()
        return _invoke_real(data, seq)

    system._write_snapshot_atomic = stalling_write  # noqa: SLF001 — test seam

    # P1: captures {A} only, then parks before the lock; its timeout fires.
    p1 = asyncio.create_task(system._persist_strict())
    await asyncio.to_thread(parked.wait, 10.0)
    with pytest.raises(asyncio.TimeoutError):
        await p1
    assert system._ops["persist_fail"] == 1  # noqa: SLF001

    # P2: newer state {A, B}; the seam no longer stalls; it succeeds.
    await system.store(content="B", memory_type=MemoryType.SEMANTIC, memory_id="B")
    await system._persist_strict()
    assert system._ops["persist_ok"] == 2  # noqa: SLF001 — initial + P2
    assert _ids_in_file(_path(tmp_path)) == {"A", "B"}

    # Release P1's parked thread and let it run to completion.
    release.set()
    assert await asyncio.to_thread(finished.wait, 10.0)
    await asyncio.sleep(0)  # let any to_thread bookkeeping settle

    # THE GUARANTEE: the newer successful snapshot survives P1's abandoned write.
    assert _ids_in_file(_path(tmp_path)) == {"A", "B"}

    # And a fresh engine restores the newer state, not the stale one.
    fresh = MemorySystem(persistence_file=_path(tmp_path), io_timeout=5.0)
    assert await fresh._load_strict() is True  # noqa: SLF001
    assert set(fresh._memories.keys()) == {"A", "B"}  # noqa: SLF001
