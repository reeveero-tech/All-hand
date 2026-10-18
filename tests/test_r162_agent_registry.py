"""R-162 (D-081): standard agent registry — identity / worker / task separated,
registration, health, liveness, capacity, saturation, versions, drain/stop.

Acceptance (P3_AGENT_COORDINATION_AR.md §R-162): 10k logical agents; unique
registration; removing/stopping a worker leaves no stuck task; memory/CPU
measured (the measurements themselves: scripts/r162_registry_scale.py).

Evidence class: unit (registry with an injected clock) and local integration
through the real server task flow (httpx ASGITransport, in process).
"""
from __future__ import annotations

import asyncio
import multiprocessing
import os
import threading
import time
import tracemalloc

import pytest

try:  # the locked dev deps ship httpx2 (as tests/test_r160_contracts.py)
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    import httpx

from agent_civilization.core import agent_registry as AR
from agent_civilization.core import contracts as C
from agent_civilization.core.agent_registry import (
    AgentRegistry, DuplicateRegistration, InvalidTransition, LeaseError, NotAssignable,
    Saturated, UnknownAgent, UnknownWorker)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _reg(**kw):
    clock = Clock()
    return AgentRegistry(clock=clock, **kw), clock


def _ready(reg, wid="w1", capacity=4, pool="p"):
    reg.register_worker(wid, pool, capacity)
    reg.mark_ready(wid)
    return wid


# ------------------------------------------------------------ registration

def test_registration_is_unique_by_id_name_and_worker():
    reg, _ = _reg()
    reg.register_agent("a1", "alpha", "analyst")
    with pytest.raises(DuplicateRegistration):
        reg.register_agent("a1", "other", "analyst")
    with pytest.raises(DuplicateRegistration):
        reg.register_agent("a2", "alpha", "analyst")
    _ready(reg, "w1")
    with pytest.raises(DuplicateRegistration):
        reg.register_worker("w1", "p", 1)
    for bad in ("", "  "):
        with pytest.raises(ValueError):
            reg.register_agent(bad, "n", "s")
    for cap in (0, -1, True, 1.5):
        with pytest.raises(ValueError):
            reg.register_worker("wx", "p", cap)
    assert reg.snapshot()["agents"] == 1 and reg.snapshot()["workers"] == 1


def test_identity_worker_and_task_are_separate_records():
    reg, _ = _reg()
    reg.register_agent("a1", "alpha", "analyst", version="3", capabilities=("statistics",))
    reg.register_agent("b1", "beta", "builder")
    _ready(reg, "w1", capacity=8)
    la, lb = reg.acquire("t1", "a1"), reg.acquire("t2", "b1")
    # one worker serves identities of different specializations
    assert la.worker_id == lb.worker_id == "w1"
    assert (la.task_id, la.agent_id, la.agent_version) == ("t1", "a1", "3")
    ident = reg.agent("a1")
    assert not hasattr(ident, "worker_id") and not hasattr(ident, "task_id")
    assert not hasattr(reg.worker("w1"), "specialization")
    assert reg.leases_of("w1") == ["t1", "t2"]
    with pytest.raises(LeaseError):
        reg.acquire("t1", "b1")                 # a task holds one lease


# ---------------------------------------------------------- worker lifecycle

def test_worker_lifecycle_transitions_are_enforced():
    reg, _ = _reg()
    reg.register_worker("w", "p", 1)
    assert reg.worker("w").state == "starting" and reg.worker_health("w") == "starting"
    with pytest.raises(InvalidTransition):
        reg.drain("w")                          # starting -> draining is not allowed
    reg.mark_ready("w")
    reg.drain("w")
    with pytest.raises(InvalidTransition):
        reg.mark_ready("w")                     # draining -> ready is not allowed
    reg.stop("w")
    for op in (reg.mark_ready, reg.drain, reg.stop, reg.heartbeat):
        with pytest.raises(InvalidTransition):
            op("w")                             # a stopped worker stays stopped
    with pytest.raises(UnknownWorker):
        reg.heartbeat("nope")


