"""R-103 (D-046): memory and learning on the live execution path.

Before R-103 (docs/audit/M6/logs/raw/72): ``agos_kernel.agent_production`` was
imported only by tests; a task run through the API left no memory, the next
task could not see it, and LearningRuntime measured nothing.  Also three bugs
inside memory_system: recall(prompt) could not find a task stored by
learn_from_task (prompt kept in metadata, not content), load() left
memory_type/datetimes as str; and (preventive) ids were the clock reading alone.
"""
import asyncio
import json
import time
import urllib.error

import pytest
from fastapi.testclient import TestClient

import server
from agent_civilization.agents.real import create_civilization
from agent_civilization.core import experience, safety
from agos_kernel.agent_production.memory_system import MemorySystem, MemoryType


@pytest.fixture
def client(tenant_auth):
    # SEC-02: memory belongs to the authenticated tenant; API calls authenticate
    with TestClient(server.app, headers=tenant_auth.headers) as c:
        yield c


def _task(c, spec, prompt, data=None):
    tid = c.post("/api/tasks", json={"specialization": spec, "prompt": prompt, "data": data}).json()["task_id"]
    for _ in range(400):
        t = c.get(f"/api/tasks/{tid}").json()
        if t["status"] in ("completed", "failed"):
            return t["result"]
        time.sleep(0.01)
    raise AssertionError("task did not finish")


# ------------------------------------------------------------------ memory
def test_later_api_task_recalls_the_earlier_similar_one(client):
    first = _task(client, "analyst", "analyze weekly sales numbers", [10, 12, 15, 11])
    assert first["experience"]["memory"]["recalled"] == []
    second = _task(client, "analyst", "analyze weekly sales numbers again", [10, 12, 15, 30])
    recalled = second["experience"]["memory"]["recalled"]
    assert len(recalled) == 1
    assert recalled[0]["memory_id"] == first["experience"]["memory"]["stored_id"]
    assert recalled[0]["answer"] == first["result"]["answer"]  # the earlier real answer
    assert recalled[0]["score"] >= experience.MIN_RECALL_SCORE


def test_unrelated_or_other_specialization_tasks_are_not_recalled(client):
    _task(client, "analyst", "analyze weekly sales numbers", [10, 12, 15])
    assert _task(client, "analyst", "حلل درجات الطلاب", [70, 80, 90])["experience"]["memory"]["recalled"] == []
    assert _task(client, "data_processor", "analyze weekly sales numbers", [10, 12, 15]
                 )["experience"]["memory"]["recalled"] == []


def test_failures_are_remembered_too(client):
    bad = _task(client, "modifier", "rename a to b", "def f(:\n")  # invalid Python -> real failure
    assert bad["ok"] is False
    hits = client.get("/api/memory", params={"specialization": "modifier"}).json()["memories"]
    assert hits and hits[0]["ok"] is False and hits[0]["answer"] == bad["result"]["error"]


# ---------------------------------------------------------------- learning
def test_learning_prior_reflects_earlier_outcomes(client):
    r1 = _task(client, "modifier", "rename a to b", "def f():\n    a = 1\n")
    assert r1["ok"] and r1["experience"]["learning"]["prior"]["samples"] == 0
    assert _task(client, "modifier", "rename a to b", "def f(:\n")["ok"] is False   # invalid Python
    r3 = _task(client, "modifier", "rename a to b", "def g():\n    a = 2\n")
    prior = r3["experience"]["learning"]["prior"]
    assert prior["input_kind"] == "code" and prior["samples"] == 2 and prior["success_rate"] == 0.5
    stats = client.get("/api/learning").json()["learning"]["by_specialization"]["modifier"]
    assert stats["samples"] == 3 and stats["success_rate"] == round(2 / 3, 3)


def test_status_reports_experience(client):
    _task(client, "analyst", "avg", [1, 2])
    # SEC-02: the public status shows only the layer's configuration; the
    # counts are the tenant's own, at /api/learning (authenticated)
    exp = client.get("/api/system/status").json()["experience"]
    assert exp["enabled"] is True and "memories" not in exp and "learning" not in exp
    mine = client.get("/api/learning").json()
    assert mine["memories"] == 1 and mine["learning"]["tasks"] == 1


