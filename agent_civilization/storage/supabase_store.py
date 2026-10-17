#!/usr/bin/env python3
"""
Supabase storage layer for the Agent Civilization.

Persists tasks, agent stats, and events to the Supabase database.
Uses the REST API (no external SDK needed) via aiohttp.
"""

import base64
import functools
from datetime import datetime, timezone
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

import aiohttp

from agent_civilization.core import telemetry
from agent_civilization.core.tenancy import ALL_TENANTS, check_scope

logger = logging.getLogger("storage")

SUPABASE_URL = os.environ.get("SUPABASE_URL") or os.environ.get("VITE_SUPABASE_URL", "")
# R-042b: the server writes server-side, so the preferred key is the project's
# secret / service_role key (bypasses RLS). docs/sql/001_init.sql enables RLS
# with no anon policies, so an anon/publishable key can read/write nothing
# (verified on a hosted project — docs/audit/M7/logs/20). SUPABASE_ANON_KEY is
# kept as a fallback for existing deployments. Never expose the secret key to
# the browser (no VITE_ variable for it).
_KEY_VARS = ("SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY", "VITE_SUPABASE_ANON_KEY")
SUPABASE_KEY_SOURCE = next((v for v in _KEY_VARS if os.environ.get(v)), "")
SUPABASE_KEY = os.environ.get(SUPABASE_KEY_SOURCE, "") if SUPABASE_KEY_SOURCE else ""


def _jwt_role(key: str) -> Optional[str]:
    """Return the unverified `role` claim of a legacy Supabase JWT key, or None.

    Classification only — the signature is NOT checked and the value never
    leaves this function. Supabase legacy keys are JWTs whose payload carries
    role=anon or role=service_role.
    """
    parts = key.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        role = json.loads(base64.urlsafe_b64decode(payload.encode())).get("role")
        return role if isinstance(role, str) else None
    except Exception:
        return None


def classify_key(key: str, source: str) -> str:
    """R-078 (RISK-018): classify a Supabase key without exposing it.

    'service' — sb_secret_* or JWT role=service_role (bypasses RLS: writes work)
    'public'  — sb_publishable_* or JWT role=anon, or an unrecognised key that
                came from an *_ANON_KEY variable (RLS rejects every write, D-039)
    'unknown' — unrecognised format from SUPABASE_SERVICE_KEY
    'none'    — no key configured
    The key format wins over the variable name, so an anon key placed in
    SUPABASE_SERVICE_KEY is still reported as 'public'.
    """
    if not key:
        return "none"
    if key.startswith("sb_secret_"):
        return "service"
    if key.startswith("sb_publishable_"):
        return "public"
    role = _jwt_role(key)
    if role == "service_role":
        return "service"
    if role == "anon":
        return "public"
    return "public" if source.endswith("ANON_KEY") else "unknown"


def key_status() -> Dict[str, str]:
    """Kind and source variable of the configured key — never its value."""
    return {"kind": classify_key(SUPABASE_KEY, SUPABASE_KEY_SOURCE), "source": SUPABASE_KEY_SOURCE or "none"}


PUBLIC_KEY_WARNING = (
    "Supabase is configured with a PUBLIC key (%s, from %s): with the RLS in "
    "docs/sql/001_init.sql every write is rejected and the server keeps data in "
    "memory only (D-039, RISK-018). Set SUPABASE_SERVICE_KEY to the project's "
    "secret/service_role key."
)


def log_key_status() -> Dict[str, str]:
    """Log the key classification once at startup (R-078). Returns key_status()."""
    st = key_status()
    if not is_configured():
        return st
    if st["kind"] == "public":
        logger.warning(PUBLIC_KEY_WARNING, st["kind"], st["source"])
    elif st["kind"] == "unknown":
        logger.warning("Supabase key format not recognised (from %s) — cannot tell whether writes will pass RLS; "
                       "run scripts/run_r042_storage_verify.py after deploy.", st["source"])
    else:
        logger.info("Supabase storage key: %s (from %s)", st["kind"], st["source"])
    return st


