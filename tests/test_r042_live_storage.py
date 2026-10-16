"""R-042: server end-to-end against LIVE storage (PostgREST/Supabase).

Skipped unless R042_LIVE=1 and SUPABASE_URL/SUPABASE_ANON_KEY point at a live
endpoint with docs/sql/001_init.sql + 002_tenant_isolation.sql applied (CI job `storage`, or
scripts/r042/run_local_stack.sh, or a real Supabase project).

Covers what the unit suite cannot: the real HTTP round-trip of every storage
call made by server.py (insert_task, update_task, upsert_agent_stats,
insert_event, get_tasks, get_task, get_events) and the stored column types.
"""
import os
import time

import pytest

LIVE = os.environ.get("R042_LIVE") == "1" and bool(os.environ.get("SUPABASE_URL")) \
    and bool(os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_ANON_KEY"))

pytestmark = [
    pytest.mark.skipif(not LIVE, reason="R-042 live storage not configured (set R042_LIVE=1)"),
    # R-072: may target a hosted Supabase project — exempt from the offline guard.
    pytest.mark.allow_external_network,
]


_KEYS = {}


@pytest.fixture(scope="module")
def client():
    """SEC-02: authenticated as tenant ``r042_live``; a second tenant
    ``r042_other`` is configured to prove the live rows are tenant-scoped."""
    import hashlib
    import secrets

    from fastapi.testclient import TestClient

    import server
    from agent_civilization.core.tenancy import Principal
    _KEYS.update(own="r042-" + secrets.token_urlsafe(16), other="r042o-" + secrets.token_urlsafe(16))
    saved = (server.CREDENTIALS, server.ENABLE_AUTH)
    server.CREDENTIALS = {hashlib.sha256(_KEYS["own"].encode()).hexdigest(): Principal("r042_live"),
                          hashlib.sha256(_KEYS["other"].encode()).hexdigest(): Principal("r042_other")}
    server.ENABLE_AUTH = True
    try:
        with TestClient(server.app, headers={"X-API-Key": _KEYS["own"]}) as c:
            yield c
    finally:
        server.CREDENTIALS, server.ENABLE_AUTH = saved


def _rest(path):
    import json
    import urllib.request
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_ANON_KEY"]
    req = urllib.request.Request(f"{os.environ['SUPABASE_URL']}/rest/v1/{path}",
                                 headers={"apikey": key, "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _wait_done(client, task_id, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = client.get(f"/api/tasks/{task_id}").json()
        if t.get("status") in ("completed", "failed"):
            return t
        time.sleep(0.1)
    raise AssertionError(f"task {task_id} did not finish")


def test_status_reports_live_storage(client):
    body = client.get("/api/system/status").json()
    assert body["supabase_configured"] is True
    assert isinstance(body["tasks_in_db"], int)


def test_task_lifecycle_persisted(client):
    r = client.post("/api/tasks", json={"specialization": "analyst", "prompt": "R-042 e2e: summarise 1 2 3"})
    assert r.status_code == 202, r.text
    task_id = r.json()["task_id"]
    mem = _wait_done(client, task_id)

    # give the post-execution storage writes a moment (they follow the memory update)
    row = None
    for _ in range(100):
        listed = client.get("/api/tasks?limit=200").json()
        assert listed["source"] == "database", listed.get("source")
        row = next((t for t in listed["tasks"] if t["task_id"] == task_id), None)
        if row and row["status"] == mem["status"] and row.get("result") is not None:
            break
        time.sleep(0.1)
    assert row is not None, "task not persisted"
    assert row["status"] == mem["status"]
    assert row["agent_name"] == mem["agent"]
    assert isinstance(row["result"], dict), f"result stored as {type(row['result']).__name__}"
    assert row["result"]["ok"] == mem["result"]["ok"]
    assert row["finished_at"] is not None
    # SEC-02: the live row carries its owner; another tenant does not see it
    assert row["tenant_id"] == "r042_live"
    other = {"X-API-Key": _KEYS["other"]}
    assert not any(t["task_id"] == task_id for t in client.get("/api/tasks?limit=200", headers=other).json()["tasks"])
    assert client.get(f"/api/tasks/{task_id}", headers=other).status_code == 404
    assert client.get("/api/tasks?limit=200", headers={"X-API-Key": ""}).status_code == 401

    # task_completed is written after update_task + upsert_agent_stats; over a
    # real network (hosted Supabase) that lands later — poll, don't read once.
    types = set()
    for _ in range(100):
        events = client.get("/api/events?limit=200").json()["events"]
        types = {e["event_type"] for e in events if isinstance(e.get("event_data"), dict)
                 and e["event_data"].get("task_id") == task_id}
        if {"task_created", "task_completed"} <= types:
            break
        time.sleep(0.1)
    assert {"task_created", "task_completed"} <= types, f"events for task: {types}"
    other_events = client.get("/api/events?limit=200", headers=other).json()["events"]
    assert not any(isinstance(e.get("event_data"), dict) and e["event_data"].get("task_id") == task_id
                   for e in other_events)


def test_task_read_by_id_from_storage_after_memory_loss(client):
    """BUG-04 (R-143): with the record gone from process memory (restart), the
    real route reads the task by ID from real PostgreSQL/PostgREST — for its
    owner only; another tenant gets exactly the missing-ID 404."""
    import server
    r = client.post("/api/tasks", json={"specialization": "analyst", "prompt": "BUG-04 live: summarise 4 5 6"})
    assert r.status_code == 202, r.text
    task_id = r.json()["task_id"]
    mem = _wait_done(client, task_id)
    stored = []
    for _ in range(100):  # the final update_task follows the memory update
        stored = _rest(f"tasks?task_id=eq.{task_id}")
        if stored and stored[0]["status"] == mem["status"] and stored[0].get("result") is not None:
            break
        time.sleep(0.1)
    assert stored and stored[0]["tenant_id"] == "r042_live"

    server.TASKS.pop(task_id)                                  # memory lost
    assert task_id not in server.TASKS
    got = client.get(f"/api/tasks/{task_id}")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["source"] == "database"
    assert body["id"] == body["task_id"] == task_id
    assert body["status"] == mem["status"]
    assert body["result"] == mem["result"]
    assert body["tenant_id"] == "r042_live" and body["agent_name"] == mem["agent"]
    assert task_id not in server.TASKS                         # read, not re-cached

    other = {"X-API-Key": _KEYS["other"]}
    cross = client.get(f"/api/tasks/{task_id}", headers=other)
    missing = client.get("/api/tasks/r042nosuchid", headers=other)
    assert cross.status_code == missing.status_code == 404
    assert cross.content == missing.content and cross.json() == {"detail": "task not found"}


def test_chat_persists_and_agent_stats_upserted(client):
    r = client.post("/api/chat", json={"message": "R-042 e2e: what is 2+2?"})
    assert r.status_code == 200, r.text
    body = r.json()
    task_id, agent = body["task_id"], body["agent"]
    listed = client.get("/api/tasks?limit=200").json()
    row = next((t for t in listed["tasks"] if t["task_id"] == task_id), None)
    assert row is not None and row["status"] in ("completed", "failed")
    assert isinstance(row["result"], dict)
    # BUG-02 (R-141): the stored status is the one derived from the outcome
    # (before: always "completed", even for a failed execution)
    expected = "completed" if body["outcome"]["ok"] else "failed"
    assert row["status"] == expected, (row["status"], body["outcome"]["ok"])
    ev = [e for e in _rest("events?event_type=eq.chat_completed&order=created_at.desc&limit=50")
          if isinstance(e.get("event_data"), dict) and e["event_data"].get("task_id") == task_id]
    assert len(ev) == 1 and ev[0]["event_data"]["ok"] is body["outcome"]["ok"]
    assert ev[0]["event_data"]["status"] == expected

    # read agent_stats straight from the REST endpoint (independent of the server's client)
    rows = _rest(f"agent_stats?agent_name=eq.{agent}")
    assert len(rows) == 1, f"agent_stats rows for {agent}: {rows}"
    assert rows[0]["tasks_completed"] + rows[0]["tasks_failed"] >= 1