# ------------------------------------------------- behavioural effect: researcher
def _break_network(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("connection reset by peer")
    monkeypatch.setattr(safety, "safe_urlopen", boom)


def test_researcher_falls_back_to_a_remembered_result_when_live_search_fails(client, monkeypatch):
    live = _task(client, "researcher", "Python programming language")
    assert live["ok"] and "degraded" not in live["result"]
    _break_network(monkeypatch)
    again = _task(client, "researcher", "Python programming language")
    res = again["result"]
    assert again["ok"] is True and res["degraded"] is True and res["source"] == "memory"
    assert res["tool_output"] == live["result"]["tool_output"]
    assert "connection reset" in res["live_error"] and "live search failed" in res["answer"]
    assert again["experience"]["memory"]["fallback_used"] == live["experience"]["memory"]["stored_id"]
    # learning records the live failure, not the substituted success
    assert again["experience"]["learning"]["after"]["success_rate"] == 0.5


def test_researcher_without_a_close_memory_still_fails_honestly(client, monkeypatch):
    _task(client, "researcher", "Python programming language")
    _break_network(monkeypatch)
    other = _task(client, "researcher", "history of the Ottoman empire")
    assert other["ok"] is False and other["experience"]["memory"]["fallback_used"] is None


def test_only_researcher_gets_a_fallback(client, monkeypatch):
    _task(client, "api_integrator", "GET https://example.com/")
    _break_network(monkeypatch)
    r = _task(client, "api_integrator", "GET https://example.com/")
    assert r["ok"] is False and r["experience"]["memory"]["fallback_used"] is None


# ------------------------------------------------------- both implementations
def test_specialists_implementation_has_the_same_experience(tenant_context):
    civ = create_civilization(1, impl="specialists")
    agent = civ["analyst"][0]

    async def go():
        await agent.execute({"prompt": "analyze weekly sales", "data": [1, 2, 3]})
        return await agent.execute({"prompt": "analyze weekly sales", "data": [4, 5, 6]})
    out = asyncio.run(go())
    assert out["impl"] and out["experience"]["memory"]["recalled"][0]["prompt"] == "analyze weekly sales"


# ------------------------------------------------------------- persistence
def test_memory_and_learning_survive_a_restart_through_the_file(tmp_path, monkeypatch, client):
    f = tmp_path / "memory.json"
    monkeypatch.setenv("AGOS_MEMORY_FILE", str(f))
    experience.reset()
    first = _task(client, "analyst", "analyze weekly sales numbers", [10, 12, 15])
    asyncio.run(experience.persist())
    assert json.loads(f.read_text(encoding="utf-8"))
    experience.reset()                                   # "restart": process state gone
    later = _task(client, "analyst", "analyze weekly sales numbers", [1, 2, 3])
    assert later["experience"]["memory"]["recalled"][0]["memory_id"] == first["experience"]["memory"]["stored_id"]
    assert later["experience"]["learning"]["prior"]["samples"] == 1   # learning rebuilt from memory


# -------------------------------------------------------- limits / switches
def test_memory_is_bounded(client, monkeypatch):
    monkeypatch.setenv("AGOS_MEMORY_MAX", "10")
    experience.reset()
    for i in range(15):
        _task(client, "analyst", f"run {i}", [i, i + 1])
    assert client.get("/api/learning").json()["memories"] == 10


def test_disabled_layer_leaves_no_trace(client, monkeypatch):
    monkeypatch.setenv("AGOS_MEMORY", "0")
    r = _task(client, "analyst", "avg", [1, 2])
    assert r["ok"] and "experience" not in r
    assert client.get("/api/learning").json()["memories"] == 0


def test_a_broken_memory_never_breaks_the_task(client, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("memory down")
    monkeypatch.setattr(experience, "recall", boom)
    r = _task(client, "analyst", "avg", [1, 2, 3])
    assert r["ok"] and r["result"]["tool_output"]["mean"] == 2.0 and "experience" not in r


def test_clearing_memory_needs_a_configured_key(client, tenant_auth):
    _task(client, "analyst", "avg", [1, 2])
    assert client.delete("/api/memory", headers={"X-API-Key": ""}).status_code == 401   # no key
    assert client.delete("/api/memory", headers={"X-API-Key": "k"}).status_code == 401  # wrong key
    creds = server.CREDENTIALS
    server.CREDENTIALS, server.ENABLE_AUTH = {}, False              # open mode (no credentials)
    try:
        assert client.delete("/api/memory").status_code == 401      # SEC-02: refused (was 403)
    finally:
        server.CREDENTIALS, server.ENABLE_AUTH = creds, True
    assert client.get("/api/learning").json()["memories"] == 1      # nothing was cleared
    assert client.delete("/api/memory").json() == {"cleared": 1}   # the tenant's own memory
    assert client.get("/api/learning").json()["memories"] == 0


# ------------------------------------------------- memory_system bug fixes
def test_recall_finds_a_task_by_its_prompt():
    async def go():
        m = MemorySystem()
        await m.learn_from_task("a1", "analysis", "quarterly revenue report", {"ok": True}, True)
        return await m.recall("quarterly revenue")
    assert len(asyncio.run(go())) == 1


def test_load_restores_types(tmp_path):
    f = str(tmp_path / "m.json")

    async def go():
        m = MemorySystem(persistence_file=f)
        await m.store("k1", MemoryType.SEMANTIC, tags={"t"}, importance=0.8)
        await m.store("k2", MemoryType.SEMANTIC, tags={"t"}, importance=0.8)
        await m.persist()
        m2 = MemorySystem(persistence_file=f)
        await m2.load()
        from agos_kernel.agent_production.memory_system import MemoryQuery
        return await m2.search(MemoryQuery(memory_type=MemoryType.SEMANTIC, limit=10))
    found = asyncio.run(go())
    assert len(found) == 2 and all(isinstance(e.memory_type, MemoryType) for e in found)


def test_ids_do_not_depend_only_on_the_clock(monkeypatch):
    """Preventive (not observed on Linux: 50,000 stores, no collision — raw/73):
    the id was the clock reading alone, so a repeated/coarse reading made the
    second store overwrite the first. Demonstrated with a fixed clock."""
    from datetime import datetime as real_dt
    import agos_kernel.agent_production.memory_system as ms

    class FixedClock(real_dt):
        @classmethod
        def utcnow(cls):
            return real_dt(2026, 1, 1)
    monkeypatch.setattr(ms, "datetime", FixedClock)

    async def go():
        m = MemorySystem()
        for i in range(200):
            await m.store(f"c{i}", MemoryType.EPISODIC)
        return m.get_stats()["total_memories"]
    assert asyncio.run(go()) == 200
