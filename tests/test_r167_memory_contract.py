"""R-167: the unified memory contract — one interface for write/search/order/
delete/export/restore with adapters for the existing strategies (process
memory, file-backed), plus retrieve/get and pure iteration.

Red-first: this suite was written BEFORE ``memory_store.py`` existed and run
on the pre-change tree (log 207 — collection failed on the missing module).
It pins the semantics table (D-089):

- write: delegates to MemorySystem.store; caller-provided existing id REPLACES
  the entry and the engine re-indexes (old type/tag index entries removed).
- get: pure lookup (no touch). retrieve: lookup + touch (access bookkeeping).
- search: MemoryQuery (substring content or metadata.prompt; type/tags
  intersection; min_importance; since; metadata_equals exact-match; limit,
  None = explicitly unbounded). Pure — never touches entries.
- ordered: pure listing by importance_recency | recency | created; no touch.
- delete: removes storage + both indexes; False if missing.
- export: PURE and lossless (all fields); nested metadata deep-copied (the
  caller may mutate the export without touching live state); the export's
  max_entries never changes the target's capacity.
- restore: full-payload validation BEFORE any state change (format, entry
  shapes, memory_id key match); existing ids skipped and REPORTED (content
  never overwritten); capacity check rejects atomically (no auto-prune, no
  silent loss); the input payload is never modified; raises ValueError on
  invalid payloads.
- persist/load (file strategy): inspectable bool results; corrupt file ->
  False, no partial state. In-process: persist() is False (no file configured).
- Records only: the export covers memory entries, NOT the LearningRuntime
  state (its own export_knowledge exists separately).

Parity with the old interfaces is proven by: the same contract suite running
against both adapters, write-new/read-old and write-old/read-new, legacy
persist-format compatibility, and the pre-existing experience/SEC-02 suites
staying green (they pin the old behavior exactly).
"""
import asyncio
import copy
import os
import sys
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agos_kernel.agent_production.memory_system import (  # noqa: E402
    MemoryQuery,
    MemorySystem,
    MemoryType,
)

from agent_civilization.core import experience, tenancy  # noqa: E402

STRATEGIES = ["process", "file"]


def _mk_store(strategy, tmp_path, capacity=50):
    from agos_kernel.agent_production.memory_store import FileBackedStore, InProcessStore

    if strategy == "process":
        return InProcessStore(MemorySystem(max_entries=capacity))
    return FileBackedStore(
        MemorySystem(max_entries=capacity, persistence_file=str(tmp_path / "mem.json"))
    )


@pytest.fixture(params=STRATEGIES)
def store(request, tmp_path):
    return _mk_store(request.param, tmp_path)


@pytest.fixture(params=STRATEGIES)
def store_small(request, tmp_path):
    return _mk_store(request.param, tmp_path, capacity=3)


# ---------------------------------------------------------------- contract ops


def test_write_get_retrieve_delete_roundtrip(store):
    async def go():
        mid = await store.write("hello world", MemoryType.EPISODIC, tags={"a"}, metadata={"k": "v"})
        assert isinstance(mid, str) and mid
        # get: pure
        entry = store.get(mid)
        assert entry is not None and entry.content == "hello world"
        assert entry.access_count == 0
        # retrieve: touches
        got = await store.retrieve(mid)
        assert got is not None and got.access_count == 1
        # delete
        assert await store.delete(mid) is True
        assert store.get(mid) is None
        assert await store.delete(mid) is False
        assert await store.retrieve(mid) is None

    asyncio.run(go())


def test_search_substring_content_and_metadata_prompt(store):
    async def go():
        m1 = await store.write("alpha beta", MemoryType.EPISODIC)
        m2 = await store.write("gamma", MemoryType.EPISODIC, metadata={"prompt": "alpha hidden"})
        await store.write("unrelated", MemoryType.EPISODIC)
        hits = await store.search(MemoryQuery(query="alpha", limit=None))
        assert {h.memory_id for h in hits} == {m1, m2}
        assert await store.search(MemoryQuery(query="nope", limit=None)) == []

    asyncio.run(go())


