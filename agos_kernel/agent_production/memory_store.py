"""R-167: the unified memory contract — one interface for write/search/order/
delete/export/restore over the existing entry model, with adapters for the
existing strategies (process memory, file-backed).

Design rules (D-089):

- One data model: ``MemoryEntry``/``MemoryQuery`` (extended with
  ``metadata_equals`` as the LAST field — positional calls stay compatible;
  ``limit=None`` means explicitly unbounded).
- One state source: every adapter delegates to the SAME ``MemorySystem`` it
  wraps. No second singleton is created; the experience layer passes its own
  engine.
- The file strategy is an explicit snapshot (persist/load on demand), NOT
  write-through — this mirrors the current architecture exactly.
- export() is pure and lossless (deep-copies nested metadata); restore()
  validates the full payload BEFORE any state change, skips (and reports)
  existing ids without overwriting, and rejects — atomically, with no
  auto-prune and no silent loss — anything the capacity cannot hold.
- The export covers memory RECORDS only; the LearningRuntime state has its own
  ``export_knowledge`` and is not part of this contract.

Declared limits (R-167, not R-168): restart/corruption/concurrency/100k+
durability guarantees are R-168's scope. Here: corrupt files fail closed with
an inspectable False, no partial state.
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from .memory_system import MemoryEntry, MemoryQuery, MemorySystem, MemoryType

logger = logging.getLogger(__name__)

EXPORT_FORMAT = "agos-memory/v1"

_ORDERINGS = ("importance_recency", "recency", "created")


class MemorySystemStore:
    """The contract over one ``MemorySystem`` (the shared engine).

    This base implements every operation by delegation; the strategy
    subclasses below only differ in persistence behavior.
    """

    def __init__(self, system: MemorySystem):
        self._system = system

    # ------------------------------------------------------------- properties
    @property
    def system(self) -> MemorySystem:
        """The wrapped engine (the single state source)."""
        return self._system

    @property
    def capacity(self) -> int:
        return self._system._max_entries

    @property
    def persistence_path(self) -> Optional[str]:
        return self._system._persistence_file

    def stats(self) -> Dict[str, Any]:
        return self._system.get_stats()

    # ------------------------------------------------------------- pure reads
    def entries(self) -> List[MemoryEntry]:
        """Pure iteration over all entries, in insertion order (no touch)."""
        return list(self._system._memories.values())

    def get(self, memory_id: str) -> Optional[MemoryEntry]:
        """Pure lookup by id — NO touch (unlike ``retrieve``)."""
        return self._system._memories.get(memory_id)

    # ------------------------------------------------------------ contract ops
    async def write(
        self,
        content: str,
        memory_type: MemoryType,
        tags: Optional[Set[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        importance: float = 0.5,
        memory_id: Optional[str] = None,
    ) -> str:
        """Store an entry (delegates to MemorySystem.store; replace-on-same-id
        re-indexes — the engine removes the old type/tag index entries)."""
        return await self._system.store(
            content=content, memory_type=memory_type, tags=tags,
            metadata=metadata, importance=importance, memory_id=memory_id,
        )

    async def retrieve(self, memory_id: str) -> Optional[MemoryEntry]:
        """Lookup + touch (access bookkeeping), like MemorySystem.retrieve."""
        return await self._system.retrieve(memory_id)

    async def search(self, query: MemoryQuery) -> List[MemoryEntry]:
        """Pure filtered search (delegates to MemorySystem.search)."""
        return await self._system.search(query)

    async def ordered(
        self,
        memory_type: Optional[MemoryType] = None,
        order: str = "importance_recency",
        limit: Optional[int] = None,
    ) -> List[MemoryEntry]:
        """Pure listing in the requested order. No touch, no filters beyond
        the optional type. ``created`` is insertion order (stable for replay).
        """
        if order not in _ORDERINGS:
            raise ValueError(f"unknown order {order!r} (use one of {_ORDERINGS})")
        out = [e for e in self._system._memories.values()
               if memory_type is None or e.memory_type == memory_type]
        if order == "importance_recency":
            out.sort(key=lambda m: (m.importance, m.last_accessed.timestamp()), reverse=True)
        elif order == "recency":
            out.sort(key=lambda m: m.last_accessed.timestamp(), reverse=True)
        else:  # created — insertion order is already created-ascending
            pass
        return out if limit is None else out[:limit]

    async def delete(self, memory_id: str) -> bool:
        """Remove storage + both indexes; False if missing."""
        return await self._system.forget(memory_id)

    async def export(self) -> Dict[str, Any]:
        """A pure, lossless, portable snapshot of every record.

        The nested metadata is deep-copied: mutating the export never touches
        live state, and export never changes access bookkeeping.
        """
        return {
            "format": EXPORT_FORMAT,
            "max_entries": self.capacity,
            "entries": {
                mid: {**e.to_dict(), "metadata": copy.deepcopy(e.metadata),
                      "tags": list(e.tags)}
                for mid, e in self._system._memories.items()
            },
        }

    async def restore(self, data: Any) -> Dict[str, Any]:
        """Import records with NO loss and NO silent overwrite.

        Semantics (D-089):
        - The FULL payload is validated BEFORE any state change (envelope or
          legacy persist format; entry shapes; memory_id key match; types).
        - Existing ids are SKIPPED and reported (their content is never
          overwritten); the result reports both counts and the skipped ids.
        - If the entries that would be added exceed the capacity, the whole
          operation is REJECTED with ValueError and NOTHING changes (no
          auto-prune, no silent loss).
        - The input payload is never modified (entries are copied on insert).
        - The payload's ``max_entries`` never changes this store's capacity.
        """
        entries = self._validate_payload(data)
        new_ids = [mid for mid in entries if mid not in self._system._memories]
        if len(self._system._memories) + len(new_ids) > self.capacity:
            raise ValueError(
                f"restore rejected: {len(new_ids)} new entries would exceed the "
                f"capacity {self.capacity} (current {len(self._system._memories)}); "
                "nothing was changed"
            )
        skipped = []
        for mid, entry in entries.items():
            if mid in self._system._memories:
                skipped.append(mid)
                continue
            self._insert(entry)
        return {"restored": len(new_ids), "skipped_existing": skipped,
                "total": len(self._system._memories)}

    # ------------------------------------------------------------ persistence
    async def persist(self) -> bool:
        """Snapshot to the persistence file. True iff a file was written."""
        raise NotImplementedError  # strategy-specific

    async def load(self) -> bool:
        """Load from the persistence file. True iff a file was read."""
        raise NotImplementedError  # strategy-specific

    # ------------------------------------------------------------ internals
    def _validate_payload(self, data: Any) -> Dict[str, MemoryEntry]:
        if not isinstance(data, dict):
            raise ValueError("restore payload must be a dict")
        if "entries" in data and "format" in data:
            if data.get("format") != EXPORT_FORMAT:
                raise ValueError(f"unsupported export format {data.get('format')!r}")
            raw = data["entries"]
        else:
            raw = data  # legacy engine persist format: {id: to_dict()}
        if not isinstance(raw, dict):
            raise ValueError("restore entries must be a dict")
        entries: Dict[str, MemoryEntry] = {}
        for mid, item in raw.items():
            entry = self._validate_entry(mid, item)
            entries[mid] = entry
        return entries

    def _validate_entry(self, mid: str, item: Any) -> MemoryEntry:
        required = {"memory_id", "content", "memory_type", "tags", "metadata",
                    "importance", "created_at", "last_accessed", "access_count"}
        if not isinstance(item, dict):
            raise ValueError(f"entry {mid!r} must be a dict")
        missing = required - set(item)
        if missing:
            raise ValueError(f"entry {mid!r} is missing fields: {sorted(missing)}")
        if item["memory_id"] != mid:
            raise ValueError(f"entry key {mid!r} does not match memory_id {item['memory_id']!r}")
        try:
            mtype = MemoryType(item["memory_type"])
        except ValueError as exc:
            raise ValueError(f"entry {mid!r} has an invalid memory_type") from exc
        if not isinstance(item["tags"], (list, set)):
            raise ValueError(f"entry {mid!r} tags must be a list/set")
        if not isinstance(item["metadata"], dict):
            raise ValueError(f"entry {mid!r} metadata must be a dict")
        try:
            created = item["created_at"] if isinstance(item["created_at"], datetime) \
                else datetime.fromisoformat(str(item["created_at"]))
            accessed = item["last_accessed"] if isinstance(item["last_accessed"], datetime) \
                else datetime.fromisoformat(str(item["last_accessed"]))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"entry {mid!r} has an invalid timestamp") from exc
        return MemoryEntry(
            memory_id=mid,
            content=str(item["content"]),
            memory_type=mtype,
            tags=set(item["tags"]),
            metadata=copy.deepcopy(item["metadata"]),  # never alias the input
            importance=float(item["importance"]),
            created_at=created,
            last_accessed=accessed,
            access_count=int(item["access_count"]),
        )

    def _insert(self, entry: MemoryEntry) -> None:
        mid = entry.memory_id
        self._system._memories[mid] = entry
        self._system._index_by_type[entry.memory_type].add(mid)
        for tag in entry.tags:
            self._system._index_by_tag[tag].add(mid)


class InProcessStore(MemorySystemStore):
    """The volatile strategy: everything lives and dies with the process."""

    async def persist(self) -> bool:
        return False  # nothing to persist: no file configured

    async def load(self) -> bool:
        return False


class FileBackedStore(MemorySystemStore):
    """The file strategy: the SAME engine plus an explicit JSON snapshot.

    Writes stay in-memory (the snapshot model — persistence is an explicit
    ``persist()``, mirroring the pre-contract architecture); ``persist``/``load``
    report inspectable bools and fail closed on corruption (no partial state).
    """

    def __init__(self, system: MemorySystem):
        if not system._persistence_file:
            raise ValueError("FileBackedStore requires a MemorySystem with a persistence_file")
        super().__init__(system)

    async def persist(self) -> bool:
        try:
            self._system._persist_strict()
            return True
        except Exception as exc:  # noqa: BLE001 — logged, inspectable False
            logger.error("memory persist failed: %s", type(exc).__name__)
            return False

    async def load(self) -> bool:
        try:
            return self._system._load_strict()
        except Exception as exc:  # noqa: BLE001 — logged, inspectable False
            logger.error("memory load failed: %s", type(exc).__name__)
            return False


def store_for(system: MemorySystem) -> MemorySystemStore:
    """Pick the adapter for an existing engine by its configuration."""
    if system._persistence_file:
        return FileBackedStore(system)
    return InProcessStore(system)
