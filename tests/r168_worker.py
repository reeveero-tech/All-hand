"""R-168 subprocess worker — REAL cross-process restart tests.

The engine state must survive a process that writes and exits completely,
and be readable by a NEW, independent process. This worker is driven by
``tests/test_r168_durability.py`` via ``sys.executable`` — no in-process
object reuse, which is exactly what R-168 requires to prove.

Commands (JSON on stdout, exit 0/1):
  seed <spec.json> <persist.json>   store spec entries, touch them, persist
  verify <persist.json>              NEW process: load + dump canonical state
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agos_kernel.agent_production.memory_system import (  # noqa: E402
    MemoryQuery,
    MemorySystem,
    MemoryType,
)


def canonical(system: MemorySystem) -> dict:
    """A canonical, JSON-serializable snapshot of the loaded engine state."""
    return {
        "records": {
            mid: {
                "memory_id": e.memory_id,
                "content": e.content,
                "memory_type": e.memory_type.value,
                "tags": sorted(e.tags),
                "metadata": e.metadata,
                "importance": e.importance,
                "created_at": e.created_at.isoformat(),
                "last_accessed": e.last_accessed.isoformat(),
                "access_count": e.access_count,
            }
            for mid, e in sorted(system._memories.items())
        },
        "index_by_type": {
            t.value: sorted(ids) for t, ids in system._index_by_type.items() if ids
        },
        "index_by_tag": {t: sorted(ids) for t, ids in system._index_by_tag.items() if ids},
        "total": len(system._memories),
        "search_episodic": sorted(
            e.memory_id for e in asyncio.run(system.search(
                MemoryQuery(memory_type=MemoryType.EPISODIC, limit=None)))
        ),
        "search_tag_alpha": sorted(
            e.memory_id for e in asyncio.run(system.search(
                MemoryQuery(tags={"alpha"}, limit=None)))
        ),
    }


async def seed(spec: dict, persist_path: str) -> None:
    system = MemorySystem(max_entries=spec.get("max_entries", 1000),
                          persistence_file=persist_path)
    for item in spec["entries"]:
        await system.store(
            content=item["content"],
            memory_type=MemoryType(item["memory_type"]),
            tags=set(item["tags"]),
            metadata=item["metadata"],
            importance=item["importance"],
            memory_id=item["memory_id"],
        )
        for _ in range(item.get("touches", 0)):
            await system.retrieve(item["memory_id"])
    await system._persist_strict()


def main() -> int:
    cmd = sys.argv[1]
    if cmd == "seed":
        with open(sys.argv[2], encoding="utf-8") as f:
            spec = json.load(f)
        asyncio.run(seed(spec, sys.argv[3]))
        print(json.dumps({"ok": True}))
        return 0
    if cmd == "verify":
        system = MemorySystem(max_entries=100000, persistence_file=sys.argv[2])
        loaded = asyncio.run(system._load_strict())
        print(json.dumps({"ok": True, "loaded": loaded, "state": canonical(system)},
                         ensure_ascii=False))
        return 0 if loaded else 2
    print(json.dumps({"ok": False, "error": f"unknown command {cmd!r}"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
