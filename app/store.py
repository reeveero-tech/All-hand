"""Run persistence: in-memory for live runs, Supabase (PostgREST) for history when configured."""
from __future__ import annotations

import os

import httpx

URL = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_KEY") or ""
TABLE = "runs"


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