def test_liveness_health_and_reap_of_a_silent_worker():
    reg, clock = _reg(heartbeat_ttl_s=10)
    reg.register_agent("a", "a", "s")
    _ready(reg, "w", capacity=1)
    assert reg.is_live("w") and reg.worker_health("w") == "healthy"
    reg.acquire("t1", "a")
    assert reg.worker_health("w") == "saturated"
    clock.t += 10
    assert reg.is_live("w")                     # exactly the TTL is still live
    clock.t += 0.5
    assert not reg.is_live("w") and reg.worker_health("w") == "unresponsive"
    out = reg.reap()
    assert [(o.worker_id, o.task_ids, o.reason) for o in out] == [("w", ["t1"], "worker missed its heartbeat")]
    assert reg.worker("w").state == "dead" and reg.lease("t1") is None
    assert reg.snapshot()["leases_active"] == 0 and reg.counters["reaped_workers"] == 1
    with pytest.raises(InvalidTransition):
        reg.heartbeat("w")                      # a heartbeat does not revive a dead worker


def test_heartbeat_keeps_a_worker_live():
    reg, clock = _reg(heartbeat_ttl_s=5)
    _ready(reg, "w")
    for _ in range(10):
        clock.t += 4
        reg.heartbeat("w")
    assert reg.is_live("w") and reg.reap() == []


def test_an_unresponsive_worker_gets_no_new_lease():
    reg, clock = _reg(heartbeat_ttl_s=5)
    reg.register_agent("a", "a", "s")
    _ready(reg, "w")
    clock.t += 6
    with pytest.raises(Saturated):
        reg.acquire("t", "a")


# --------------------------------------------------- capacity and saturation

def test_capacity_saturation_and_worker_choice():
    reg, _ = _reg()
    for i in range(3):
        reg.register_agent(f"a{i}", f"a{i}", "s")
    _ready(reg, "small", capacity=1)
    _ready(reg, "big", capacity=2)
    assert reg.saturation() == 0.0
    l1 = reg.acquire("t1", "a0")
    assert l1.worker_id == "big"                # the most free slots first
    reg.acquire("t2", "a1")
    reg.acquire("t3", "a2")
    assert reg.saturation() == 1.0
    with pytest.raises(Saturated):
        reg.acquire("t4", "a0")
    assert reg.counters["saturated"] == 1 and reg.lease("t4") is None
    reg.release("t2")
    assert reg.saturation() == round(2 / 3, 4)
    assert reg.acquire("t4", "a0").task_id == "t4"
    with pytest.raises(LeaseError):
        reg.release("t2")                        # a lease ends once


def test_pool_filter():
    reg, _ = _reg()
    reg.register_agent("a", "a", "s")
    _ready(reg, "w1", pool="web", capacity=1)
    _ready(reg, "w2", pool="local", capacity=1)
    assert reg.acquire("t1", "a", pool="local").worker_id == "w2"
    with pytest.raises(Saturated):
        reg.acquire("t2", "a", pool="local")
    assert reg.saturation("web") == 0.0 and reg.saturation("local") == 1.0
    assert reg.saturation("none") is None


# --------------------------------------------------- drain / stop / remove

def test_drain_takes_no_new_lease_but_running_ones_finish():
    reg, _ = _reg()
    reg.register_agent("a", "a", "s")
    _ready(reg, "w", capacity=5)
    reg.acquire("t1", "a")
    reg.drain("w")
    assert reg.worker_health("w") == "draining"
    with pytest.raises(Saturated):
        reg.acquire("t2", "a")
    reg.release("t1")
    assert reg.stop("w").task_ids == []


