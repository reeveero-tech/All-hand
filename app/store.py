"""Run persistence: in-memory for live runs, Supabase (PostgREST) for history when configured.

A run is written when it starts, every HEARTBEAT seconds while it runs, and when it ends. A stored run that
still says "running" but is not live in this process and has a stale heartbeat was killed (restart, deploy,
crash): it is reported as "interrupted" instead of pretending to still run.
"""
from __future__ import annotations

import os
import time

import httpx

URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY") or ""
TABLE = "runs"
HEARTBEAT = 20  # seconds between progress saves of a running run
STALE = 90  # a "running" row whose heartbeat is older than this, and that is not live here, is interrupted
LIST_LIMIT = 50
SUMMARY = ("id", "goal", "status", "agents_spawned", "created_at", "finished_at", "heartbeat_at",
           "tasks_total", "tasks_done", "tasks_failed", "thread_id", "follow_up_of")


def enabled() -> bool:
    return bool(URL and KEY)


def _h() -> dict:
    return {"apikey": KEY, "Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal"}


async def save(run: dict) -> bool:
    if not enabled():
        return False
    row = {"id": run["id"], "goal": run["goal"], "status": run["status"], "result": run["result"],
           "data": run, "agents_spawned": run["agents_spawned"]}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{URL}/rest/v1/{TABLE}", headers=_h(), json=row)
        return r.status_code < 300
    except httpx.HTTPError:
        return False


async def load(run_id: str) -> dict | None:
    if not enabled():
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{URL}/rest/v1/{TABLE}", headers=_h(), params={"id": f"eq.{run_id}", "select": "data"})
        rows = r.json() if r.status_code == 200 else []
        return rows[0]["data"] if rows else None
    except (httpx.HTTPError, ValueError, KeyError):
        return None


async def list_for_owner(owner: str, limit: int = LIST_LIMIT) -> list[dict] | None:
    """Summaries of the owner's stored runs, newest first. None means history could not be read."""
    if not enabled():
        return []
    select = "id,goal,status,agents_spawned," + ",".join(f"{k}:data->{k}" for k in SUMMARY[4:])
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{URL}/rest/v1/{TABLE}", headers=_h(),
                            params={"select": select, "data->>owner": f"eq.{owner}",
                                    "order": "data->created_at.desc", "limit": str(limit)})
        if r.status_code != 200:
            return None
        return [{k: row.get(k) for k in SUMMARY} for row in r.json()]
    except (httpx.HTTPError, ValueError):
        return None


def effective_status(data: dict, live: bool, now: float | None = None) -> str:
    """What a stored run really is now."""
    status = data.get("status")
    if status != "running" or live:
        return status
    beat = data.get("heartbeat_at") or data.get("created_at") or 0
    return "interrupted" if (now or time.time()) - float(beat) > STALE else "running"
