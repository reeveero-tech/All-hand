#!/usr/bin/env python3
"""R-042: verify supabase_store.py against a live PostgREST/Supabase endpoint.

Reads SUPABASE_URL and SUPABASE_SERVICE_KEY (or SUPABASE_ANON_KEY fallback) from
the environment (same variables as production — see supabase_store.py). Works unchanged against:
  * a real Supabase project (after applying docs/sql/001_init.sql), or
  * the local/CI stack in scripts/r042/run_local_stack.sh
    (PostgreSQL + PostgREST + /rest/v1 prefix proxy).

Each check exercises the real storage method, then reads the row back through
the REST API and asserts the stored shape/type. Exit code: 0 only if every check
passes; 1 otherwise; 2 if storage is not configured.
"""

import asyncio
import os
import sys
import uuid

import aiohttp

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from agent_civilization.storage import supabase_store as ss  # noqa: E402

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


async def fetch(session, path):
    async with session.get(f"{ss.SUPABASE_URL}/rest/v1/{path}") as r:
        return r.status, await r.json()


async def delete(session, path):
    async with session.delete(f"{ss.SUPABASE_URL}/rest/v1/{path}") as r:
        return r.status


async def main() -> int:
    if not ss.is_configured():
        print("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — nothing to verify")
        return 2
    print(f"target: {ss.SUPABASE_URL}")
    store = ss.SupabaseStorage()
    run = uuid.uuid4().hex[:8]
    task_id = f"r042-{run}"
    agent = f"r042-agent-{run}"
    headers = {"apikey": ss.SUPABASE_KEY, "Authorization": f"Bearer {ss.SUPABASE_KEY}"}
    async with aiohttp.ClientSession(headers=headers) as http:
        try:
            # --- tasks: insert -> select
            ok = await store.insert_task(task_id, "analysis", "R-042 live check", "queued")
            check("tasks.insert_task returns True", ok)
            st, rows = await fetch(http, f"tasks?task_id=eq.{task_id}")
            check("tasks row readable after insert", st == 200 and len(rows) == 1, f"http={st} rows={rows}")
            if rows:
                check("tasks.created_at defaulted", rows[0].get("created_at") is not None)
                check("tasks.status == queued", rows[0].get("status") == "queued")

            listed = await store.get_tasks(limit=500)
            check("tasks.get_tasks includes row", any(t.get("task_id") == task_id for t in listed),
                  f"{len(listed)} rows")

            # --- tasks: running -> completed with result/elapsed
            ok = await store.update_task(task_id, "running", agent_name=agent)
            check("tasks.update_task(running) returns True", ok)
            result = {"ok": True, "output": "x = 1", "elapsed_s": 0.01, "nested": {"n": [1, 2]}}
            ok = await store.update_task(task_id, "completed", agent_name=agent, result=result, elapsed_s=0.01)
            check("tasks.update_task(completed, result) returns True", ok)
            st, rows = await fetch(http, f"tasks?task_id=eq.{task_id}")
            row = rows[0] if st == 200 and rows else {}
            check("tasks.status == completed", row.get("status") == "completed", f"row={row}")
            check("tasks.agent_name stored", row.get("agent_name") == agent)
            check("tasks.finished_at is a timestamp", isinstance(row.get("finished_at"), str) and
                  row["finished_at"][:2] in ("19", "20"), f"finished_at={row.get('finished_at')!r}")
            check("tasks.result stored as JSON object (not string)", row.get("result") == result,
                  f"type={type(row.get('result')).__name__} value={row.get('result')!r}")
            check("tasks.elapsed_s numeric", isinstance(row.get("elapsed_s"), (int, float)))

            # --- agent_stats: upsert twice -> one merged row
            ok1 = await store.upsert_agent_stats(agent, "analysis", 1, 0, 0.5)
            ok2 = await store.upsert_agent_stats(agent, "analysis", 2, 1, 1.25)
            check("agent_stats.upsert (insert) returns True", ok1)
            check("agent_stats.upsert (conflict/merge) returns True", ok2)
            st, rows = await fetch(http, f"agent_stats?agent_name=eq.{agent}")
            check("agent_stats exactly one row after two upserts", st == 200 and len(rows) == 1, f"rows={rows}")
            if rows:
                r0 = rows[0]
                check("agent_stats merged values", (r0.get("tasks_completed"), r0.get("tasks_failed"),
                      r0.get("total_time_s")) == (2, 1, 1.25), f"row={r0}")
                check("agent_stats timestamps set", bool(r0.get("last_active_at")) and bool(r0.get("updated_at")))
            stats = await store.get_agent_stats()
            check("agent_stats.get_agent_stats includes row", any(s.get("agent_name") == agent for s in stats))

            # --- events: insert -> select
            data = {"task_id": task_id, "ok": True, "n": 3}
            ok = await store.insert_event("r042_check", data)
            check("events.insert_event returns True", ok)
            events = await store.get_events(limit=500)
            mine = [e for e in events if e.get("event_type") == "r042_check"
                    and isinstance(e.get("event_data"), dict) and e["event_data"].get("task_id") == task_id]
            raw = [e for e in events if e.get("event_type") == "r042_check"]
            check("events.event_data stored as JSON object (not string)", len(mine) == 1,
                  f"sample={raw[:1]}")
            if mine:
                check("events.id is integer identity", isinstance(mine[0].get("id"), int))

            # --- negative control: a rejected write must return False (and be logged — RISK-013)
            bad = ss.SupabaseStorage()
            bad.key = "invalid.jwt.token"
            ok = await bad.insert_task(f"{task_id}-bad", "analysis", "must be rejected")
            await bad.close()
            check("rejected write (invalid key) returns False", ok is False)
        finally:
            # best-effort cleanup (may be refused by RLS on a real project; harmless)
            await delete(http, f"tasks?task_id=eq.{task_id}")
            await delete(http, f"agent_stats?agent_name=eq.{agent}")
            await delete(http, f"events?event_type=eq.r042_check&event_data->>task_id=eq.{task_id}")
            await store.close()

    passed, total = sum(RESULTS), len(RESULTS)
    print(f"\nR-042 storage verification: {passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