def test_stop_and_remove_return_every_running_task_as_orphan():
    reg, _ = _reg()
    reg.register_agent("a", "a", "s")
    _ready(reg, "w1", capacity=3)
    _ready(reg, "w2", capacity=3)
    tids = [f"t{i}" for i in range(6)]
    for t in tids:
        reg.acquire(t, "a")
    on_w1 = reg.leases_of("w1")
    assert len(on_w1) == 3
    out = reg.remove_worker("w1")              # removed while holding leases
    assert out.task_ids == on_w1 and out.reason == "worker removed"
    with pytest.raises(UnknownWorker):
        reg.worker("w1")
    assert all(reg.lease(t) is None for t in on_w1)
    left = reg.stop("w2")
    assert sorted(left.task_ids + out.task_ids) == sorted(tids)
    assert reg.snapshot()["leases_active"] == 0 and reg.counters["orphaned"] == 6
    assert reg.remove_worker("w2").task_ids == []   # already stopped: nothing left


def test_lease_expiry_is_reported_not_silent():
    reg, clock = _reg(lease_ttl_s=30, heartbeat_ttl_s=1000)
    reg.register_agent("a", "a", "s")
    _ready(reg, "w")
    reg.acquire("t1", "a")
    reg.acquire("t2", "a")
    clock.t += 20
    reg.renew("t2")
    clock.t += 15
    out = reg.reap()
    assert [(o.reason, o.task_ids) for o in out] == [("lease expired", ["t1"])]
    assert reg.lease("t2") is not None and reg.counters["expired"] == 1


# ----------------------------------------------------- versions and states

def test_upgrade_keeps_running_leases_on_their_version():
    reg, _ = _reg()
    reg.register_agent("a", "a", "s", version="1")
    _ready(reg, "w")
    old = reg.acquire("t1", "a")
    reg.upgrade("a", "2")
    new = reg.acquire("t2", "a")
    assert (old.agent_version, new.agent_version) == ("1", "2")
    assert reg.versions("a") == ["1", "2"]
    with pytest.raises(DuplicateRegistration):
        reg.upgrade("a", "1")                    # a version is never re-registered
    reg.retire_agent("a")
    with pytest.raises(InvalidTransition):
        reg.upgrade("a", "3")


def test_disabled_and_retired_identities_are_not_assignable():
    reg, _ = _reg()
    reg.register_agent("a", "a", "s")
    reg.register_agent("b", "b", "s")
    _ready(reg, "w", capacity=10)
    held = reg.acquire("t0", "a")
    reg.disable_agent("a")
    with pytest.raises(NotAssignable):
        reg.acquire("t1", "a")
    assert reg.select_agent("s").agent_id == "b"
    reg.release(held.task_id)                    # a running lease still ends normally
    reg.enable_agent("a")
    reg.retire_agent("b")
    assert reg.select_agent("s").agent_id == "a"
    with pytest.raises(InvalidTransition):
        reg.enable_agent("b")
    reg.retire_agent("a")
    with pytest.raises(UnknownAgent):
        reg.select_agent("s")
    with pytest.raises(UnknownAgent):
        reg.agent("zz")


def test_select_is_least_loaded():
    reg, _ = _reg()
    for i in range(3):
        reg.register_agent(f"a{i}", f"a{i}", "s")
    _ready(reg, "w", capacity=100)
    picked = []
    for i in range(9):
        a = reg.select_agent("s").agent_id
        picked.append(a)
        reg.acquire(f"t{i}", a)
    assert sorted(picked.count(a) for a in ("a0", "a1", "a2")) == [3, 3, 3]
    for i in range(3):                            # free a1 completely
        tid = next(t for t in reg.leases_of("w") if reg.lease(t).agent_id == "a1")
        reg.release(tid)
    assert reg.select_agent("s").agent_id == "a1"


def test_positive_int_env(monkeypatch):
    monkeypatch.delenv("R162_X", raising=False)
    assert AR.positive_int_env("R162_X", 7) == 7
    monkeypatch.setenv("R162_X", "12")
    assert AR.positive_int_env("R162_X", 7) == 12
    for bad in ("abc", "0", "-3", "1000001"):
        monkeypatch.setenv("R162_X", bad)
        with pytest.raises(RuntimeError):
            AR.positive_int_env("R162_X", 7)