def test_search_filters_and_limit(store):
    async def go():
        m1 = await store.write("one", MemoryType.SEMANTIC, tags={"x"}, importance=0.9)
        m2 = await store.write("two", MemoryType.SEMANTIC, tags={"x", "y"}, importance=0.2)
        m3 = await store.write("three", MemoryType.EPISODIC, tags={"y"})
        # type filter
        assert {h.memory_id for h in await store.search(MemoryQuery(memory_type=MemoryType.EPISODIC, limit=None))} == {m3}
        # tag intersection
        assert {h.memory_id for h in await store.search(MemoryQuery(tags={"x"}, limit=None))} == {m1, m2}
        assert {h.memory_id for h in await store.search(MemoryQuery(tags={"x", "y"}, limit=None))} == {m2}
        # min_importance (m3 keeps the default 0.5, so the bar is 0.6)
        assert {h.memory_id for h in await store.search(MemoryQuery(min_importance=0.6, limit=None))} == {m1}
        # since
        old = datetime.utcnow() - timedelta(hours=1)
        assert await store.search(MemoryQuery(since=old + timedelta(hours=2), limit=None)) == []
        # limit slices
        assert len(await store.search(MemoryQuery(limit=2))) == 2
        # limit=None is explicitly unbounded (needs >10 entries to prove the
        # None is not silently treated as the default 10)
        for i in range(9):
            await store.write(f"bulk{i}", MemoryType.WORKING)
        assert len(await store.search(MemoryQuery(limit=None))) == 12

    asyncio.run(go())


def test_search_metadata_equals_and_positional_compat(store):
    async def go():
        await store.write("a", MemoryType.EPISODIC, metadata={"layer": "experience", "tenant_id": "t1"})
        await store.write("b", MemoryType.EPISODIC, metadata={"layer": "experience"})
        await store.write("c", MemoryType.EPISODIC, metadata={"other": None})
        # exact match: only the two entries carrying layer=experience
        hits = await store.search(MemoryQuery(metadata_equals={"layer": "experience"}, limit=None))
        assert len(hits) == 2 and all(h.metadata.get("layer") == "experience" for h in hits)
        # key present with value None matches ONLY present-with-None, never missing
        none_hits = await store.search(MemoryQuery(metadata_equals={"other": None}, limit=None))
        assert len(none_hits) == 1
        assert await store.search(MemoryQuery(metadata_equals={"missing": None}, limit=None)) == []
        # positional construction still works (metadata_equals is the LAST field)
        q = MemoryQuery("a", MemoryType.EPISODIC, {"x"}, 0.0, None, 5)
        assert q.limit == 5 and q.metadata_equals is None

    asyncio.run(go())


def test_ordered_is_pure_and_orders(store):
    async def go():
        m1 = await store.write("low", MemoryType.EPISODIC, importance=0.1)
        m2 = await store.write("high", MemoryType.EPISODIC, importance=0.9)
        m3 = await store.write("mid", MemoryType.SEMANTIC, importance=0.5)
        before = {m: (store.get(m).access_count, store.get(m).last_accessed) for m in (m1, m2, m3)}
        # importance+recency (same key as engine search)
        by_imp = await store.ordered()
        assert [e.memory_id for e in by_imp][:2] == [m2, m3]
        # recency only
        by_rec = await store.ordered(order="recency")
        assert set(e.memory_id for e in by_rec) == {m1, m2, m3}
        # created (insertion order)
        by_created = await store.ordered(order="created")
        assert [e.memory_id for e in by_created] == [m1, m2, m3]
        # type filter + limit, still pure
        sem = await store.ordered(memory_type=MemoryType.SEMANTIC)
        assert [e.memory_id for e in sem] == [m3]
        assert {m: (store.get(m).access_count, store.get(m).last_accessed) for m in (m1, m2, m3)} == before

    asyncio.run(go())


