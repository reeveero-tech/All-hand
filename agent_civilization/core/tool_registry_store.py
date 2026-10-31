"""R-171 batch 3 (D-113) — a DURABLE tool registry SHARED by every worker
process on the host.

WHY: until D-112 the registry lived in each process's memory. A tool disabled
in one worker kept running in the others, and a restart forgot every state
change together with its audit trail — a governance control that does not
survive a restart or reach the other workers is not a control.

DESIGN (the precedent of ``rate_limit_store`` / ``sse_admission``, D-075):

* one SQLite file per HOST (WAL), outside the source tree by default
  (``AGOS_TOOL_REGISTRY_DB`` overrides); it holds the current records, the
  tombstones, the append-only audit trail and a revision counter;
* ``PersistentToolRegistry`` keeps every rule of ``ToolRegistry`` unchanged
  (it inherits them) and only changes WHERE the state lives:
  - every read first compares the shared revision with the one it holds and
    reloads when another process changed something (one indexed SELECT);
  - every mutation runs inside ``BEGIN IMMEDIATE``: it reloads the latest
    shared state, applies the inherited rule, writes the changed rows, the
    new audit entries (refusals included) and the new revision, and commits.
    A stale process therefore cannot move a tool another process moved (no
    lost update), and the audit ``seq`` stays unique and gap-free;
  - a failed write rolls back AND drops the in-memory state, so memory never
    runs ahead of the store (the next read reloads);
* every stored row carries the SHA-256 digest of its record; a row whose
  content no longer matches its digest is an integrity failure;
* any store failure raises ``RegistryStoreError`` (its message never contains
  the local path). The execution gate turns it into a refusal — fail-closed,
  never "run on stale or empty state".

HONEST limits (declared, D-113): HOST-LOCAL — workers on two hosts do not
share it (multi-host needs a deployment decision: Postgres/Supabase or Redis);
the audit trail grows without rotation; whoever can write the file can change
it (the digest detects edits that do not recompute it, it is not a signature).
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from agent_civilization.core.tool_registry import (
    MUTABLE_FIELDS,
    RegistryError,
    ToolRecord,
    ToolRegistry,
)

ENV_DB = "AGOS_TOOL_REGISTRY_DB"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_records (
    tool_id TEXT NOT NULL,
    version TEXT NOT NULL,
    record_json TEXT NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY (tool_id, version)
);
CREATE TABLE IF NOT EXISTS tool_tombstones (
    tool_id TEXT NOT NULL,
    version TEXT NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY (tool_id, version)
);
CREATE TABLE IF NOT EXISTS tool_audit (
    seq INTEGER PRIMARY KEY,
    entry_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_meta (
    k TEXT PRIMARY KEY,
    v INTEGER NOT NULL
);
"""


class RegistryStoreError(RegistryError):
    """The shared registry store is unavailable or inconsistent. Callers that
    execute tools must refuse (fail-closed). The message never carries the
    local file path. ``reason`` is a fixed vocabulary safe to log:
    ``cannot open`` / ``cannot lock`` / ``read failed`` / ``write failed`` /
    ``integrity``."""

    def __init__(self, message: str, reason: str = "integrity"):
        super().__init__(message)
        self.reason = reason


def default_db_path() -> str:
    """Host-wide default, deliberately OUTSIDE the tracked source tree."""
    return os.environ.get(ENV_DB) or os.path.join(
        tempfile.gettempdir(), "agos_tool_registry.sqlite3")


def _immutable(rec: ToolRecord) -> Dict[str, Any]:
    return rec.model_dump(mode="json", exclude=set(MUTABLE_FIELDS))


def _store_error(what: str, exc: BaseException) -> RegistryStoreError:
    return RegistryStoreError(f"tool registry store unavailable: {what} ({type(exc).__name__})",
                              reason=what)