def _jsonable(value: Any) -> Any:
    """Return a JSON-native copy of ``value`` for a ``jsonb`` column.

    R-042: values were previously sent as ``json.dumps(value)``, i.e. a JSON
    *string*; PostgREST stored that string inside ``jsonb`` (double-encoded), so
    readers got ``str`` instead of an object (verified live — docs/audit/M7/logs/13).
    Round-tripping keeps the object shape and coerces non-JSON types via ``str``.
    """
    return json.loads(json.dumps(value, default=str))


# R-082 (RISK-013): runtime outcome of every storage operation, so a failure
# after startup (Supabase down, key revoked, 5xx) is visible in /health and
# /metrics instead of only in the log. Only the operation name, HTTP status or
# exception *type* and a UTC timestamp are kept — never bodies, URLs or keys.
_STATS: Dict[str, Any] = {"ops": {}, "last_ok": None, "last_error": None, "last_success_at": None}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _record(op: str, ok: bool, reason: Optional[str] = None) -> None:
    # R-159: the same outcome on the current `storage` span (status code only)
    m = re.match(r"^HTTP (\d{3})$", reason or "")
    telemetry.annotate(kind="storage", outcome="ok" if ok else "fail",
                       http_status=int(m.group(1)) if m else None)
    counters = _STATS["ops"].setdefault(op, {"ok": 0, "failed": 0})
    counters["ok" if ok else "failed"] += 1
    _STATS["last_ok"] = ok
    if ok:
        _STATS["last_success_at"] = _now_iso()
    else:
        _STATS["last_error"] = {"operation": op, "reason": reason or "unknown", "at": _now_iso()}


def _record_exception(op: str, e: BaseException) -> None:
    _record(op, False, f"exception: {type(e).__name__}")
    telemetry.annotate(kind="storage", error=e, reason="exception")


def _traced(op: str):
    """R-159: one `storage` span per operation actually attempted (an
    unconfigured store does nothing and emits nothing). Return value and
    failure handling are unchanged."""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            if not is_configured():
                return await fn(*args, **kwargs)
            with telemetry.span("storage", op):
                return await fn(*args, **kwargs)
        return wrapper
    return deco


def storage_stats() -> Dict[str, Any]:
    """Snapshot of storage operation outcomes (R-082). Safe to expose: no values."""
    ops = {k: dict(v) for k, v in _STATS["ops"].items()}
    return {
        "operations": ops,
        "total_ok": sum(v["ok"] for v in ops.values()),
        "total_failed": sum(v["failed"] for v in ops.values()),
        "last_ok": _STATS["last_ok"],
        "last_error": dict(_STATS["last_error"]) if _STATS["last_error"] else None,
        "last_success_at": _STATS["last_success_at"],
    }


def reset_storage_stats() -> None:
    """Test helper: forget all recorded outcomes."""
    _STATS.update({"ops": {}, "last_ok": None, "last_error": None, "last_success_at": None})


def _rows(op: str, body: Any, key: str) -> List[Dict[str, Any]]:
    """R-160 (storage read contract v1): a PostgREST read must be a JSON array
    of row objects carrying ``key``. Anything else is a malformed response:
    recorded as a failure of ``op`` (fixed reason, no body) and read as no
    rows — never handed to the API as if it were rows (a non-list body used
    to become ``{"count": len(body), "tasks": body}``, and a row without
    ``task_id`` a 500 in GET /api/tasks/{id})."""
    if not isinstance(body, list):
        logger.warning("%s: malformed response (not a list)", op)
        _record(op, False, "malformed response")
        return []
    rows = [r for r in body if isinstance(r, dict) and isinstance(r.get(key), (str, int))]
    if len(rows) != len(body):
        logger.warning("%s: %d malformed row(s) dropped", op, len(body) - len(rows))
        _record(op, False, "malformed response")
    return rows


async def _accepted(r: aiohttp.ClientResponse, op: str, codes: tuple) -> bool:
    """True if the HTTP status is in ``codes``; otherwise record the failure.

    R-149 (Security QA): the backend's rejection body is untrusted text — a
    broken or hostile backend can echo the service key it received — so it is
    never logged. The diagnosis keeps the operation and the status (the
    failure stats record the same); that is what RISK-013 needs.
    """
    if r.status in codes:
        _record(op, True)
        return True
    _record(op, False, f"HTTP {r.status}")
    logger.warning("%s rejected: HTTP %s", op, r.status)
    return False


