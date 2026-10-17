"""R-157 (D-069): contract / parity tests for overlapping capabilities.

Matrix: docs/governance/roadmaps/revival-scale/maps/CAPABILITY_CONTRACTS_228b956.md

C1  agent runtime — ``real`` (RealSpecialistAgent) vs ``specialists``
    (SpecialistBackedAgent): the same reference cases run on both; outcomes,
    errors, stats, status and memory/learning side effects must match except
    for the documented ``INTENDED_DIFFERENCES``.
C2  civilization health — ``/health`` (registry check) vs ``/health/ready``
    vs ``/api/system/status``: one source of truth (the server's live
    civilization) and the same verdict for the same state.
C3  memory & learning — ``experience`` (tenant-scoped facade, canonical on
    the live path) vs the kernel engine singletons (``get_memory_system``,
    ``get_learning_runtime``): layered, not parallel; pinned so a caller can
    not silently move to the un-tenanted singleton.
C4  messaging — ``agent_civilization.communication`` is the same object as
    ``core.agents.communication_hub`` (re-export, not a second implementation).
C5  agent selection — three policies exist; pinned as they are (no
    unification in R-157, see the matrix, gap G-157-3).
C6  frontends — ``src/lib/api.ts`` paths vs server routes (Q-002 stays open).
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from agent_civilization import communication
from agent_civilization.agents.real import SPECIALIZATIONS, create_civilization
from agent_civilization.agents.real import real_agents, runtime_contract
from agent_civilization.agents.real.specialist_runtime import SpecialistBackedAgent
from agent_civilization.core import experience, safety, tenancy
from agent_civilization.core.agents import communication_hub, real_dispatcher
from agent_civilization.pipeline import orchestrator
from agos_kernel.infrastructure import health

ROOT = Path(__file__).resolve().parent.parent

# Reference cases: one deterministic input per specialization (offline guard of
# conftest answers network calls). Same table as tests/test_specialist_runtime.py
# plus refusals that must be identical (test_runner without the code-exec
# opt-in, validator with invalid JSON, empty and None work).
REFERENCE = {
    "educator": {"prompt": "Recursion is when a function calls itself. Every recursion needs a base case."},
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
EDGE_CASES = [
    ("validator", {"prompt": "not json"}),        # handler refusal (ok=False, no exception)
    ("analyst", {"prompt": "avg", "data": []}),   # empty input
    ("analyst", {}),                              # no prompt at all
    ("analyst", None),                            # work=None (server never sends it; both accept)
    ("api_integrator", {"prompt": "GET http://127.0.0.1:1/"}),  # SSRF refusal (SEC-01)
]
VOLATILE = {"elapsed_s", "elapsed_ms", "duration_ms", "duration_s", "timestamp", "fetched_at", "latency_ms"}
SECRET = "internal secret /etc/passwd token=abc123"


# live measurements (monitor reads the process, network_client times DNS):
# the key must exist on both runtimes, its value is per-run
MEASURED = re.compile(r"(_ms|_s|_kb|_mb|_pct)$|^loadavg$")


def _scrub(v):
    if isinstance(v, dict):
        return {k: ("<measured>" if MEASURED.search(k) else _scrub(x))
                for k, x in v.items() if k not in VOLATILE}
    if isinstance(v, list):
        return [_scrub(x) for x in v]
    if isinstance(v, str):
        # timing / resource figures embedded in text are per-run
        return re.sub(r"\d+\.\d+", "<n>", v)
    return v


def _comparable(outcome):
    out = {k: v for k, v in outcome.items() if k not in ("agent", "impl", "elapsed_s")}
    return json.loads(json.dumps(_scrub(out), sort_keys=True, default=str))


@pytest.fixture
def runtimes():
    return {"real": create_civilization(1, impl="real"),
            "specialists": create_civilization(1, impl="specialists")}


def _run(agent, work):
    return asyncio.run(agent.execute(work))


@pytest.fixture
def raising_dispatch(monkeypatch):
    """Make the shared dispatcher raise/return what a test needs — on BOTH paths
    (real_agents binds ``_dispatch`` at import; the specialist templates import
    ``real_dispatcher.dispatch`` at call time)."""
    def install(fn):
        monkeypatch.setattr(real_agents, "_dispatch", fn)
        monkeypatch.setattr(real_dispatcher, "dispatch", fn)
    return install


# ============================================================ C1 agent runtime
def test_c1_both_runtimes_satisfy_the_canonical_contract(runtimes):
    for impl, civ in runtimes.items():
        assert sorted(civ) == sorted(SPECIALIZATIONS), impl
        for spec, pool in civ.items():
            a = pool[0]
            assert isinstance(a, runtime_contract.AgentRuntime), (impl, spec)
            assert a.specialization == spec and a.status == "idle"
            assert set(a.stats) == runtime_contract.STATS_KEYS
            snap = a.snapshot()
            assert runtime_contract.SNAPSHOT_KEYS <= set(snap)
            extra = set(snap) - runtime_contract.SNAPSHOT_KEYS
            assert extra == (set() if impl == "real" else {"impl"}), (impl, extra)
            assert set(snap["stats"]) == runtime_contract.STATS_KEYS


@pytest.mark.parametrize("spec", sorted(SPECIALIZATIONS))
def test_c1_reference_case_same_outcome_on_both_runtimes(spec, runtimes):
    work = REFERENCE.get(spec, {"prompt": "demo"})
    real = _run(runtimes["real"][spec][0], dict(work))
    spc = _run(runtimes["specialists"][spec][0], dict(work))
    assert set(real) == runtime_contract.OUTCOME_KEYS
    assert set(spc) == runtime_contract.OUTCOME_KEYS | {"impl"}
    assert _comparable(real) == _comparable(spc)


@pytest.mark.parametrize("spec,work", EDGE_CASES, ids=[f"{s}-{i}" for i, (s, _) in enumerate(EDGE_CASES)])
def test_c1_edge_and_refusal_cases_match(spec, work, runtimes):
    real = _run(runtimes["real"][spec][0], work if work is None else dict(work))
    spc = _run(runtimes["specialists"][spec][0], work if work is None else dict(work))
    assert real["ok"] is False or spec == "analyst"  # refusals stay refusals
    assert _comparable(real) == _comparable(spc)


def test_c1_ssrf_refusal_is_kept_on_both(runtimes):
    for impl in runtimes:
        r = _run(runtimes[impl]["api_integrator"][0], {"prompt": "GET http://127.0.0.1:1/"})
        assert r["ok"] is False, impl
        assert r["result"]["ok"] is False and r["result"].get("error"), (impl, r)


def test_c1_unexpected_exception_reports_type_only_on_both(runtimes, raising_dispatch):
    """Negative / security: the raw message of an unexpected exception never
    reaches the result (R-149). Before R-157 the specialists runtime returned
    'RuntimeError: <message>' (076_r157_runtime_parity_before.log)."""
    async def boom(spec, work):
        raise RuntimeError(SECRET)
    raising_dispatch(boom)
    for impl in runtimes:
        a = runtimes[impl]["analyst"][0]
        r = _run(a, {"prompt": "avg", "data": [1, 2]})
        assert r["ok"] is False
        assert r["result"] == {"ok": False, "error": "RuntimeError"}, (impl, r)
        assert SECRET not in json.dumps(r), impl
        assert "elapsed_s" in r and a.status == "idle"
        assert a.stats["tasks_failed"] == 1 and a.stats["tasks_completed"] == 0


def test_c1_unsafe_url_error_keeps_its_fixed_message_on_both(runtimes, raising_dispatch):
    msg = "url rejected: host resolves to a private address"

    async def unsafe(spec, work):
        raise safety.UnsafeURLError(msg)
    raising_dispatch(unsafe)
    for impl in runtimes:
        r = _run(runtimes[impl]["api_integrator"][0], {"prompt": "GET http://10.0.0.1/"})
        assert r["result"] == {"ok": False, "error": msg}, (impl, r)


def test_c1_no_handler_is_the_documented_intended_difference(runtimes, raising_dispatch):
    async def none(spec, work):
        return None
    raising_dispatch(none)
    r = _run(runtimes["real"]["analyst"][0], {"prompt": "x"})
    s = _run(runtimes["specialists"]["analyst"][0], {"prompt": "x"})
    assert r["ok"] is False and s["ok"] is False
    assert r["result"]["error"] == "no handler for analyst"
    assert s["result"]["error"] == "no real dispatcher for analyst"
    assert "no_handler_message" in runtime_contract.INTENDED_DIFFERENCES


def test_c1_specialist_instance_bookkeeping_is_preserved(runtimes, raising_dispatch):
    """The adapter still drives the wrapped specialist instance: its own
    performance_metrics and status move exactly as BaseAgent.execute moved them."""
    a: SpecialistBackedAgent = runtimes["specialists"]["validator"][0]
    pm = a.inner.performance_metrics
    _run(a, {"prompt": '{"a":1}'})
    _run(a, {"prompt": "not json"})
    assert (pm["tasks_completed"], pm["tasks_failed"]) == (1, 1)

    async def boom(spec, work):
        raise ValueError(SECRET)
    raising_dispatch(boom)
    _run(a, {"prompt": "x"})
    assert (pm["tasks_completed"], pm["tasks_failed"]) == (1, 2)
    assert a.inner.status == "idle" and a.status == "idle"
    # the legacy BaseAgent.execute path itself is untouched (hub/_execute_work users)
    legacy = asyncio.run(a.inner.execute({"prompt": "x"}))
    assert legacy["ok"] is False and legacy["result"]["error"].startswith("ValueError: ")


def _experience_side_effects(agent, work, tenant):
    token = tenancy.set_tenant(tenant)
    try:
        out = _run(agent, work)
    finally:
        tenancy.reset_tenant(token)
    st = experience.stats(tenant)
    mem = [e.metadata for e in experience.memory()._memories.values() if e.metadata.get("tenant_id") == tenant]
    stored = [{k: v for k, v in md.items() if k not in ("agent", "elapsed_ms")} for md in mem]
    exp = out.get("experience")
    exp_shape = None if exp is None else {
        "recalled": len(exp["memory"]["recalled"]), "stored": exp["memory"]["stored_id"] is not None,
        "prior": exp["learning"]["prior"],
        "after": {k: v for k, v in exp["learning"]["after"].items() if k != "avg_duration_ms"}}
    learn = json.loads(json.dumps(st["learning"]))
    for s in learn["by_specialization"].values():
        for k in s["by_input_kind"].values():
            k.pop("avg_duration_ms", None)
    return {"memories": st["memories"], "stored": stored, "learning": learn, "experience": exp_shape}


@pytest.mark.parametrize("case", ["success", "refusal", "exception"])
def test_c1_memory_and_learning_side_effects_match(case, runtimes, raising_dispatch):
    spec, work = {"success": ("analyst", {"prompt": "avg", "data": [1, 2, 3]}),
                  "refusal": ("validator", {"prompt": "not json"}),
                  "exception": ("analyst", {"prompt": "avg", "data": [1, 2, 3]})}[case]
    if case == "exception":
        async def boom(s, w):
            raise RuntimeError(SECRET)
        raising_dispatch(boom)
    effects = {}
    for impl in runtimes:
        experience.reset()
        effects[impl] = _experience_side_effects(runtimes[impl][spec][0], dict(work), "t-r157")
    assert effects["real"] == effects["specialists"], effects
    if case == "exception":
        # canonical: a raising handler stores nothing (run_with_experience does
        # not complete) — before R-157 specialists stored the leaked message
        assert effects["real"]["memories"] == 0 and effects["real"]["experience"] is None
        assert SECRET not in json.dumps(effects)
    else:
        assert effects["real"]["memories"] == 1


def test_c1_no_tenant_no_memory_on_both(runtimes):
    for impl in runtimes:
        experience.reset()
        r = _run(runtimes[impl]["analyst"][0], {"prompt": "avg", "data": [1, 2]})
        assert "experience" not in r and experience.stats(tenancy.ALL_TENANTS)["memories"] == 0


def test_c1_public_error_rule():
    assert runtime_contract.public_error(RuntimeError(SECRET)) == "RuntimeError"
    assert runtime_contract.public_error(KeyError("k")) == "KeyError"
    assert runtime_contract.public_error(safety.UnsafeURLError("url rejected: x")) == "url rejected: x"
    assert runtime_contract.failure_result(OSError(SECRET)) == {"ok": False, "error": "OSError"}


# ============================================================ C2 civilization health
@pytest.fixture
def client():
    with TestClient(server.app) as c:
        yield c


def _views(c):
    h, r, s = c.get("/health"), c.get("/health/ready"), c.get("/api/system/status")
    return h, r, s


def test_c2_health_views_agree_on_the_live_civilization(client, monkeypatch):
    small = {spec: create_civilization(2)[spec] for spec in list(SPECIALIZATIONS)[:3]}
    monkeypatch.setattr(server, "CIVILIZATION", small)
    h, r, s = _views(client)
    civ = h.json()["checks"]["civilization"]
    assert civ["status"] == "healthy"
    assert civ["details"]["agent_count"] == 6
    assert r.status_code == 200 and r.json()["agents"] == 6
    assert s.json()["total_agents"] == 6


def test_c2_default_server_counts_match(client):
    total = sum(len(p) for p in server.CIVILIZATION.values())
    h, r, s = _views(client)
    assert h.json()["checks"]["civilization"]["details"]["agent_count"] == total
    assert r.json() == {"status": "ready", "agents": total, "specializations": len(SPECIALIZATIONS)}
    assert s.json()["total_agents"] == total


@pytest.mark.parametrize("civ,reason", [({}, "Civilization not initialized"),
                                         ({"analyst": []}, "No agents available")])
def test_c2_not_ready_state_is_unhealthy_in_both_views(civ, reason, client, monkeypatch):
    """Negative: before R-157 /health built its own 1,000-agent civilization and
    said 'healthy' while /health/ready said 503 (077_r157_health_views_before.log)."""
    monkeypatch.setattr(server, "CIVILIZATION", civ)
    h, r, _ = _views(client)
    assert r.status_code == 503 and r.json() == {"status": "not_ready", "reason": reason}
    assert h.status_code == 503 and h.json()["status"] == "unhealthy"
    c = h.json()["checks"]["civilization"]
    assert c["status"] == "unhealthy" and c["message"] == reason


def test_c2_broken_civilization_does_not_leak_in_either_view(client, monkeypatch):
    class Broken(dict):
        def values(self):
            raise RuntimeError(SECRET)
    monkeypatch.setattr(server, "CIVILIZATION", Broken(a=[1]))
    # (/api/system/status is not a health view: it lets the error surface as a
    # generic 500 — out of scope here)
    h, r = client.get("/health"), client.get("/health/ready")
    assert r.status_code == 503 and r.json()["reason"] == "readiness check failed"
    assert h.status_code == 503
    assert SECRET not in h.text and SECRET not in r.text
    assert h.json()["checks"]["civilization"]["message"] == "readiness check failed"


def test_c2_server_health_does_not_build_a_second_civilization(client, monkeypatch):
    import agent_civilization.agents.real as real_pkg

    def forbidden(*a, **k):
        raise AssertionError("health built a new civilization")
    monkeypatch.setattr(real_pkg, "create_civilization", forbidden)
    assert client.get("/health").json()["checks"]["civilization"]["status"] == "healthy"


def test_c2_standalone_registry_keeps_legacy_behaviour(monkeypatch):
    """Without a registered provider (health used outside server.py) the check
    still builds the default civilization, exactly as before R-157."""
    monkeypatch.setattr(health, "_civilization_provider", None)
    out = asyncio.run(health.check_civilization())
    assert out == {"status": "healthy", "message": "Civilization active with 1000 agents", "agent_count": 1000}


def test_c2_civilization_status_rule():
    assert health.civilization_status({}) == {"ready": False, "reason": "Civilization not initialized", "agents": 0}
    assert health.civilization_status(None) == {"ready": False, "reason": "Civilization not initialized", "agents": 0}
    assert health.civilization_status({"a": []}) == {"ready": False, "reason": "No agents available", "agents": 0}
    assert health.civilization_status({"a": [1, 2], "b": [3]}) == {"ready": True, "reason": None, "agents": 3}


# ============================================================ C3 memory & learning
def test_c3_live_path_uses_the_tenant_facade_not_kernel_singletons(runtimes, monkeypatch):
    from agos_kernel.agent_production import learning_runtime, memory_system
    k_mem, k_learn = memory_system.get_memory_system(), learning_runtime.get_learning_runtime()
    before = (len(k_mem._memories), dict(k_learn._global_patterns))
    for impl in runtimes:
        token = tenancy.set_tenant("t-c3")
        try:
            _run(runtimes[impl]["analyst"][0], {"prompt": "avg", "data": [4, 5]})
        finally:
            tenancy.reset_tenant(token)
    assert experience.stats("t-c3")["memories"] == 2
    assert experience.memory() is not k_mem
    assert (len(k_mem._memories), dict(k_learn._global_patterns)) == before


def test_c3_facade_search_matches_engine_contents_per_tenant(runtimes):
    for tenant, data in (("t-a", [1, 2]), ("t-b", [3, 4])):
        token = tenancy.set_tenant(tenant)
        try:
            _run(runtimes["real"]["analyst"][0], {"prompt": "avg numbers", "data": data})
        finally:
            tenancy.reset_tenant(token)
    for tenant in ("t-a", "t-b"):
        via_facade = {m["memory_id"] for m in asyncio.run(experience.search("", scope=tenant))}
        in_engine = {mid for mid, e in experience.memory()._memories.items() if e.metadata.get("tenant_id") == tenant}
        assert via_facade == in_engine and len(in_engine) == 1
    with pytest.raises(Exception):
        asyncio.run(experience.search("", scope=None))  # no scope -> refused (SEC-02)


# ============================================================ C4 messaging
def test_c4_public_messaging_api_is_the_implementation():
    assert communication.CommunicationHub is communication_hub.CommunicationHub
    assert communication.DeliveryResult is communication_hub.DeliveryResult
    civ = create_civilization(1, impl="specialists")
    hubs = {id(pool[0].inner.communication_hub) for pool in civ.values()}
    assert len(hubs) == 1  # one shared hub for the runtime (R-054)


# ============================================================ C5 agent selection (pinned, not unified)
class _A:
    def __init__(self, name, done=0, failed=0, status="idle"):
        self.name, self.status = name, status
        self.stats = {"tasks_completed": done, "tasks_failed": failed, "total_time_s": 0.0}


def test_c5_selection_policies_are_pinned_as_they_are(monkeypatch):
    pool = [_A("a0", done=5), _A("a1", done=1), _A("a2", done=0, failed=3), _A("a3", done=0)]
    monkeypatch.setattr(server, "CIVILIZATION", {"analyst": pool})
    # server.py: first idle agent in pool order (load is ignored)
    assert server._pick_agent("analyst").name == "a0"
    # pipeline: least-loaded idle agent (completed + failed), reserved at once
    picked = asyncio.run(orchestrator._pick(pool))
    assert picked.name == "a3" and picked.status == "working"
    # server.py external: fewest completed tasks among registered external agents
    ext = [_A("e0", done=2), _A("e1", done=1)]
    monkeypatch.setattr(server, "EXTERNAL_AGENTS", ext)
    assert server._pick_agent("external").name == "e1"
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        server._pick_agent("nope")


# ============================================================ C6 frontends (Q-002 open)
def test_c6_src_client_paths_exist_on_the_server_under_api_prefix():
    """src/lib/api.ts builds paths relative to a user-set base. Every path it
    calls exists on the server under /api (path compatibility only — auth and
    payload drift are recorded as gap G-157-4; Q-002 is NOT decided here)."""
    text = (ROOT / "src/lib/api.ts").read_text(encoding="utf-8")
    calls = set(re.findall(r'request<[^>]*>\(\s*[`"](/[a-z/_]+)', text, re.S))
    assert calls >= {"/system/status", "/agents", "/specializations", "/tasks", "/chat"}
    routes = {r.path for r in server.app.routes if hasattr(r, "path")}
    for p in {c.rstrip("/") for c in calls}:
        assert "/api" + p in routes or ("/api" + p + "/{task_id}") in routes, p
    assert (ROOT / "web/src/api.js").exists() and (ROOT / "src/router.tsx").exists()
