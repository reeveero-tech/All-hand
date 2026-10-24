"""R-032 (D-037): opt-in runtime backed by the specialists/ package.

AGOS_AGENT_IMPL=specialists → create_civilization builds agents from the
1,000 specialist classes. Default (real) is unchanged. These tests cover
both paths, the BaseAgent ok-propagation fix, the pipeline, the live server
routes, and messaging between specialist instances.
"""
import asyncio
import importlib

import pytest
from fastapi.testclient import TestClient

from agent_civilization.agents.real import SPECIALIZATIONS, create_civilization
from agent_civilization.agents.real.real_agents import RealSpecialistAgent
from agent_civilization.agents.real.specialist_runtime import SpecialistBackedAgent
from agent_civilization.core.agents.base_agent import BaseAgent
from agent_civilization.pipeline.orchestrator import execute_plan
from agent_civilization.routing.auto_router import route

PAYLOADS = {
    # R-116: educator needs material (>=2 sentences) offline; "demo" is refused honestly.
    "educator": {"prompt": "Recursion is when a function calls itself. Every recursion needs a base case."},
    # R-170 (batch 12): the four deterministic text handlers need a task they can
    # actually do; "demo" is now refused honestly (task_not_completed, no hollow ok).
    "strategist": {"prompt": "Turn these into a roadmap, in this order: write docs; fix the bug; add tests"},
    "architect": {"prompt": "Design an architecture with a REST API and a database."},
    "designer": {"prompt": "Create a colour palette based on #1e88e5."},
    "reviewer": {"prompt": "Review this code:", "data": "def avg(xs):\n    return sum(xs) / len(xs)\n"},
    "analyst": {"prompt": "avg", "data": [1, 2, 3, 4]},
    "data_processor": {"prompt": "sum", "data": [1, 2, 3, 4]},
    "db_manager": {"prompt": "CREATE TABLE t(x INTEGER)"},
    "test_runner": {"prompt": "print(1+1)"},
    "validator": {"prompt": '{"a":1}'},
    "code_generator": {"prompt": "generate code", "data": "def add(a,b):\n    return a+b"},
    "modifier": {"prompt": "normalize", "data": "import os\nprint( os.getcwd() )"},
    "surgeon": {"prompt": "diagnose", "data": "def f( x ):\n    return  x"},
    "api_integrator": {"prompt": "GET https://example.com"},
    "network_client": {"prompt": "check connectivity to example.com"},
}


def test_default_impl_is_unchanged(monkeypatch):
    monkeypatch.delenv("AGOS_AGENT_IMPL", raising=False)
    civ = create_civilization(2)
    assert all(isinstance(a, RealSpecialistAgent) for pool in civ.values() for a in pool)


def test_unknown_impl_fails_loudly():
    with pytest.raises(ValueError):
        create_civilization(1, impl="bogus")


def test_env_selects_specialists(monkeypatch):
    monkeypatch.setenv("AGOS_AGENT_IMPL", "specialists")
    civ = create_civilization(1)
    a = civ["analyst"][0]
    assert isinstance(a, SpecialistBackedAgent)
    assert isinstance(a.inner, BaseAgent)


def test_target_count_uses_all_1000_specialist_classes():
    civ = create_civilization(50, impl="specialists")
    agents = [a for pool in civ.values() for a in pool]
    assert len(agents) == 1000
    assert len({a.name for a in agents}) == 1000
    # every one of the 1,000 classes is actually instantiated (50 distinct per spec)
    assert len({a.impl_class for a in agents}) == 1000
    for spec in SPECIALIZATIONS:
        assert all(a.inner.specialization == spec for a in civ[spec])


@pytest.mark.code_exec  # R-100: proves real test_runner execution (operator opt-in)
def test_every_specialization_executes_through_specialist_class():
    civ = create_civilization(1, impl="specialists")

    async def run():
        return [(s, await civ[s][0].execute(PAYLOADS.get(s, {"prompt": "demo"})))
                for s in SPECIALIZATIONS]

    for spec, r in asyncio.run(run()):
        assert r["ok"], f"{spec}: {r['result']}"
        assert r["impl"].lower().startswith(spec.replace("_", ""))


def test_base_agent_execute_propagates_failure():
    """Discriminating: before the fix BaseAgent.execute returned ok=True on failure."""
    civ = create_civilization(1, impl="specialists")
    a = civ["validator"][0]
    raw = asyncio.run(a.inner.execute({"prompt": "not json"}))
    assert raw["ok"] is False
    r = asyncio.run(a.execute({"prompt": "not json"}))
    assert r["ok"] is False and "invalid JSON" in r["result"]["error"]
    assert a.stats["tasks_failed"] == 1 and a.stats["tasks_completed"] == 0


@pytest.mark.code_exec  # R-100: proves real test_runner execution (operator opt-in)
def test_pipeline_runs_on_specialist_civilization():
    civ = create_civilization(5, impl="specialists")
    result = asyncio.run(execute_plan(route("review and test this code: print(1+1)"),
                                      civ, data="print(1+1)"))
    assert all(s["ok"] for s in result["stages"]), result["stages"]
    assert "exit=0" in result["final"]