def _scoped_params(limit: int, scope) -> Dict[str, str]:
    params = {"order": "created_at.desc", "limit": str(int(limit))}
    if scope is not ALL_TENANTS:
        params["tenant_id"] = f"eq.{scope}"  # scope validated by check_scope: [A-Za-z0-9_-]
    return params


_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _task_by_id_params(task_id: str, scope) -> Dict[str, str]:
    """BUG-04 (R-143): one row by primary key, scope enforced in the query
    (tenant_id=eq.<t>; never matches NULL). No order: it is not a list."""
    params = {"task_id": f"eq.{task_id}", "limit": "1"}
    if scope is not ALL_TENANTS:
        params["tenant_id"] = f"eq.{scope}"  # scope validated by check_scope: [A-Za-z0-9_-]
    return params


def is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


class SupabaseStorage:
    """Async Supabase REST API client for persisting civilization data."""

    def __init__(self, *, guarded: bool = False, allow_private: bool = False):
        self.url = SUPABASE_URL
        self.key = SUPABASE_KEY
        # R-161b (G-161-3): guarded=True pins every connection to the DNS
        # answer the SEC-05 policy vetted (GuardedResolver, no DNS cache,
        # verifying TLS, redirects refused). Opt-in: the integration runner
        # uses it; the server's default path is unchanged.
        self.guarded, self.allow_private = guarded, allow_private
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            extra = {}
            if self.guarded:
                from agent_civilization.agents.external_adapter import guarded_session_kwargs
                extra = guarded_session_kwargs(self.allow_private)
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                },
                **extra,
            )
        return self._session

    @_traced("insert_task")
    async def insert_task(self, task_id: str, specialization: str, prompt: str, status: str = "queued",
                          *, tenant_id: Optional[str] = None) -> bool:
        """SEC-02: ``tenant_id`` is the owner, fixed at insert (``update_task``
        never changes it). A row inserted without one is untenanted: no tenant
        can read it (quarantined, admin ``ALL_TENANTS`` only)."""
        if not is_configured():
            return False
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.url}/rest/v1/tasks",
                json={
                    "task_id": task_id,
                    "specialization": specialization,
                    "prompt": prompt[:500],
                    "status": status,
                    "tenant_id": tenant_id,
                },
            ) as r:
                return await _accepted(r, "insert_task", (200, 201))
        except Exception as e:
            logger.warning("insert_task failed: %s", type(e).__name__)
            # R-149 (Security QA): the exception TYPE is the diagnosis — the
            # raw text is not trusted (all handlers below log the same way).
            _record_exception("insert_task", e)
            return False

    @_traced("update_task")
    async def update_task(self, task_id: str, status: str, agent_name: str = None,
                          result: Dict = None, elapsed_s: float = None) -> bool:
        if not is_configured():
            return False
        try:
            session = await self._get_session()
            payload: Dict[str, Any] = {"status": status}
            if agent_name:
                payload["agent_name"] = agent_name
            if result is not None:
                payload["result"] = _jsonable(result)
            if elapsed_s is not None:
                payload["elapsed_s"] = elapsed_s
            if status in ("completed", "failed"):
                payload["finished_at"] = "now()"
            async with session.patch(
                f"{self.url}/rest/v1/tasks?task_id=eq.{task_id}",
                json=payload,
            ) as r:
                return await _accepted(r, "update_task", (200, 204))
        except Exception as e:
            logger.warning("update_task failed: %s", type(e).__name__)
            _record_exception("update_task", e)
            return False

    @_traced("get_tasks")
    async def get_tasks(self, limit: int = 50, *, scope=None) -> List[Dict[str, Any]]:
        """SEC-02: rows of ``scope`` only — a tenant id, or ALL_TENANTS for an
        admin. No scope -> no rows (default deny). Untenanted rows (NULL) never
        match a tenant filter."""
        if not is_configured() or scope is None:
            return []
        check_scope(scope)
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.url}/rest/v1/tasks", params=_scoped_params(limit, scope),
            ) as r:
                if await _accepted(r, "get_tasks", (200,)):
                    return _rows("get_tasks", await r.json(), "task_id")
                return []
        except Exception as e:
            logger.warning("get_tasks failed: %s", type(e).__name__)
            _record_exception("get_tasks", e)
            return []

    @_traced("get_task")
    async def get_task(self, task_id: str, *, scope=None) -> Optional[Dict[str, Any]]:
        """BUG-04 (R-143): the stored row of ``task_id`` within ``scope`` — a
        tenant id, or ALL_TENANTS for an admin — or None. The tenant filter is
        part of the PostgREST query (no unscoped fetch). No scope -> None with
        no network call (default deny); an id outside the task-id alphabet is
        never sent as a filter."""
        if not is_configured() or scope is None:
            return None
        check_scope(scope)
        if not isinstance(task_id, str) or not _TASK_ID_RE.match(task_id):
            return None
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.url}/rest/v1/tasks", params=_task_by_id_params(task_id, scope),
            ) as r:
                if await _accepted(r, "get_task", (200,)):
                    rows = [row for row in _rows("get_task", await r.json(), "task_id")
                            if row["task_id"] == task_id]
                    return rows[0] if rows else None
                return None
        except Exception as e:
            logger.warning("get_task failed: %s", type(e).__name__)
            _record_exception("get_task", e)
            return None

    @_traced("upsert_agent_stats")
    async def upsert_agent_stats(self, agent_name: str, specialization: str,
                                  tasks_completed: int, tasks_failed: int,
                                  total_time_s: float) -> bool:
        if not is_configured():
            return False
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.url}/rest/v1/agent_stats",
                params={"on_conflict": "agent_name"},
                json={
                    "agent_name": agent_name,
                    "specialization": specialization,
                    "tasks_completed": tasks_completed,
                    "tasks_failed": tasks_failed,
                    "total_time_s": total_time_s,
                    "last_active_at": "now()",
                    "updated_at": "now()",
                },
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                    "Prefer": "upsert=on-conflict,resolution=merge-duplicates",
                },
            ) as r:
                return await _accepted(r, "upsert_agent_stats", (200, 201))
        except Exception as e:
            logger.warning("upsert_agent_stats failed: %s", type(e).__name__)
            _record_exception("upsert_agent_stats", e)
            return False

    @_traced("get_agent_stats")
    async def get_agent_stats(self) -> List[Dict[str, Any]]:
        if not is_configured():
            return []
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.url}/rest/v1/agent_stats?order=total_time_s.desc",
            ) as r:
                if await _accepted(r, "get_agent_stats", (200,)):
                    return _rows("get_agent_stats", await r.json(), "agent_name")
                return []
        except Exception as e:
            logger.warning("get_agent_stats failed: %s", type(e).__name__)
            _record_exception("get_agent_stats", e)
            return []

    @_traced("insert_event")
    async def insert_event(self, event_type: str, event_data: Dict[str, Any], *,
                           tenant_id: Optional[str] = None) -> bool:
        if not is_configured():
            return False
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.url}/rest/v1/events",
                json={
                    "event_type": event_type,
                    "event_data": _jsonable(event_data),
                    "tenant_id": tenant_id,
                },
            ) as r:
                return await _accepted(r, "insert_event", (200, 201))
        except Exception as e:
            logger.warning("insert_event failed: %s", type(e).__name__)
            _record_exception("insert_event", e)
            return False

    @_traced("get_events")
    async def get_events(self, limit: int = 50, *, scope=None) -> List[Dict[str, Any]]:
        """SEC-02: same scoping rule as ``get_tasks``."""
        if not is_configured() or scope is None:
            return []
        check_scope(scope)
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.url}/rest/v1/events", params=_scoped_params(limit, scope),
            ) as r:
                if await _accepted(r, "get_events", (200,)):
                    return _rows("get_events", await r.json(), "event_type")
                return []
        except Exception as e:
            logger.warning("get_events failed: %s", type(e).__name__)
            _record_exception("get_events", e)
            return []

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


_storage: Optional[SupabaseStorage] = None


def get_storage() -> SupabaseStorage:
    global _storage
    if _storage is None:
        _storage = SupabaseStorage()
    return _storage
