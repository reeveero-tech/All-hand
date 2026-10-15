"""R-132 (RISK-029, D-055): blocking calls run on separate bounded pools.

Before R-132 one default executor (6 threads on the 2-CPU host) served every
blocking call, and under HTTP load a reachability check waited behind slow
LLM/web calls (docs/audit/M7/logs/raw/161_r131_compare.log).
"""
import asyncio
import contextvars
import time

import pytest

from agent_civilization.core import pools
from agent_civilization.core.agents import real_dispatcher as rd


@pytest.fixture(autouse=True)
def fresh_pools(monkeypatch):
    monkeypatch.setattr(pools, "_pools", {})
    yield
    for ex in pools._pools.values():
        ex.shutdown(wait=False, cancel_futures=True)


def test_saturated_web_pool_does_not_delay_network_calls(monkeypatch):
    monkeypatch.setenv("AGOS_POOL_WEB", "2")
    monkeypatch.setenv("AGOS_POOL_WEB_QUEUE", "0")  # uncapped: measure queueing itself

    async def go():
        slow = [asyncio.create_task(rd._to_thread(time.sleep, 0.6, _pool="web")) for _ in range(12)]
        await asyncio.sleep(0.05)
        t = time.monotonic()
        assert await rd._to_thread(lambda: "up", _pool="net") == "up"
        fast = time.monotonic() - t
        t = time.monotonic()
        await rd._to_thread(lambda: None, _pool="web")  # queued behind 12 sleeps on 2 threads
        queued = time.monotonic() - t
        await asyncio.gather(*slow)
        return fast, queued

    fast, queued = asyncio.run(go())
    assert fast < 0.2, fast
    assert queued > 2.0, queued  # the slow kind only delays itself


def test_sizes_from_env_are_clamped(monkeypatch):
    monkeypatch.setenv("AGOS_POOL_NET", "500")
    monkeypatch.setenv("AGOS_POOL_LOCAL", "0")
    monkeypatch.setenv("AGOS_POOL_WEB", "abc")
    assert pools.size("net") == 64 and pools.size("local") == 1
    assert pools.size("web") == pools.POOL_SPECS["web"][1]
    monkeypatch.delenv("AGOS_POOL_LLM", raising=False)
    assert pools.get("llm")._max_workers == 2


def test_context_variables_are_carried_like_to_thread():
    var = contextvars.ContextVar("r132", default="unset")

    async def go():
        var.set("request-42")
        return await pools.run("local", var.get)

    assert asyncio.run(go()) == "request-42"


def test_unknown_pool_is_an_error_and_snapshot_lists_created_pools():
    with pytest.raises(KeyError):
        pools.get("gpu")
    asyncio.run(pools.run("net", lambda: None))
    assert set(pools.snapshot()) == {"net"}


def test_default_call_sites_use_named_pools(monkeypatch):
    seen = []
    real = pools.run

    async def spy(name, fn, *a, **k):
        seen.append(name)
        return await real(name, fn, *a, **k)

    monkeypatch.setattr(pools, "run", spy)
    asyncio.run(rd._to_thread(lambda: 1))
    asyncio.run(rd._to_thread(lambda: 1, _pool="web"))
    assert seen == ["local", "web"]


def test_capped_queue_fails_fast_instead_of_waiting(monkeypatch):
    monkeypatch.setenv("AGOS_POOL_WEB", "1")
    monkeypatch.setenv("AGOS_POOL_WEB_QUEUE", "2")

    async def go():
        held = [asyncio.create_task(pools.run("web", time.sleep, 0.5)) for _ in range(3)]  # 1 running + 2 queued
        await asyncio.sleep(0.05)
        t = time.monotonic()
        with pytest.raises(pools.PoolBusy, match="web pool busy: 2 call"):
            await pools.run("web", lambda: None)
        dt = time.monotonic() - t
        await asyncio.gather(*held)
        return dt

    before = pools.stats["busy"]["web"]
    assert asyncio.run(go()) < 0.05
    assert pools.stats["busy"]["web"] == before + 1


def test_uncapped_pools_still_queue(monkeypatch):
    monkeypatch.setenv("AGOS_POOL_NET", "1")
    monkeypatch.delenv("AGOS_POOL_NET_QUEUE", raising=False)
    monkeypatch.delenv("AGOS_POOL_WEB_QUEUE", raising=False)
    assert pools.queue_cap("net") == 0 and pools.queue_cap("web") == 0  # caps are opt-in

    async def go():
        return await asyncio.gather(*(pools.run("net", time.sleep, 0.05) for _ in range(5)))

    assert asyncio.run(go()) == [None] * 5


def test_educator_reports_busy_web_pool_honestly(monkeypatch):
    async def busy(*a, **k):
        raise pools.PoolBusy("web pool busy: 8 call(s) already waiting for 4 thread(s) (cap AGOS_POOL_WEB_QUEUE=8)")

    async def no_llm(*a, **k):
        return {"ok": False, "error": "keyless LLM busy: 6 request(s) ahead"}

    monkeypatch.setattr(rd, "_to_thread", busy)
    monkeypatch.setattr(rd, "_llm_text", no_llm)
    out = asyncio.run(rd._educator({"prompt": "explain the difference between REST and GraphQL"}))
    assert not out["ok"] and "web pool busy" in out["error"] and "keyless LLM busy" in out["error"]


def test_wait_cancelled_while_queued_never_runs(monkeypatch):
    monkeypatch.setenv("AGOS_POOL_WEB", "1")
    ran = []

    async def go():
        blocker = asyncio.create_task(pools.run("web", time.sleep, 0.4))
        await asyncio.sleep(0.05)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(pools.run("web", ran.append, "late"), timeout=0.1)
        await blocker
        await asyncio.sleep(0.1)

    asyncio.run(go())
    assert ran == []


def test_educator_web_wait_is_bounded(monkeypatch):
    monkeypatch.setattr(rd, "WEB_WAIT_S", 0.2)

    def slow_search(*a, **k):
        time.sleep(1.0)
        return []

    async def no_llm(*a, **k):
        return {"ok": False, "error": "keyless LLM busy: 6 request(s) ahead"}

    monkeypatch.setattr(rd, "_web_search", slow_search)
    monkeypatch.setattr(rd, "_llm_text", no_llm)
    t = time.monotonic()
    out = asyncio.run(rd._educator({"prompt": "explain the difference between REST and GraphQL"}))
    assert time.monotonic() - t < 1.5
    assert not out["ok"] and "no result within 0s" in out["error"]
