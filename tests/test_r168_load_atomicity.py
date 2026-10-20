"""R-168 (urgent gap, pre-fix): _load_strict() atomicity — RED test.

A review note claims MemorySystem._load_strict() may insert records into
memory and the indexes BEFORE the whole file is known to be valid: a
JSON-valid file whose LAST record is semantically invalid could leave
partial state behind when the load raises.

This suite was written BEFORE any fix, run on the unmodified tree, and its
result recorded in the audit evidence. It captures a deep before/after
snapshot of the engine (records, both indexes, stats, search/retrieve
results, access counters) and asserts that a FAILED load changes NOTHING.

Corruption cases covered:
- JSON-valid file, valid first record + semantically invalid LAST record:
  bad memory_type / bad timestamp / missing required field / bad tags /
  memory_id key mismatch.
- Corruption in the FIRST and in the MIDDLE record.
- Corrupt JSON itself.
- An engine that ALREADY holds records (two tenants) before the failed
  load — the previous state must survive untouched.
- Success path: a fully valid file rebuilds records and indexes correctly.
"""
import asyncio
import copy
import json
import os
import sys


import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agos_kernel.agent_production.memory_system import (  # noqa: E402
    MemoryQuery,
    MemorySystem,
    MemoryType,
)

VALID_T1 = {
    "memory_id": "mem_tenant1_a",
    "content": "tenant one alpha",
    "memory_type": "episodic",
    "tags": ["t1", "alpha"],
    "metadata": {"tenant_id": "t1"},
    "importance": 0.6,
    "created_at": "2026-01-01T00:00:00",
    "last_accessed": "2026-01-02T00:00:00",
    "access_count": 2,
}

VALID_T2 = {
    "memory_id": "mem_tenant2_b",
    "content": "tenant two beta",
    "memory_type": "semantic",
    "tags": ["t2", "beta"],
    "metadata": {"tenant_id": "t2"},
    "importance": 0.4,
    "created_at": "2026-01-03T00:00:00",
    "last_accessed": "2026-01-04T00:00:00",
    "access_count": 0,
}


def _snapshot(system: MemorySystem) -> dict:
    """A deep, comparable snapshot of engine state (pure reads only)."""
    return {
        "records": {
            mid: {
                "memory_id": e.memory_id,
                "content": e.content,
                "memory_type": e.memory_type,
                "tags": set(e.tags),
                "metadata": copy.deepcopy(e.metadata),
                "importance": e.importance,
                "created_at": e.created_at,
                "last_accessed": e.last_accessed,
                "access_count": e.access_count,
            }
            for mid, e in system._memories.items()
        },
        "index_by_type": {k: set(v) for k, v in system._index_by_type.items()},
        "index_by_tag": {k: set(v) for k, v in system._index_by_tag.items()},
        "stats": system.get_stats(),
    }


async def _search_state(system: MemorySystem) -> list:
    """Pure search results (search never touches entries)."""
    res = await system.search(MemoryQuery(query=None, limit=None))
    return sorted(e.memory_id for e in res)


