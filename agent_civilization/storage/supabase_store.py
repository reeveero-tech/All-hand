#!/usr/bin/env python3
"""
Supabase storage layer for the Agent Civilization.

Persists tasks, agent stats, and events to the Supabase database.
Uses the REST API (no external SDK needed) via aiohttp.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger("storage")

SUPABASE_URL = os.environ.get("SUPABASE_URL") or os.environ.get("VITE_SUPABASE_URL", "")
# R-042b: the server writes server-side, so the preferred key is the project's
# secret / service_role key (bypasses RLS). docs/sql/001_init.sql enables RLS
# with no anon policies, so an anon/publishable key can read/write nothing
# (verified on a hosted project — docs/audit/M7/logs/20). SUPABASE_ANON_KEY is
# kept as a fallback for existing deployments. Never expose the secret key to
# the browser (no VITE_ variable for it).
SUPABASE_KEY = (
    os.environ.get("SUPABASE_SERVICE_KEY")
    or os.environ.get("SUPABASE_ANON_KEY")
    or os.environ.get("VITE_SUPABASE_ANON_KEY", "")
)


def _jsonable(value: Any) -> Any:
    """Return a JSON-native copy of ``value`` for a ``jsonb`` column.

    R-042: values were previously sent as ``json.dumps(value)``, i.e. a JSON
    *string*; PostgREST stored that string inside ``jsonb`` (double-encoded), so
    readers got ``str`` instead of an object (verified live — docs/audit/M7/logs/13).
    Round-tripping keeps the object shape and coerces non-JSON types via ``str``.
    """
    return json.loads(json.dumps(value, default=str))


async def _accepted(r: aiohttp.ClientResponse, op: str, codes: tuple) -> bool:
    """True if the HTTP status is in ``codes``; otherwise log status + body (RISK-013)."""
    if r.status in codes:
        return True
    try:
        body = (await r.text())[:300]
    except Exception:  # pragma: no cover - diagnostic only
        body = "<unreadable>"
    logger.warning("%s rejected: HTTP %s %s", op, r.status, body)
    return False


def is_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


class SupabaseStorage:
    """Async Supabase REST API client for persisting civilization data."""

    def __init__(self):
        self.url = SUPABASE_URL
        self.key = SUPABASE_KEY
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={
                    "apikey": self.key,
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                },
            )
        return self._session

    async def insert_task(self, task_id: str, specialization: str, prompt: str, status: str = "queued") -> bool:
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
                },
            ) as r:
                return await _accepted(r, "insert_task", (200, 201))
        except Exception as e:
            logger.warning("insert_task failed: %s", e)
            return False

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
            logger.warning("update_task failed: %s", e)
            return False

    async def get_tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not is_configured():
            return []
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.url}/rest/v1/tasks?order=created_at.desc&limit={limit}",
            ) as r:
                if await _accepted(r, "get_tasks", (200,)):
                    return await r.json()
                return []
        except Exception as e:
            logger.warning("get_tasks failed: %s", e)
            return []

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
            logger.warning("upsert_agent_stats failed: %s", e)
            return False

    async def get_agent_stats(self) -> List[Dict[str, Any]]:
        if not is_configured():
            return []
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.url}/rest/v1/agent_stats?order=total_time_s.desc",
            ) as r:
                if await _accepted(r, "get_agent_stats", (200,)):
                    return await r.json()
                return []
        except Exception as e:
            logger.warning("get_agent_stats failed: %s", e)
            return []

    async def insert_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
        if not is_configured():
            return False
        try:
            session = await self._get_session()
            async with session.post(
                f"{self.url}/rest/v1/events",
                json={
                    "event_type": event_type,
                    "event_data": _jsonable(event_data),
                },
            ) as r:
                return await _accepted(r, "insert_event", (200, 201))
        except Exception as e:
            logger.warning("insert_event failed: %s", e)
            return False

    async def get_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not is_configured():
            return []
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.url}/rest/v1/events?order=created_at.desc&limit={limit}",
            ) as r:
                if await _accepted(r, "get_events", (200,)):
                    return await r.json()
                return []
        except Exception as e:
            logger.warning("get_events failed: %s", e)
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