def test_snapshot_has_counts_only():
    reg, _ = _reg()
    reg.register_agent("secret-agent-name", "secret-agent-name", "s")
    _ready(reg, "w")
    reg.acquire("task-SECRET", "secret-agent-name")
    text = repr(reg.snapshot())
    assert "SECRET" not in text and "secret-agent-name" not in text


# ------------------------------------------------------------- 10k logical

def _threads():
    return len(os.listdir("/proc/self/task")) if os.path.isdir("/proc/self/task") else threading.active_count()


def test_ten_thousand_logical_agents_without_a_thread_or_process_each():
    """10,000 identities from the real civilization builder (20 x 500), one
    lease each on 10 workers, then half the workers stop: every task comes
    back exactly once, none is left attached. No thread or process is created
    per agent; memory and time are bounded and measured."""
    from agent_civilization.agents.real import create_civilization

    threads0, children0 = _threads(), len(multiprocessing.active_children())
    tracemalloc.start()
    t0, c0 = time.perf_counter(), time.process_time()
    civ = create_civilization(500, impl="real")
    reg = AgentRegistry()
    n = AR.register_civilization(reg, civ, version="real")
    for i in range(10):
        reg.register_worker(f"w{i}", "inproc", 1000)
        reg.mark_ready(f"w{i}")
    tids = []
    for spec in civ:
        for _ in range(500):
            a = reg.select_agent(spec)
            tid = f"t{len(tids)}"
            reg.acquire(tid, a.agent_id)
            tids.append(tid)
    elapsed, cpu = time.perf_counter() - t0, time.process_time() - c0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert n == 10_000 and reg.snapshot()["agents"] == 10_000 and len(reg._names) == 10_000
    assert reg.snapshot()["leases_active"] == 10_000 and reg.saturation() == 1.0
    assert {reg._load[a] for a in reg._agents} == {1}          # one task per identity
    with pytest.raises(Saturated):
        reg.acquire("extra", "analyst_0000")
    assert _threads() == threads0 and len(multiprocessing.active_children()) == children0

    orphaned = []
    for i in range(5):
        orphaned += reg.stop(f"w{i}").task_ids
    for i in range(5):
        orphaned += reg.remove_worker(f"w{i + 5}").task_ids
    assert sorted(orphaned) == sorted(tids) and len(set(orphaned)) == 10_000
    assert reg.snapshot()["leases_active"] == 0 and all(v == 0 for v in reg._load.values())

    assert peak < 64 * 1024 * 1024, peak        # measured ~ tens of MB incl. executors
    assert elapsed < 30 and cpu < 30, (elapsed, cpu)


# --------------------------------------------- local integration (server)

async def _asgi(tenant_auth):
    import server
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                             base_url="http://agos.test", headers=tenant_auth.headers)


async def _wait(server, tid, states=("completed", "failed")):
    for _ in range(300):
        if server.TASKS[tid]["status"] in states:
            return server.TASKS[tid]
        await asyncio.sleep(0.01)
    raise AssertionError(server.TASKS[tid])


@pytest.fixture
def slow_analyst(monkeypatch):
    import server
    gate = asyncio.Event()
    agent = server.CIVILIZATION["analyst"][0]

    async def slow(self, work):
        await gate.wait()
        return {"ok": True, "agent": self.name, "elapsed_s": 0, "result": {"value": 1}}
    for a in server.CIVILIZATION["analyst"]:
        monkeypatch.setattr(type(a), "execute", slow)
    return gate, agent