def test_export_lossless_and_pure(store):
    async def go():
        mid = await store.write(
            "export me", MemoryType.EPISODIC, tags={"t1", "t2"},
            metadata={"layer": "experience", "tenant_id": "tA", "words": ["a", "b"]},
            importance=0.77,
        )
        await store.retrieve(mid)  # access_count -> 1
        snap = await store.export()
        assert snap["format"] == "agos-memory/v1"
        assert snap["entries"][mid]["importance"] == 0.77
        assert set(snap["entries"][mid]["tags"]) == {"t1", "t2"}
        assert snap["entries"][mid]["metadata"]["tenant_id"] == "tA"
        assert snap["entries"][mid]["access_count"] == 1
        # pure: export does not change access bookkeeping
        assert store.get(mid).access_count == 1
        # nested mutation of the export must not touch live state
        snap["entries"][mid]["metadata"]["tenant_id"] = "MUTATED"
        snap["entries"][mid]["importance"] = 99.0
        assert store.get(mid).metadata["tenant_id"] == "tA"
        assert store.get(mid).importance == 0.77

    asyncio.run(go())


def test_restore_semantics(store, tmp_path):
    async def go():
        mid = await store.write("keep", MemoryType.EPISODIC, importance=0.5)
        payload = await store.export()
        payload = copy.deepcopy(payload)  # restore must not modify its input
        # new ids in the payload
        payload["entries"]["new_1"] = {
            "memory_id": "new_1", "content": "restored", "memory_type": "episodic",
            "tags": ["z"], "metadata": {"tenant_id": "tB"}, "importance": 0.4,
            "created_at": "2026-01-01T00:00:00", "last_accessed": "2026-01-01T00:00:00",
            "access_count": 0,
        }
        payload_orig = copy.deepcopy(payload)  # captured AFTER building the input
        result = await store.restore(payload)
        assert result["restored"] == 1
        assert result["skipped_existing"] == [mid]
        # existing content NOT overwritten (no "keep" -> "restored" replacement)
        assert store.get(mid).content == "keep"
        # new entry lossless with restored types
        e = store.get("new_1")
        assert e.memory_type is MemoryType.EPISODIC
        assert e.tags == {"z"}
        assert isinstance(e.created_at, datetime)
        assert e.metadata["tenant_id"] == "tB"
        # the input payload is untouched
        assert payload == payload_orig

    asyncio.run(go())


def test_restore_capacity_rejected_atomically(store_small):
    async def go():
        for i in range(2):
            await store_small.write(f"live{i}", MemoryType.EPISODIC)
        payload = {"format": "agos-memory/v1", "max_entries": 999, "entries": {}}
        for i in range(5):
            payload["entries"][f"x{i}"] = {
                "memory_id": f"x{i}", "content": f"c{i}", "memory_type": "episodic",
                "tags": [], "metadata": {}, "importance": 0.5,
                "created_at": "2026-01-01T00:00:00", "last_accessed": "2026-01-01T00:00:00",
                "access_count": 0,
            }
        before = len(store_small.entries())
        with pytest.raises(ValueError):
            await store_small.restore(payload)
        # atomic rejection: no partial state
        assert len(store_small.entries()) == before
        assert store_small.get("x0") is None

    asyncio.run(go())


def test_restore_invalid_payloads_rejected(store):
    async def go():
        # not a dict
        with pytest.raises(ValueError):
            await store.restore(["nope"])
        # wrong format marker
        with pytest.raises(ValueError):
            await store.restore({"format": "other/v9", "entries": {}})
        # entry key mismatch
        with pytest.raises(ValueError):
            await store.restore({"format": "agos-memory/v1", "entries": {
                "a": {"memory_id": "b", "content": "x", "memory_type": "episodic",
                      "tags": [], "metadata": {}, "importance": 0.5,
                      "created_at": "2026-01-01T00:00:00", "last_accessed": "2026-01-01T00:00:00",
                      "access_count": 0}}})
        # bad memory_type
        with pytest.raises(ValueError):
            await store.restore({"format": "agos-memory/v1", "entries": {
                "a": {"memory_id": "a", "content": "x", "memory_type": "bogus",
                      "tags": [], "metadata": {}, "importance": 0.5,
                      "created_at": "2026-01-01T00:00:00", "last_accessed": "2026-01-01T00:00:00",
                      "access_count": 0}}})
        # bad datetime
        with pytest.raises(ValueError):
            await store.restore({"format": "agos-memory/v1", "entries": {
                "a": {"memory_id": "a", "content": "x", "memory_type": "episodic",
                      "tags": [], "metadata": {}, "importance": 0.5,
                      "created_at": "not-a-date", "last_accessed": "2026-01-01T00:00:00",
                      "access_count": 0}}})
        # missing required field
        with pytest.raises(ValueError):
            await store.restore({"format": "agos-memory/v1", "entries": {
                "a": {"memory_id": "a", "content": "x", "memory_type": "episodic",
                      "tags": [], "metadata": {}, "importance": 0.5,
                      "access_count": 0}}})

    asyncio.run(go())