def _write_changes(conn: sqlite3.Connection,
                   old_records: Dict[Tuple[str, str], ToolRecord],
                   new_records: Dict[Tuple[str, str], ToolRecord],
                   old_removed: Dict[Tuple[str, str], str],
                   new_removed: Dict[Tuple[str, str], str],
                   new_audit: List[Dict[str, Any]]) -> None:
    """Writes the difference between two in-memory states (inside the caller's
    transaction). A module function so a failing write can be simulated."""
    for key, rec in new_records.items():
        if old_records.get(key) is not rec:
            conn.execute("INSERT OR REPLACE INTO tool_records VALUES (?, ?, ?, ?)",
                         (*key, rec.model_dump_json(), rec.digest()))
    for key in old_records.keys() - new_records.keys():
        conn.execute("DELETE FROM tool_records WHERE tool_id = ? AND version = ?", key)
    for key, digest in new_removed.items():
        if old_removed.get(key) != digest:
            conn.execute("INSERT OR REPLACE INTO tool_tombstones VALUES (?, ?, ?)", (*key, digest))
    for key in old_removed.keys() - new_removed.keys():
        conn.execute("DELETE FROM tool_tombstones WHERE tool_id = ? AND version = ?", key)
    for entry in new_audit:
        # plain INSERT: a duplicate seq is a bug and must fail the transaction
        conn.execute("INSERT INTO tool_audit (seq, entry_json) VALUES (?, ?)",
                     (entry["seq"], json.dumps(entry, sort_keys=True, default=str)))