def test_server_registers_the_civilization_and_one_inproc_worker():
    import server
    snap = server.AGENT_REGISTRY.snapshot()
    total = sum(len(v) for v in server.CIVILIZATION.values())
    assert snap["agents"] >= total
    # an earlier lifespan in this process may have stopped the worker; serving
    # (lifespan startup or the next task) always starts from this call
    server._start_registry_worker()
    w = server.AGENT_REGISTRY.worker(server.WORKER_ID)
    assert w.pool == "inproc" and w.capacity == server.WORKER_CAPACITY and w.state == "ready"
    a = server.CIVILIZATION["analyst"][0]
    ident = server.AGENT_REGISTRY.agent(a.name)
    assert ident.specialization == "analyst" and ident.capabilities == ("statistics",)


async def test_a_running_task_holds_a_lease_and_releases_it(tenant_auth, slow_analyst):
    import server
    gate, _ = slow_analyst
    before = server.AGENT_REGISTRY.worker(server.WORKER_ID).in_flight
    async with await _asgi(tenant_auth) as c:
        tid = (await c.post("/api/tasks", json={"specialization": "analyst", "prompt": "x"})).json()["task_id"]
        await _wait(server, tid, ("running",))
        lease = server.AGENT_REGISTRY.lease(tid)
        assert lease is not None and lease.worker_id == server.WORKER_ID
        assert lease.agent_id == server.TASKS[tid]["agent"]
        assert server.AGENT_REGISTRY.worker(server.WORKER_ID).in_flight == before + 1
        gate.set()
        rec = await _wait(server, tid)
    assert rec["status"] == "completed"
    assert server.AGENT_REGISTRY.lease(tid) is None
    assert server.AGENT_REGISTRY.worker(server.WORKER_ID).in_flight == before


async def test_a_failing_run_also_releases_its_lease(tenant_auth, monkeypatch):
    import server

    async def broken(self, work):
        raise RuntimeError("boom")
    for a in server.CIVILIZATION["analyst"]:
        monkeypatch.setattr(type(a), "execute", broken)
    async with await _asgi(tenant_auth) as c:
        tid = (await c.post("/api/tasks", json={"specialization": "analyst", "prompt": "x"})).json()["task_id"]
        rec = await _wait(server, tid)
    assert rec["status"] == "failed" and server.AGENT_REGISTRY.lease(tid) is None


async def test_at_capacity_a_task_fails_explicitly_instead_of_waiting(tenant_auth, slow_analyst, monkeypatch):
    import server
    gate, _ = slow_analyst
    w = server.AGENT_REGISTRY.worker(server.WORKER_ID)
    monkeypatch.setattr(w, "capacity", w.in_flight + 1)
    async with await _asgi(tenant_auth) as c:
        t1 = (await c.post("/api/tasks", json={"specialization": "analyst", "prompt": "a"})).json()["task_id"]
        await _wait(server, t1, ("running",))
        t2 = (await c.post("/api/tasks", json={"specialization": "analyst", "prompt": "b"})).json()["task_id"]
        r2 = await _wait(server, t2)
        assert r2["status"] == "failed" and r2["result"]["result"] == {"error": server.WORKER_SATURATED}
        C.validate("task_record", (await c.get(f"/api/tasks/{t2}")).json())   # contract v1 unchanged
        assert server.AGENT_REGISTRY.lease(t2) is None
        gate.set()
        r1 = await _wait(server, t1)
    assert r1["status"] == "completed"


async def test_stopping_the_worker_ends_running_tasks_and_a_new_worker_starts(tenant_auth, slow_analyst):
    import server
    gate, _ = slow_analyst
    async with await _asgi(tenant_auth) as c:
        tid = (await c.post("/api/tasks", json={"specialization": "analyst", "prompt": "x"})).json()["task_id"]
        await _wait(server, tid, ("running",))
        old = server.WORKER_ID
        server._stop_registry_worker()          # what the lifespan does at shutdown
        rec = server.TASKS[tid]
        assert rec["status"] == "failed"
        assert rec["result"]["result"] == {"error": "task cancelled before completion: worker stopped"}
        C.validate("task_record", (await c.get(f"/api/tasks/{tid}")).json())
        assert server.AGENT_REGISTRY.lease(tid) is None
        assert server.AGENT_REGISTRY.worker(old).state == "stopped"
        server._start_registry_worker()         # what the lifespan does at startup
        assert server.WORKER_ID != old and server.AGENT_REGISTRY.worker(server.WORKER_ID).state == "ready"
        gate.set()
        await asyncio.sleep(0.05)
        assert server.TASKS[tid]["status"] == "failed"    # the late run does not resurrect it
        t2 = (await c.post("/api/tasks", json={"specialization": "analyst", "prompt": "y"})).json()["task_id"]
        assert (await _wait(server, t2))["status"] == "completed"