def _write_file(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


async def _seed_engine(system: MemorySystem):
    """Pre-existing state from two tenants, distinct from the file's records."""
    await system.store(content="old one", memory_type=MemoryType.EPISODIC,
                       tags={"old"}, metadata={"tenant_id": "t1"},
                       importance=0.5, memory_id="mem_old_1")
    await system.store(content="old two", memory_type=MemoryType.PROCEDURAL,
                       tags={"old"}, metadata={"tenant_id": "t2"},
                       importance=0.5, memory_id="mem_old_2")


BAD_LAST_VARIANTS = {
    "invalid_memory_type": {**VALID_T1, "memory_id": "mem_bad",
                            "memory_type": "not_a_type"},
    "invalid_timestamp": {**VALID_T1, "memory_id": "mem_bad",
                          "last_accessed": "not-a-date"},
    "missing_field": {k: v for k, v in VALID_T1.items()
                      if k != "importance"},
    "bad_tags": {**VALID_T1, "memory_id": "mem_bad", "tags": "not-a-list"},
    "id_key_mismatch": {**VALID_T1},  # key mem_bad vs entry mem_tenant1_a
}


@pytest.mark.parametrize("variant", sorted(BAD_LAST_VARIANTS))
def test_load_fails_atomically_bad_last_record(tmp_path, variant):
    """JSON-valid file, valid FIRST record, invalid LAST record: the failed
    load must leave the engine EXACTLY as it was (no partial insert)."""
    async def scenario():
        path = str(tmp_path / "mem.json")
        bad = copy.deepcopy(BAD_LAST_VARIANTS[variant])
        _write_file(path, {VALID_T1["memory_id"]: VALID_T1, "mem_bad": bad})

        system = MemorySystem(max_entries=100, persistence_file=path)
        await _seed_engine(system)
        before = _snapshot(system)
        before_search = await _search_state(system)

        raised = False
        try:
            system._load_strict()
        except Exception:
            raised = True
        assert raised, f"variant {variant}: load unexpectedly succeeded"

        assert _snapshot(system) == before, (
            f"variant {variant}: PARTIAL STATE CHANGE after failed load"
        )
        assert await _search_state(system) == before_search

    asyncio.run(scenario())


def test_load_fails_atomically_bad_first_record(tmp_path):
    """Invalid FIRST record: nothing may be inserted before the raise."""
    async def scenario():
        path = str(tmp_path / "mem.json")
        bad = dict(VALID_T2, memory_type="nope")
        _write_file(path, {"mem_bad": bad, VALID_T2["memory_id"]: VALID_T2})

        system = MemorySystem(max_entries=100, persistence_file=path)
        await _seed_engine(system)
        before = _snapshot(system)

        with pytest.raises(Exception):
            system._load_strict()
        assert _snapshot(system) == before

    asyncio.run(scenario())


def test_load_fails_atomically_bad_middle_record(tmp_path):
    """Invalid MIDDLE record: neither neighbour may be inserted."""
    async def scenario():
        path = str(tmp_path / "mem.json")
        valid3 = dict(VALID_T2, memory_id="mem_tail")
        bad = dict(VALID_T1, memory_id="mem_bad", last_accessed="bad-date")
        _write_file(path, {
            VALID_T1["memory_id"]: VALID_T1,
            "mem_bad": bad,
            "mem_tail": valid3,
        })

        system = MemorySystem(max_entries=100, persistence_file=path)
        await _seed_engine(system)
        before = _snapshot(system)

        with pytest.raises(Exception):
            system._load_strict()
        assert _snapshot(system) == before

    asyncio.run(scenario())


def test_load_fails_atomically_corrupt_json(tmp_path):
    """Corrupt JSON: fail before any state change (already guaranteed by
    json.load placement — pinned here so a refactor cannot regress it)."""
    async def scenario():
        path = str(tmp_path / "mem.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write('{"mem_a": {"memory_id": "mem_a"')  # truncated

        system = MemorySystem(max_entries=100, persistence_file=path)
        await _seed_engine(system)
        before = _snapshot(system)

        with pytest.raises(Exception):
            system._load_strict()
        assert _snapshot(system) == before

    asyncio.run(scenario())


def test_load_success_rebuilds_state_and_indexes(tmp_path):
    """A fully valid file loads everything and rebuilds both indexes."""
    async def scenario():
        path = str(tmp_path / "mem.json")
        _write_file(path, {VALID_T1["memory_id"]: VALID_T1,
                           VALID_T2["memory_id"]: VALID_T2})

        system = MemorySystem(max_entries=100, persistence_file=path)
        assert system._load_strict() is True
        assert set(system._memories) == {VALID_T1["memory_id"],
                                         VALID_T2["memory_id"]}
        assert system._index_by_type[MemoryType.EPISODIC] == {"mem_tenant1_a"}
        assert system._index_by_type[MemoryType.SEMANTIC] == {"mem_tenant2_b"}
        assert system._index_by_tag["t1"] == {"mem_tenant1_a"}
        # access counters preserved from the file
        assert system._memories["mem_tenant1_a"].access_count == 2

    asyncio.run(scenario())


def test_store_level_load_reports_false_and_preserves_state(tmp_path):
    """The FileBackedStore.load() adapter must also leave no partial state."""
    from agos_kernel.agent_production.memory_store import FileBackedStore

    async def scenario():
        path = str(tmp_path / "mem.json")
        bad = dict(VALID_T1, memory_id="mem_bad", memory_type="nope")
        _write_file(path, {VALID_T1["memory_id"]: VALID_T1, "mem_bad": bad})

        system = MemorySystem(max_entries=100, persistence_file=path)
        await _seed_engine(system)
        store = FileBackedStore(system)
        before = _snapshot(system)

        assert (await store.load()) is False
        assert _snapshot(system) == before

    asyncio.run(scenario())
