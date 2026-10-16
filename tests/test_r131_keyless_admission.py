"""R-131 (RISK-028, D-054): keyless LLM admission control + chat routing.

Measured before the fix with a real uvicorn server under concurrent HTTP load
(docs/audit/M7/logs/raw/156_r131_http_load_before.log):
  * every /api/chat message went through the one-call-at-a-time keyless LLM to
    pick a specialist, so "sum these numbers" (no LLM needed) waited ~70 s;
  * the 90 s budget started before the queue, so callers deep in the queue
    used it all waiting and failed with "no attempt".
"""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

import server
from agent_civilization.core import llm as L


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(L, "_keyless_lock", None)
    monkeypatch.setattr(L, "_keyless_last", 0.0)
    monkeypatch.setattr(L, "_keyless_waiting", 0)
    monkeypatch.setattr(L, "KEYLESS_MIN_SPACING_S", 0.0)
    calls = []

    def fake_post(system, prompt, max_tokens, timeout):
        calls.append(prompt)
        time.sleep(0.3)
        return {"choices": [{"message": {"content": f"answer to {prompt}"}}], "model": "fake"}

    monkeypatch.setattr(L, "_keyless_post", fake_post)
    return calls


def test_budget_starts_at_the_callers_turn_not_in_the_queue(fresh, monkeypatch):
    # 4 callers, 0.3 s each, budget 0.5 s: before R-131 callers 3 and 4 spent
    # their whole budget queued and failed with "no attempt".
    monkeypatch.setattr(L, "KEYLESS_BUDGET_S", 0.5)
    monkeypatch.setattr(L, "KEYLESS_MAX_WAIT_S", 10.0)

    async def go():
        return await asyncio.gather(*(L._keyless_complete("s", f"p{i}", 10) for i in range(4)))

    res = asyncio.run(go())
    assert [r["ok"] for r in res] == [True] * 4, [r.get("error") for r in res]
    assert len(fresh) == 4 and L._keyless_waiting == 0


def test_full_queue_fails_fast_with_explicit_busy(fresh, monkeypatch):
    monkeypatch.setattr(L, "KEYLESS_MIN_SPACING_S", 6.0)
    monkeypatch.setattr(L, "KEYLESS_MAX_WAIT_S", 30.0)
    monkeypatch.setattr(L, "_keyless_waiting", 10)  # ~66 s ahead > 30 s

    async def go():
        L._keyless_lock = asyncio.Lock()
        await L._keyless_lock.acquire()
        t = time.monotonic()
        r = await L._keyless_complete("s", "p", 10)
        return r, time.monotonic() - t

    before = L.keyless_stats["busy"]
    r, dt = asyncio.run(go())
    assert not r["ok"] and r["busy"] and "keyless LLM busy" in r["error"]
    assert dt < 0.1 and fresh == [] and L.keyless_stats["busy"] == before + 1


def test_lock_wait_is_bounded(fresh, monkeypatch):
    async def go():
        L._keyless_lock = asyncio.Lock()
        await L._keyless_lock.acquire()  # someone else holds it forever
        t = time.monotonic()
        r = await L._keyless_complete("s", "p", 10, max_wait_s=0.2)
        return r, time.monotonic() - t

    r, dt = asyncio.run(go())
    assert not r["ok"] and r["busy"] and 0.15 < dt < 1.0
    assert fresh == [] and L._keyless_waiting == 0


def test_idle_provider_still_answers(fresh):
    r = asyncio.run(L._keyless_complete("s", "hello", 10))
    assert r["ok"] and r["provider"] == "keyless" and r["content"] == "answer to hello"


class _KeylessLLM:
    available = True
    provider = "keyless"

    async def complete(self, *a, **k):
        raise AssertionError("chat must not route through the keyless LLM (R-131)")


@pytest.fixture
def client(monkeypatch, tenant_auth):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # SEC-02: chat/tasks need an authenticated tenant
    with TestClient(server.app, raise_server_exceptions=False, headers=tenant_auth.headers) as c:
        yield c


def test_chat_does_not_route_through_keyless_llm(client, monkeypatch):
    monkeypatch.setattr(server, "get_llm", lambda: _KeylessLLM())
    d = client.post("/api/chat", json={"message": "حلل 4 8 15"}).json()
    assert d["routed_to"] == "analyst" and d["routing"]["method"] == "keywords"
    assert d["outcome"]["ok"]


def test_keyed_provider_still_routes_by_llm(client, monkeypatch):
    class _Keyed:
        available = True
        provider = "openrouter"

        async def complete(self, *a, **k):
            return {"ok": True, "content": "db_manager"}

    monkeypatch.setattr(server, "get_llm", lambda: _Keyed())
    d = client.post("/api/chat", json={"message": "حلل 4 8 15"}).json()
    assert d["routed_to"] == "db_manager" and d["routing"]["method"] == "llm"