def test_restore_does_not_adopt_export_capacity(store_small):
    async def go():
        payload = {"format": "agos-memory/v1", "max_entries": 99999, "entries": {
            "a": {"memory_id": "a", "content": "x", "memory_type": "episodic",
                  "tags": [], "metadata": {}, "importance": 0.5,
                  "created_at": "2026-01-01T00:00:00", "last_accessed": "2026-01-01T00:00:00",
                  "access_count": 0}}}
        await store_small.restore(payload)
        assert store_small.capacity == 3  # unchanged by the payload's max_entries

    asyncio.run(go())


def test_overwrite_reindexes(store):
    async def go():
        await store.write("first", MemoryType.EPISODIC, tags={"old"}, importance=0.5, memory_id="fixed")
        # replace with a different type and tags
        await store.write("second", MemoryType.SEMANTIC, tags={"new"}, importance=0.8, memory_id="fixed")
        # old filters must MISS
        assert {h.memory_id for h in await store.search(MemoryQuery(memory_type=MemoryType.EPISODIC, limit=None))} == set()
        assert await store.search(MemoryQuery(tags={"old"}, limit=None)) == []
        # new filters must FIND
        assert {h.memory_id for h in await store.search(MemoryQuery(memory_type=MemoryType.SEMANTIC, limit=None))} == {"fixed"}
        assert {h.memory_id for h in await store.search(MemoryQuery(tags={"new"}, limit=None))} == {"fixed"}
        # delete leaves no stale index references
        assert await store.delete("fixed") is True
        stats = store.stats()
        total_by_indexes = sum(stats["by_type"].values())
        assert total_by_indexes == stats["total_memories"] == 0

    asyncio.run(go())


def test_write_capacity_prune_documented(store_small):
    async def go():
        # write beyond capacity: the least important/oldest are dropped (the
        # documented write-path policy; restore REJECTS instead — see above)
        for i in range(5):
            await store_small.write(f"e{i}", MemoryType.EPISODIC, importance=0.1 + i * 0.1)
        assert len(store_small.entries()) == 3
        kept = {e.content for e in store_small.entries()}
        assert kept == {"e2", "e3", "e4"}  # highest importance survive

    asyncio.run(go())


# ---------------------------------------------------------------- persist/load


def test_file_persist_load_roundtrip(tmp_path):
    from agos_kernel.agent_production.memory_store import FileBackedStore

    async def go():
        s1 = FileBackedStore(MemorySystem(max_entries=50, persistence_file=str(tmp_path / "m.json")))
        await s1.write("durable", MemoryType.EPISODIC, tags={"t"}, metadata={"layer": "experience"})
        assert await s1.persist() is True
        s2 = FileBackedStore(MemorySystem(max_entries=50, persistence_file=str(tmp_path / "m.json")))
        assert await s2.load() is True
        entries = s2.entries()
        assert len(entries) == 1
        e = entries[0]
        assert e is not None and e.content == "durable"
        assert e.memory_type is MemoryType.EPISODIC and e.tags == {"t"}

    asyncio.run(go())


def test_file_persist_missing_and_corrupt(tmp_path):
    from agos_kernel.agent_production.memory_store import FileBackedStore

    async def go():
        # no file yet: load is False (inspectable), no crash, no partial state
        s = FileBackedStore(MemorySystem(max_entries=5, persistence_file=str(tmp_path / "none.json")))
        assert await s.load() is False
        # corrupt file: load is False, state stays empty (no partial load)
        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        s2 = FileBackedStore(MemorySystem(max_entries=5, persistence_file=str(bad)))
        assert await s2.load() is False
        assert s2.entries() == []

    asyncio.run(go())


