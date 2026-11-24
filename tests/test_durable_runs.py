"""Durable runs: saved at start, on heartbeat and at the end (before 'finished' is announced);
history survives restarts; a run killed mid-way shows as interrupted, never as still running."""
import asyncio
import time

import httpx
from fastapi.testclient import TestClient

from app import main, store
from app.orchestrator import Orchestrator, Run
from app.llm import Usage, extract_json
import json


class FakeLLM:
    def __init__(self):
        self.usage = Usage()

    async def chat(self, messages, *a, **k):
        sys = messages[0]["content"]
        if sys.startswith("أنت المنسق الرئيسي"):
            return json.dumps({"simple": True, "summary": "s", "tasks": [
                {"id": "a", "title": "a", "role": "writer", "instructions": "x", "acceptance": "ok", "depends_on": []}]})
        if sys.startswith("أنت مدقق"):
            return json.dumps({"pass": True, "score": 9, "issues": []})
        if sys.startswith("أنت المنسق. اجمع"):
            return "جواب"
        return json.dumps({"action": "final", "output": "ناتج"})

    async def chat_json(self, messages, *a, **k):
        return extract_json(await self.chat(messages))

A = {"X-Client-Key": "cli_" + "a" * 32}
B = {"X-Client-Key": "cli_" + "b" * 32}
OA = main._owner(A["X-Client-Key"])


def setup_function():
    main.RUNS.clear()


def test_effective_status():
    now = 10_000.0
    assert store.effective_status({"status": "running", "heartbeat_at": now - 5}, live=False, now=now) == "running"
    assert store.effective_status({"status": "running", "heartbeat_at": now - 500}, live=False, now=now) == "interrupted"
    assert store.effective_status({"status": "running", "created_at": now - 500}, live=False, now=now) == "interrupted"
    assert store.effective_status({"status": "running", "heartbeat_at": now - 500}, live=True, now=now) == "running"
    assert store.effective_status({"status": "done", "heartbeat_at": 0}, live=False, now=now) == "done"


def test_list_merges_live_and_history_and_marks_interrupted(monkeypatch):
    live = Run(goal="حية", llm_public={}, owner=OA)
    main.RUNS[live.id] = live
    old = time.time() - 3600
    rows = [{"id": live.id, "goal": "نسخة قديمة", "status": "running", "created_at": live.created_at, "heartbeat_at": old},
            {"id": "aaaaaaaaaaa1", "goal": "انقطعت", "status": "running", "created_at": old, "heartbeat_at": old},
            {"id": "aaaaaaaaaaa2", "goal": "اكتملت", "status": "done", "created_at": old - 10, "heartbeat_at": old}]
    asked = []

    async def list_for_owner(owner, limit=50):
        asked.append(owner)
        return [dict(r) for r in rows] if owner == OA else []
    monkeypatch.setattr(store, "list_for_owner", list_for_owner)
    c = TestClient(main.app)
    got = c.get("/api/runs", headers=A).json()
    assert [(r["id"], r["status"]) for r in got] == [(live.id, "running"), ("aaaaaaaaaaa1", "interrupted"),
                                                     ("aaaaaaaaaaa2", "done")]
    assert got[0]["goal"] == "حية"  # the live copy wins over the stored snapshot
    assert all("heartbeat_at" not in r and "owner" not in r for r in got)
    assert c.get("/api/runs", headers=B).json() == []
    assert asked == [OA, main._owner(B["X-Client-Key"])]  # history is always filtered by the caller's owner


def test_history_unavailable_is_reported(monkeypatch):
    async def down(owner, limit=50):
        return None
    monkeypatch.setattr(store, "list_for_owner", down)
    r = TestClient(main.app).get("/api/runs", headers=A)
    assert r.status_code == 200 and r.headers["X-History"] == "unavailable"


def test_stored_running_run_without_heartbeat_reads_as_interrupted(monkeypatch):
    async def load(run_id):
        return {"id": run_id, "goal": "g", "status": "running", "owner": OA, "created_at": 1.0, "heartbeat_at": 1.0}
    monkeypatch.setattr(store, "load", load)
    got = TestClient(main.app).get("/api/runs/abcdefabcdef", headers=A).json()
    assert got["status"] == "interrupted" and "heartbeat_at" not in got and "owner" not in got


async def test_final_save_happens_before_finished_is_announced():
    r = Run(goal="سؤال", llm_public={})
    seen = []

    async def persist(final=False):
        seen.append((final, r.status, [e["type"] for e in r.events]))
        return True
    await Orchestrator(r, FakeLLM(), persist=persist).execute()
    final, status, types = seen[-1]
    assert final and status == "done" and "run_finished" not in types
    assert r.events[-1]["type"] == "run_finished" and r.events[-1]["saved"] is True and r.saved is True


async def test_failed_final_save_is_explicit():
    r = Run(goal="سؤال", llm_public={})
    calls = []

    async def persist(final=False):
        calls.append(final)
        if final:
            raise httpx.ConnectError("db down")
        return True
    await Orchestrator(r, FakeLLM(), persist=persist).execute()
    assert calls.count(True) == 3  # retried
    assert r.status == "done" and r.saved is False and r.events[-1]["saved"] is False
    assert r.full()["saved"] is False


def test_run_is_saved_when_it_starts_with_owner(monkeypatch):
    saved = []

    async def save(data):
        saved.append({k: data.get(k) for k in ("id", "status", "owner", "heartbeat_at")})
        return True
    monkeypatch.setattr(store, "save", save)

    async def slow(self):
        await asyncio.sleep(0.2)
        self.run.status = "done"
    monkeypatch.setattr(main.Orchestrator, "execute", slow)
    with TestClient(main.app) as c:
        rid = c.post("/api/runs", json={"goal": "x"}, headers=A).json()["id"]
        for _ in range(50):
            if saved:
                break
            time.sleep(0.01)
    assert saved[0]["id"] == rid and saved[0]["status"] == "running" and saved[0]["owner"] == OA
    assert saved[0]["heartbeat_at"]


def test_shutdown_marks_live_runs_interrupted(monkeypatch):
    saved = []

    async def save(data):
        saved.append((data["id"], data["status"], data["owner"]))
        return True
    monkeypatch.setattr(store, "save", save)
    run = Run(goal="طويلة", llm_public={}, owner=OA)
    main.RUNS[run.id] = run
    with TestClient(main.app):
        pass  # lifespan shutdown runs on exit
    assert (run.id, "interrupted", OA) in saved


async def test_list_for_owner_filters_on_owner_in_the_query(monkeypatch):
    seen = []

    def handler(request: httpx.Request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=[{"id": "x1", "goal": "g", "status": "done", "agents_spawned": 1,
                                          "created_at": 5.0, "finished_at": 6.0, "heartbeat_at": 6.0,
                                          "tasks_total": 1, "tasks_done": 1, "tasks_failed": 0}])
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(store, "URL", "https://db.example")
    monkeypatch.setattr(store, "KEY", "k")
    rows = await store.list_for_owner(OA)
    assert seen[0]["data->>owner"] == f"eq.{OA}" and seen[0]["order"] == "data->created_at.desc"
    assert rows[0]["id"] == "x1" and set(rows[0]) == set(store.SUMMARY)
