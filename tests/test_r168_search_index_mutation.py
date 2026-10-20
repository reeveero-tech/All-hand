"""R-168: search() must never mutate the live tag index (RED-first bug).

A search with TWO tags destructively corrupted the engine: the tag-filter
loop aliased the first-iterated tag's live index set and intersected it IN
PLACE (``tag_results = tag_set`` then ``tag_results &= tag_set`` mutates the
aliased ``_index_by_tag[first_tag]``), so every record that carried the
first tag but not the second was silently removed from that tag's index.

Which tag iterates first is set-order (hash-based, per-process random), so
the tests below are built so that BOTH tags carry exclusive records: on the
buggy code, whichever tag iterates first loses its exclusive record, so at
least one post-search assertion always fails — deterministic in catching the
bug regardless of iteration order.

Production hit this constantly: every ``run_with_experience`` recalls with
tags {spec:<spec>, tenant:<tid>} BEFORE storing, so a tenant's first recall
intersected the spec index with its (still empty) tenant index and emptied
the spec entry — later recalls for OTHER tenants then found nothing.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agos_kernel.agent_production.memory_system import (  # noqa: E402
    MemoryQuery,
    MemorySystem,
    MemoryType,
)


async def _seed(system: MemorySystem):
    # every tag carries an exclusive record plus one shared record
    await system.store(content="A only", memory_type=MemoryType.EPISODIC,
                       tags={"A"}, memory_id="a1")
    await system.store(content="B only", memory_type=MemoryType.EPISODIC,
                       tags={"B"}, memory_id="b1")
    await system.store(content="A and B", memory_type=MemoryType.EPISODIC,
                       tags={"A", "B"}, memory_id="ab1")


def test_two_tag_search_does_not_destroy_tag_indexes():
    """A search for tags {A, B} must not remove any record from either
    tag's live index: afterwards, searching {A} alone must find a1+ab1 and
    searching {B} alone must find b1+ab1."""
    async def scenario():
        system = MemorySystem()
        await _seed(system)

        # sanity: each tag alone finds its records
        hits = await system.search(MemoryQuery(tags={"A"}, limit=None))
        assert sorted(e.memory_id for e in hits) == ["a1", "ab1"]
        hits = await system.search(MemoryQuery(tags={"B"}, limit=None))
        assert sorted(e.memory_id for e in hits) == ["ab1", "b1"]

        # the filtered search: only the shared record matches
        hits = await system.search(MemoryQuery(tags={"A", "B"}, limit=None))
        assert sorted(e.memory_id for e in hits) == ["ab1"]

        # THE BUG: the first-iterated tag's live index was intersected in
        # place, dropping its exclusive record. Both post-conditions must
        # hold — on the buggy code at least one of them fails no matter
        # which tag iterated first.
        hits = await system.search(MemoryQuery(tags={"A"}, limit=None))
        assert sorted(e.memory_id for e in hits) == ["a1", "ab1"], (
            "search(tags={A,B}) destructively mutated the live index of tag A"
        )
        hits = await system.search(MemoryQuery(tags={"B"}, limit=None))
        assert sorted(e.memory_id for e in hits) == ["ab1", "b1"], (
            "search(tags={A,B}) destructively mutated the live index of tag B"
        )
        # the live index sets themselves must be intact
        assert system._index_by_tag["A"] == {"a1", "ab1"}
        assert system._index_by_tag["B"] == {"b1", "ab1"}

    asyncio.run(scenario())


def test_tenant_recall_sequence_keeps_spec_index_intact():
    """The production sequence: recall for a tenant happens BEFORE that
    tenant's first store (run_with_experience recalls first). A tenant's
    empty first recall must not damage the spec index for the tenants that
    follow."""
    async def scenario():
        system = MemorySystem()
        # t1 stores an experience-style record
        await system.store(content="t1 work", memory_type=MemoryType.EPISODIC,
                           tags={"spec:web", "tenant:t1"}, memory_id="t1a")
        # t2's FIRST interaction: recall before any t2 record exists
        hits = await system.search(MemoryQuery(
            tags={"spec:web", "tenant:t2"}, limit=None))
        assert hits == []
        # t2 stores its record
        await system.store(content="t2 work", memory_type=MemoryType.EPISODIC,
                           tags={"spec:web", "tenant:t2"}, memory_id="t2a")

        # both tenants must still find their records via the spec+tenant
        # intersection, and the live spec index must hold both records
        assert system._index_by_tag["spec:web"] == {"t1a", "t2a"}
        hits = await system.search(MemoryQuery(
            tags={"spec:web", "tenant:t1"}, limit=None))
        assert sorted(e.memory_id for e in hits) == ["t1a"]
        hits = await system.search(MemoryQuery(
            tags={"spec:web", "tenant:t2"}, limit=None))
        assert sorted(e.memory_id for e in hits) == ["t2a"]

    asyncio.run(scenario())