def test_in_process_persist_is_false(tmp_path):
    from agos_kernel.agent_production.memory_store import InProcessStore

    async def go():
        s = InProcessStore(MemorySystem(max_entries=5))
        await s.write("volatile", MemoryType.EPISODIC)
        assert await s.persist() is False  # nothing to persist: no file configured

    asyncio.run(go())


def test_store_for_selects_strategy(tmp_path):
    from agos_kernel.agent_production.memory_store import FileBackedStore, InProcessStore, store_for

    volatile = store_for(MemorySystem())
    assert isinstance(volatile, InProcessStore)
    backed = store_for(MemorySystem(persistence_file=str(tmp_path / "m.json")))
    assert isinstance(backed, FileBackedStore)


# ---------------------------------------------------------------- parity


def test_parity_contract_and_engine(tmp_path):
    async def go():
        engine = MemorySystem(max_entries=50)
        from agos_kernel.agent_production.memory_store import InProcessStore

        contract = InProcessStore(engine)
        # write old, read new
        mid = await engine.store("old path", MemoryType.EPISODIC, tags={"p"}, metadata={"prompt": "parity"})
        hits = await contract.search(MemoryQuery(query="parity", limit=None))
        assert [h.memory_id for h in hits] == [mid]
        # write new, read old
        mid2 = await contract.write("new path", MemoryType.SEMANTIC, tags={"q"})
        engine_hits = await engine.search(MemoryQuery(tags={"q"}, limit=None))
        assert [h.memory_id for h in engine_hits] == [mid2]
        # identical results for identical queries
        q = MemoryQuery(min_importance=0.0, limit=None)
        assert [e.memory_id for e in await contract.search(q)] == [e.memory_id for e in await engine.search(q)]

    asyncio.run(go())


def test_restore_accepts_legacy_persist_format(store):
    async def go():
        # the engine persist file is {id: to_dict()} with no envelope; restore
        # accepts it so existing files remain usable
        legacy = {
            "legacy1": {
                "memory_id": "legacy1", "content": "from the old file", "memory_type": "episodic",
                "tags": ["old"], "metadata": {"tenant_id": "tL"}, "importance": 0.6,
                "created_at": "2026-01-01T00:00:00", "last_accessed": "2026-01-01T00:00:00",
                "access_count": 3,
            }
        }
        result = await store.restore(legacy)
        assert result["restored"] == 1
        e = store.get("legacy1")
        assert e.access_count == 3 and e.metadata["tenant_id"] == "tL"

    asyncio.run(go())


# ---------------------------------------------------------------- SEC-02


def _tenant_entry(mid, tenant, content, importance=0.5):
    return {
        "memory_id": mid, "content": f"{tenant}: {content}",
        "memory_type": "episodic", "tags": [f"tenant:{tenant}"],
        "metadata": {"layer": "experience", "tenant_id": tenant, "specialization": "analyst",
                     "prompt": content, "answer": "a", "ok": True, "input_kind": "text",
                     "words": sorted(content.split())},
        "importance": importance,
        "created_at": "2026-01-01T00:00:00", "last_accessed": "2026-01-01T00:00:00",
        "access_count": 0,
    }


def test_multitenant_snapshot_scoping(tmp_path):
    async def go():
        from agos_kernel.agent_production.memory_store import InProcessStore

        store = InProcessStore(MemorySystem(max_entries=50))
        payload = {"format": "agos-memory/v1", "entries": {
            "t1a": _tenant_entry("t1a", "t1", "alpha beta"),
            "t1b": _tenant_entry("t1b", "t1", "gamma"),
            "t2a": _tenant_entry("t2a", "t2", "alpha beta", importance=0.99),
        }}
        await store.restore(payload)
        # tenant fields survive the roundtrip
        assert store.get("t2a").metadata["tenant_id"] == "t2"
        # experience scoping: t1 never sees t2, even when t2's entry outranks it
        token = tenancy.set_tenant("t1")
        try:
            experience.reset()
            # wire the experience layer's memory to this store's engine
            experience._memory = store.system
            hits = await experience.search("alpha beta", scope="t1", limit=1)
            assert len(hits) == 1 and hits[0]["memory_id"] in {"t1a", "t1b"}
            ids = {h["memory_id"] for h in await experience.search("", scope="t1")}
            assert ids == {"t1a", "t1b"}
            # forget_tenant removes only t1's entries
            removed = await experience.forget_tenant("t1")
            assert removed == 2
            assert store.get("t2a") is not None
            assert store.get("t1a") is None
        finally:
            tenancy.reset_tenant(token)
            experience.reset()

    asyncio.run(go())