async def test_removed_external_agent_identity_is_disabled(tenant_auth, monkeypatch):
    import server
    from agent_civilization.agents.external_adapter import ExternalAgentAdapter
    ext = ExternalAgentAdapter("r162-ext", "http://127.0.0.1:9/v1/chat/completions", "m", "")
    monkeypatch.setattr(server, "EXTERNAL_AGENTS", [ext])
    aid = server._ensure_identity(ext)
    assert aid == "external:r162-ext" and server.AGENT_REGISTRY.agent(aid).state == "active"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://agos.test",
                                 headers=tenant_auth.admin_headers) as c:
        assert (await c.delete("/api/agents/external/r162-ext")).status_code == 200
    assert server.AGENT_REGISTRY.agent(aid).state == "disabled"
    server._ensure_identity(ext)                 # registered again later -> enabled
    assert server.AGENT_REGISTRY.agent(aid).state == "active"


async def test_a_draining_worker_takes_no_new_task_and_is_not_replaced(tenant_auth):
    import server
    wid = server.WORKER_ID
    server.AGENT_REGISTRY.drain(wid)
    try:
        async with await _asgi(tenant_auth) as c:
            tid = (await c.post("/api/tasks", json={"specialization": "analyst", "prompt": "x"})).json()["task_id"]
            rec = await _wait(server, tid)
        assert rec["status"] == "failed" and rec["result"]["result"] == {"error": "worker not available"}
        assert server.WORKER_ID == wid               # drain is respected, no new worker behind it
        assert server.AGENT_REGISTRY.lease(tid) is None
    finally:
        server.AGENT_REGISTRY.stop(wid)
        server._start_registry_worker()


def test_real_lifespan_stops_the_worker_and_a_later_task_gets_a_new_one(tenant_auth):
    """The real ASGI lifespan (TestClient context): shutdown stops the worker;
    the stopped worker is removed when the next generation starts (bounded
    table), and a task after shutdown runs on the new worker."""
    import server
    from fastapi.testclient import TestClient
    with TestClient(server.app, headers=tenant_auth.headers) as c:
        inside = server.WORKER_ID
        assert server.AGENT_REGISTRY.worker(inside).state == "ready"
    assert server.AGENT_REGISTRY.worker(inside).state == "stopped"
    workers_before = server.AGENT_REGISTRY.snapshot()["workers"]
    c = TestClient(server.app, headers=tenant_auth.headers)        # no lifespan
    tid = c.post("/api/tasks", json={"specialization": "analyst", "prompt": "analyze 1 2"}).json()["task_id"]
    for _ in range(300):
        if server.TASKS[tid]["status"] in ("completed", "failed"):
            break
        time.sleep(0.01)
    assert server.TASKS[tid]["status"] == "completed", server.TASKS[tid]
    assert server.WORKER_ID != inside
    with pytest.raises(UnknownWorker):
        server.AGENT_REGISTRY.worker(inside)
    assert server.AGENT_REGISTRY.snapshot()["workers"] == workers_before


def test_executors_without_a_name_get_a_positional_identity():
    class Nameless:
        pass
    reg = AgentRegistry()
    n = AR.register_civilization(reg, {"analyst": [Nameless(), Nameless()], "builder": [Nameless()]}, version="t")
    assert n == 3 and reg.agent("analyst#1").specialization == "analyst" and reg.agent("builder#0")