@pytest.mark.asyncio
async def test_specialists_exchange_messages_via_shared_hub():
    from agent_civilization.core.agents.base_agent import Message
    civ = create_civilization(1, impl="specialists")
    a, b = civ["analyst"][0].inner, civ["builder"][0].inner
    assert a.communication_hub is b.communication_hub
    a.start()
    b.start()
    try:
        res = await a.communication_hub.send_message(
            Message(sender=a.name, recipient=b.name, content={"action": "status_request"}))
        assert res.success
        for _ in range(50):
            await asyncio.sleep(0.02)
            if b.stats["msgs"] and a.stats["msgs"]:
                break
        assert b.stats["msgs"] == 1, "b did not receive the request"
        assert a.stats["msgs"] >= 1, "a did not receive b's status reply"
    finally:
        await a.shutdown_async()
        await b.shutdown_async()


@pytest.fixture
def specialists_server(monkeypatch):
    import server as srv
    monkeypatch.delenv("AGOS_API_KEY", raising=False)
    monkeypatch.setenv("AGOS_AGENT_IMPL", "specialists")
    importlib.reload(srv)
    yield srv
    monkeypatch.delenv("AGOS_AGENT_IMPL", raising=False)
    importlib.reload(srv)  # restore default runtime for later tests


def test_live_server_routes_with_specialists(specialists_server, request):
    srv = specialists_server
    agent = srv.CIVILIZATION["analyst"][0]
    assert isinstance(agent, SpecialistBackedAgent)
    auth = request.getfixturevalue("tenant_auth")  # SEC-02: after the reload
    c = TestClient(srv.app, headers=auth.headers)
    r = c.post("/civilization/execute",
               json={"message": "analyze the data for trends", "data": [1, 2, 3, 4, 5]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert all(s["ok"] for s in d["stages"])
    stored = c.get(f"/api/tasks/{d['task_id']}").json()
    assert stored["status"] == "completed"


# --------------------------------------------- R-105 (dedup, 1000/1000)
def test_r105_specialists_impl_still_builds_1000(monkeypatch):
    """R-105: AGOS_AGENT_IMPL=specialists still builds 1000/1000 — the same
    acceptance evidence as docs/audit/M7/logs/08 (R-032): construction AND
    execution of 1000 tasks, with the ok count asserted (not just agents built)."""
    import asyncio
    monkeypatch.setenv("AGOS_AGENT_IMPL", "specialists")
    monkeypatch.setenv("AGOS_ALLOW_CODE_EXEC", "1")  # R-100/D-044: test_runner needs it
    civ = create_civilization(50)
    total = sum(len(v) for v in civ.values())
    assert total == 1000
    # every agent is on the shared hub with its specialization set
    for spec, agents in civ.items():
        assert len(agents) == 50
        for a in agents:
            assert a.specialization == spec

    # AND the 1000 tasks actually execute (the historical 1000/1000 acceptance)
    PAYLOADS = {
        # R-116: educator needs material (>=2 sentences) offline; "demo" is refused honestly.
        "educator": {"prompt": "Recursion is when a function calls itself. Every recursion needs a base case."},
        # R-170 (batch 12): the four deterministic text handlers need a task they can
        # actually do; "demo" is now refused honestly (task_not_completed, no hollow ok).
        "strategist": {"prompt": "Turn these into a roadmap, in this order: write docs; fix the bug; add tests"},
        "architect": {"prompt": "Design an architecture with a REST API and a database."},
        "designer": {"prompt": "Create a colour palette based on #1e88e5."},
        "reviewer": {"prompt": "Review this code:", "data": "def avg(xs):\n    return sum(xs) / len(xs)\n"},
        "analyst": {"prompt": "avg", "data": [1, 2, 3, 4]},
        "data_processor": {"prompt": "sum", "data": [1, 2, 3, 4]},
        "db_manager": {"prompt": "CREATE TABLE t(x INTEGER)"},
        "network_client": {"prompt": "check connectivity to example.com"},
        "test_runner": {"prompt": "print(1+1)"},
        "validator": {"prompt": '{"a":1}'},
        "api_integrator": {"prompt": "GET https://example.com"},
        "code_generator": {"data": "def hello():\n    return 1"},
        "surgeon": {"data": "def f():\n    try:\n        pass\n    except:\n        pass\n"},
        "modifier": {"data": "x = 1   \n"},
    }

    async def run_all():
        tasks = []
        for spec, agents in civ.items():
            for a in agents:
                payload = PAYLOADS.get(spec, {"prompt": "demo"})
                tasks.append(a.execute(payload))
        return await asyncio.gather(*tasks)

    results = asyncio.run(run_all())
    ok_count = sum(1 for r in results if r.get("ok"))
    assert ok_count == 1000, f"expected 1000/1000, got {ok_count}/1000"