# ---------------------------------------------------------------- real path


class _CountingStore:
    """A traceable wrapper proving the real paths go through the contract."""

    def __init__(self, inner):
        self.inner = inner
        self.calls = {}

    def _mark(self, op):
        self.calls[op] = self.calls.get(op, 0) + 1

    async def write(self, *a, **kw):
        self._mark("write")
        return await self.inner.write(*a, **kw)

    async def search(self, *a, **kw):
        self._mark("search")
        return await self.inner.search(*a, **kw)

    async def delete(self, *a, **kw):
        self._mark("delete")
        return await self.inner.delete(*a, **kw)

    def entries(self):
        self._mark("entries")
        return self.inner.entries()

    def __getattr__(self, name):
        # everything the real paths need but we do not count (capacity,
        # persistence_path, system, get, ...) delegates to the inner store
        return getattr(self.inner, name)


def test_run_with_experience_uses_the_contract():
    from agos_kernel.agent_production.memory_store import store_for

    async def go():
        experience.reset()
        counting = _CountingStore(store_for(experience.memory()))
        original = experience.mem_store
        experience.mem_store = lambda: counting
        token = tenancy.set_tenant("r167_t")
        try:
            async def call():
                return {"ok": True, "answer": "done"}

            result, exp = await experience.run_with_experience(
                "analyst", "t", {"prompt": "unified memory contract test"}, call)
            assert result["ok"] is True
            # write + search (recall) went through the contract
            assert counting.calls.get("write", 0) == 1
            assert counting.calls.get("search", 0) >= 1
            # learning recorded exactly once for this task
            learn = experience.learning("r167_t")
            assert learn._global_patterns or exp["learning"]["after"] is not None
            # the entry is retrievable through the contract and the engine alike
            st = store_for(experience.memory())
            found = await st.search(MemoryQuery(metadata_equals={"layer": "experience"}, limit=None))
            assert any(e.metadata.get("tenant_id") == "r167_t" for e in found)
        finally:
            tenancy.reset_tenant(token)
            experience.mem_store = original
            experience.reset()

    asyncio.run(go())


def test_reset_does_not_leave_a_stale_store():
    from agos_kernel.agent_production.memory_store import store_for

    async def go():
        experience.reset()
        first = experience.memory()
        s1 = store_for(experience.memory())
        mid = await s1.write("before reset", MemoryType.EPISODIC)
        experience.reset()  # drops the singleton
        second = experience.memory()
        assert first is not second
        s2 = store_for(experience.memory())
        assert s2.system is not first  # the store never points at the dropped system
        assert s2.get(mid) is None

    asyncio.run(go())


def test_dispatcher_failed_experience_selection_via_contract():
    # the self_developer retest path selects remembered failures; the same
    # selection must be reproducible through the public contract ops
    from agos_kernel.agent_production.memory_store import store_for

    async def go():
        experience.reset()
        token = tenancy.set_tenant("r167_d")
        try:
            async def fail():
                return {"ok": False, "error": "boom"}

            await experience.run_with_experience(
                "analyst", "t", {"prompt": "g1", "goal": "g1"}, fail)
            await experience.run_with_experience(
                "analyst", "t", {"prompt": "g2", "goal": "g2"}, fail)
            st = store_for(experience.memory())
            failed = [e for e in st.entries()
                      if e.metadata.get("tenant_id") == "r167_d"
                      and e.metadata.get("layer") == "experience" and not e.metadata.get("ok")
                      and e.metadata.get("specialization") != "self_developer" and e.metadata.get("goal")]
            assert {e.metadata["goal"] for e in failed} == {"g1", "g2"}
        finally:
            tenancy.reset_tenant(token)
            experience.reset()

    asyncio.run(go())