class PersistentToolRegistry(ToolRegistry):
    """``ToolRegistry`` whose state lives in the shared host store."""

    def __init__(self, db_path: str, clock=time.time, busy_timeout_s: float = 5.0,
                 init_retries: int = 20, init_retry_sleep_s: float = 0.1):
        super().__init__(clock=clock)
        self._db_path = db_path
        self._busy_timeout_s = max(0.01, busy_timeout_s)
        self._init_retries = max(1, init_retries)
        self._init_retry_sleep_s = max(0.0, init_retry_sleep_s)
        self._conn: Optional[sqlite3.Connection] = None
        self._conn_pid: Optional[int] = None
        self._rev: Optional[int] = None
        self._in_tx = False
        self._sync()

    # -- connection --------------------------------------------------------
    def _connection(self) -> sqlite3.Connection:
        if self._conn is not None and self._conn_pid == os.getpid():
            return self._conn
        last: Optional[BaseException] = None
        for attempt in range(self._init_retries):
            try:
                conn = sqlite3.connect(self._db_path, timeout=self._busy_timeout_s,
                                       isolation_level=None, check_same_thread=False)
                try:
                    conn.execute(f"PRAGMA busy_timeout = {int(self._busy_timeout_s * 1000)}")
                    conn.execute("PRAGMA journal_mode = WAL")
                    conn.executescript(_SCHEMA)
                except BaseException:
                    conn.close()
                    raise
                self._conn, self._conn_pid = conn, os.getpid()
                return conn
            except sqlite3.OperationalError as e:
                last = e
                if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                    break
                time.sleep(self._init_retry_sleep_s)
            except sqlite3.Error as e:
                last = e
                break
        raise _store_error("cannot open", last or RuntimeError("unknown"))

    def close(self) -> None:
        with self._lock:
            if self._conn is not None and self._conn_pid == os.getpid():
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
            self._conn = None

    def _invalidate(self) -> None:
        """Drop the in-memory copy: the next read reloads from the store."""
        self._records.clear()
        self._removed.clear()
        self._impl_owner.clear()
        self._versions.clear()
        self._audit = []
        self._rev = None

    # -- load --------------------------------------------------------------
    @staticmethod
    def _revision(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT v FROM tool_meta WHERE k = 'revision'").fetchone()
        return int(row[0]) if row else 0

    def _load(self, conn: sqlite3.Connection) -> None:
        rev = self._revision(conn)
        if rev == self._rev:
            return
        records: Dict[Tuple[str, str], ToolRecord] = {}
        for tool_id, version, body, digest in conn.execute(
                "SELECT tool_id, version, record_json, digest FROM tool_records"):
            rec = ToolRecord.model_validate_json(body)
            if (rec.tool_id, rec.version) != (tool_id, version) or rec.digest() != digest:
                raise RegistryStoreError(
                    f"tool registry store integrity: row {tool_id}@{version} does not match "
                    "its digest")
            records[(tool_id, version)] = rec
        removed = {(t, v): d for t, v, d in conn.execute(
            "SELECT tool_id, version, digest FROM tool_tombstones")}
        start = len(self._audit) if self._rev is not None else 0
        audit = list(self._audit) if start else []
        for seq, body in conn.execute(
                "SELECT seq, entry_json FROM tool_audit WHERE seq > ? ORDER BY seq", (start,)):
            if seq != len(audit) + 1:
                raise RegistryStoreError("tool registry store integrity: audit sequence gap")
            audit.append(json.loads(body))
        self._records = records
        self._removed = removed
        self._versions = {}
        self._impl_owner = {}
        for (tool_id, version), rec in sorted(records.items()):
            self._versions.setdefault(tool_id, []).append(version)
            self._impl_owner[rec.implementation_ref] = tool_id
        self._audit = audit
        self._rev = rev

    def _sync(self) -> None:
        if self._in_tx:
            return
        with self._lock:
            try:
                self._load(self._connection())
            except RegistryStoreError:
                self._invalidate()
                raise
            except (sqlite3.Error, ValueError) as e:
                self._invalidate()
                raise _store_error("read failed", e) from None

    # -- transaction -------------------------------------------------------
    def _tx(self, body: Callable[[], Any]) -> Any:
        with self._lock:
            try:
                conn = self._connection()
                conn.execute("BEGIN IMMEDIATE")
            except RegistryStoreError:
                raise
            except sqlite3.Error as e:
                raise _store_error("cannot lock", e) from None
            committed = False
            try:
                self._load(conn)
                old_records, old_removed = dict(self._records), dict(self._removed)
                n_audit = len(self._audit)
                refused: Optional[RegistryError] = None
                result: Any = None
                self._in_tx = True
                try:
                    result = body()
                except RegistryStoreError:
                    raise
                except RegistryError as e:      # a rule refusal: keep its audit entry
                    refused = e
                finally:
                    self._in_tx = False
                new_audit = self._audit[n_audit:]
                if new_audit or old_records != self._records or old_removed != self._removed:
                    _write_changes(conn, old_records, self._records, old_removed,
                                   self._removed, new_audit)
                    conn.execute("INSERT INTO tool_meta (k, v) VALUES ('revision', 1) "
                                 "ON CONFLICT(k) DO UPDATE SET v = v + 1")
                    rev = self._revision(conn)
                else:
                    rev = self._rev
                conn.execute("COMMIT")
                committed = True
                self._rev = rev
            except RegistryStoreError:
                raise
            except (sqlite3.Error, ValueError) as e:
                raise _store_error("write failed", e) from None
            finally:
                if not committed:
                    try:
                        conn.execute("ROLLBACK")
                    except sqlite3.Error:
                        pass
                    self._invalidate()
            if refused is not None:
                raise refused
            return result

    # -- reads: always against the latest shared revision -------------------
    def audit(self) -> List[Dict[str, Any]]:
        self._sync()
        return super().audit()

    def get(self, tool_id: str, version: Optional[str] = None) -> ToolRecord:
        self._sync()
        return super().get(tool_id, version)

    def versions(self, tool_id: str) -> List[str]:
        self._sync()
        return super().versions(tool_id)

    def __len__(self) -> int:
        self._sync()
        return super().__len__()

    def search(self, **kwargs: Any) -> List[ToolRecord]:
        self._sync()
        return super().search(**kwargs)

    # -- mutations: one transaction each -----------------------------------
    def register(self, data: Dict[str, Any], *, actor: str) -> ToolRecord:
        return self._tx(lambda: ToolRegistry.register(self, data, actor=actor))

    def update_status(self, tool_id: str, version: str, *, actor: str,
                      **changes: Any) -> ToolRecord:
        return self._tx(lambda: ToolRegistry.update_status(self, tool_id, version,
                                                           actor=actor, **changes))

    def remove(self, tool_id: str, version: str, *, actor: str) -> None:
        return self._tx(lambda: ToolRegistry.remove(self, tool_id, version, actor=actor))

    def seed(self, records: Iterable[ToolRecord], *, actor: str) -> List[str]:
        """Registers the missing seeds in ONE transaction (concurrent workers
        seed each tool exactly once). The stored record wins over the code:
        a seed whose immutable content differs from the stored version, or
        that cannot be registered (e.g. older than the stored latest), is
        returned as drift — never rewritten. A removed version (tombstone)
        is not resurrected by seeding."""
        recs = list(records)

        def body() -> List[str]:
            drift: List[str] = []
            for rec in recs:
                key = (rec.tool_id, rec.version)
                stored = self._records.get(key)
                if stored is not None:
                    if _immutable(stored) != _immutable(rec):
                        drift.append(f"{rec.tool_id}@{rec.version}")
                    continue
                if key in self._removed:
                    continue
                try:
                    ToolRegistry.register(self, rec, actor=actor)
                except RegistryStoreError:
                    raise
                except RegistryError:
                    drift.append(f"{rec.tool_id}@{rec.version}")
            return drift
        return self._tx(body)


__all__ = ["ENV_DB", "PersistentToolRegistry", "RegistryStoreError", "default_db_path"]
