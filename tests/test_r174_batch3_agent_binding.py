"""R-174 batch 3 (D-123): the selector bound into the agent execution path.

The contracts under test (roadmap P5_TOOL_ECOSYSTEM_AR.md, R-174; owner
directive: ربط المحدد بمسار تنفيذ الوكلاء — المتخصصون يشتقون TaskSpec من
عقد المهمة):

* a work item that carries ``work["tool_contract"]`` runs through the
  SELECTION layer in BOTH agent runtimes (real and specialists): the TaskSpec
  is derived from the contract, the chosen tool runs through the real gate,
  and the outcome carries the selection audit (chosen tool, every exclusion
  with its recorded reason, candidate count);
* the contract's PRESENCE is the opt-in: without it the legacy dispatch path
  is byte-for-byte unchanged (regression guard);
* a contracted task NEVER falls back to the legacy specialization handler —
  a malformed contract or an unsatisfiable one fails loudly with the reasons,
  it never executes a tool the caller did not declare;
* the agent envelope (experience/memory, R-170 output verification, stats,
  canonical outcome keys) applies around the selection result unchanged.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_civilization.agents.real.real_agents import RealSpecialistAgent
from agent_civilization.core import tool_runtime

DISPATCHER_URI = "agent_civilization/core/agents/real_dispatcher"
REF = f"{__name__}:"
CAP = "bind.search"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "tools.sqlite3")
    monkeypatch.setenv("AGOS_TOOL_REGISTRY_DB", path)
    monkeypatch.delenv("AGOS_DISABLED_TOOLS", raising=False)
    tool_runtime.reset_runtime_registry()
    yield path
    tool_runtime.reset_runtime_registry()


def _reg():
    return tool_runtime.runtime_registry()


def _tool(monkeypatch, spec, handler, capability=CAP, **fields):
    """Register a binding-test tool the honest way (batch-1 pattern): a real
    registry record whose implementation_ref names the module-level handler,
    and the frozen dispatcher's handler table pointing at the same object."""
    from agent_civilization.core.agents import real_dispatcher
    monkeypatch.setitem(real_dispatcher._HANDLERS, spec, handler)
    data = {
        "tool_id": f"agos.internal.{spec}",
        "name": spec,
        "version": "1.0.0",
        "source": {"kind": "internal", "uri": DISPATCHER_URI},
        "description": "binding-test tool",
        "capabilities": [capability],
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "auth_scope": [],
        "tenant_scope": "request_tenant",
        "risk_class": "low",
        "side_effects": [],
        "read_only": True,
        "idempotent": True,
        "requires_approval": False,
        "timeout_ms": 30_000,
        "network_allowlist": [],
        "health_status": "healthy",
        "test_status": "not_tested",
        "implementation_ref": f"{REF}{handler.__name__}",
        "lifecycle_state": "registered",
    }
    data.update(fields)
    return _reg().register(data, actor="test:r174-batch3")


def _certify(rec):
    ev = ("smoke, contract and security proof: binding run in "
          "tests/test_r174_batch3_agent_binding.py (evidence log 348)")
    for to in ("contract_pass", "integration_pass", "certified"):
        _reg().record_test_outcome(rec.tool_id, rec.version,
                                   actor="test:r174-batch3", to=to, evidence=ev)
    _reg().update_status(rec.tool_id, rec.version, actor="test:r174-batch3",
                        lifecycle_state="validated")
    return _reg().update_status(rec.tool_id, rec.version,
                               actor="test:r174-batch3", lifecycle_state="certified")


# -- module-level handlers (implementation_ref must resolve to them) --------

async def h_bind_healthy(work):
    return {"ok": True, "tool_output": "healthy-ran"}


async def h_bind_degraded(work):
    return {"ok": True, "tool_output": "degraded-ran"}


async def h_bind_scoped(work):
    return {"ok": True, "tool_output": "scoped-ran"}


async def h_bind_fails(work):
    return {"ok": False, "error": "boom"}


# -- helpers ---------------------------------------------------------------

def _contract(capability=CAP, **extra):
    c = {"capability": capability}
    c.update(extra)
    return {"prompt": "binding test", "tool_contract": c}


def _real():
    return RealSpecialistAgent(name="analyst_0001", specialization="analyst",
                              agent_id=1)


def _specialist():
    from agent_civilization.agents.real.specialist_runtime import (
        SpecialistBackedAgent, specialist_classes)
    cls = specialist_classes("analyst")[0]
    inner = cls(name="AnalystAgent0001", hub=None, agent_id=7)
    return SpecialistBackedAgent(inner, "analyst", 7)


# -- the binding (red on main: the contract is ignored today) --------------

@pytest.mark.parametrize("agent", [_real, _specialist], ids=["real", "specialists"])
def test_contracted_work_selects_and_runs_the_chosen_tool(db, monkeypatch, agent):
    healthy = _certify(_tool(monkeypatch, "bind.healthy", h_bind_healthy,
                             health_status="healthy"))
    degraded = _certify(_tool(monkeypatch, "bind.degraded", h_bind_degraded,
                              health_status="degraded"))
    out = run(agent().execute(_contract()))
    assert out["ok"] is True
    # the healthy candidate outranks the degraded one and ACTUALLY ran
    assert out["result"]["tool"] == healthy.tool_id
    assert out["result"]["result"]["tool_output"] == "healthy-ran"
    assert out["result"]["verified"] is True
    assert degraded.tool_id not in out["result"]["result"]["tool_output"]


@pytest.mark.parametrize("agent", [_real, _specialist], ids=["real", "specialists"])
def test_contracted_work_carries_the_selection_audit(db, monkeypatch, agent):
    # an eligible tool so the task succeeds, plus the scoped sibling whose
    # exclusion (with its recorded reason) must appear in the audit.
    _certify(_tool(monkeypatch, "bind.ok3", h_bind_healthy))
    _certify(_tool(monkeypatch, "bind.scoped", h_bind_scoped,
                   auth_scope=["secret:scope"]))
    out = run(agent().execute(_contract()))
    assert out["ok"] is True
    excluded = out["result"]["selection_excluded"]
    assert any(e["tool"] == "agos.internal.bind.scoped"
               and "scopes not granted" in e["reason"] for e in excluded)
    assert out["result"]["selection_candidates"] == 1
    # the scoped tool never ran (its handler is unreachable without a grant)
    stats = tool_runtime.stats()["executed"]
    assert stats.get("agos.internal.bind.scoped@1.0.0", 0) == 0


@pytest.mark.parametrize("agent", [_real, _specialist], ids=["real", "specialists"])
def test_contracted_task_never_falls_back_to_the_legacy_handler(db, monkeypatch, agent):
    # the legacy analyst handler IS reachable (the seed provides the record)
    # and the work is valid analyst work — a contracted task must NOT use it:
    # no tool satisfies the contract, the failure names the reasons, and the
    # specialization tool never executes.
    _tool(monkeypatch, "bind.toohigh", h_bind_healthy, risk_class="critical")
    out = run(agent().execute(_contract(max_risk="low")))
    assert out["ok"] is False
    assert out["result"]["exhausted"] is True
    assert any("risk_class=critical" in e["reason"]
               for e in out["result"]["selection_excluded"])
    assert tool_runtime.stats()["executed"].get("agos.internal.bind.toohigh@1.0.0", 0) == 0
    assert tool_runtime.stats()["executed"].get("agos.internal.analyst@1.0.0", 0) == 0


@pytest.mark.parametrize("agent", [_real, _specialist], ids=["real", "specialists"])
def test_malformed_contract_fails_loudly_not_silently_legacy(db, monkeypatch, agent):
    # the work is valid analyst work — on the legacy path it would succeed;
    # a malformed contract must fail LOUDLY (type only, R-149), never
    # silently downgrade to the undeclared legacy path.
    ag = agent()
    out = run(ag.execute({"prompt": "avg", "data": [1, 2, 3, 4],
                          "tool_contract": {"capability": ""}}))
    assert out["ok"] is False
    assert out["result"].get("error") == "ValueError"
    assert tool_runtime.stats()["executed"].get("agos.internal.analyst@1.0.0", 0) == 0
    assert ag.stats["tasks_failed"] >= 1


def test_fallback_runs_through_the_agent_path(db, monkeypatch):
    # the failing candidate ranks FIRST (certified test status beats the
    # working candidate's not_tested); it runs, fails, and being
    # read-only/idempotent the fallback to the healthy one is safe.
    _certify(_tool(monkeypatch, "bind.failsfirst", h_bind_fails))
    ok_one = _tool(monkeypatch, "bind.oksecond", h_bind_healthy)
    out = run(_real().execute(_contract()))
    assert out["ok"] is True
    assert out["result"]["tool"] == ok_one.tool_id
    assert out["result"]["result"]["tool_output"] == "healthy-ran"
    assert any(f["from"].endswith("bind.failsfirst")
               and f["to"] == ok_one.tool_id for f in out["result"]["fallbacks"])


# -- the envelope (regression guards around the binding) --------------------

def test_no_contract_means_legacy_path_unchanged(db):
    # the seeded analyst record + the REAL analyst handler, valid work — the
    # legacy path runs exactly as before and carries no selection fields.
    before = tool_runtime.stats()["executed"].get("agos.internal.analyst@1.0.0", 0)
    out = run(_real().execute({"prompt": "avg", "data": [1, 2, 3, 4]}))
    assert out["ok"] is True
    assert tool_runtime.stats()["executed"].get("agos.internal.analyst@1.0.0", 0) > before
    for k in ("selection_excluded", "selection_candidates", "verified"):
        assert k not in out["result"]


@pytest.mark.parametrize("agent", [_real, _specialist], ids=["real", "specialists"])
def test_contracted_outcome_keeps_the_canonical_agent_envelope(db, monkeypatch, agent):
    _certify(_tool(monkeypatch, "bind.healthy2", h_bind_healthy))
    ag = agent()
    out = run(ag.execute(_contract()))
    assert out["ok"] is True
    # canonical outcome keys (runtime_contract) + stats counted as completed
    for k in ("ok", "agent", "elapsed_s", "result"):
        assert k in out
    # the outcome IS a selection outcome (not the legacy dispatch result)
    assert "selection_excluded" in out["result"]
    assert out["result"]["verified"] is True
    assert ag.stats["tasks_completed"] == 1
    assert ag.stats["tasks_failed"] == 0


@pytest.mark.parametrize("agent", [_real, _specialist], ids=["real", "specialists"])
def test_empty_registry_contracted_task_fails_with_reason(db, monkeypatch, agent):
    out = run(agent().execute(_contract()))
    assert out["ok"] is False
    assert out["result"]["exhausted"] is True
    assert out["result"]["error"]


def test_derive_task_spec_rejects_unknown_contract_fields():
    from agent_civilization.core.task_contract import derive_task_spec
    with pytest.raises(ValueError, match="unknown fields"):
        derive_task_spec({"tool_contract": {"capability": "x", "grant_everything": True}})


def test_derive_task_spec_no_contract_is_none():
    from agent_civilization.core.task_contract import derive_task_spec
    assert derive_task_spec({"prompt": "no contract"}) is None
    assert derive_task_spec({}) is None


def test_derive_task_spec_reads_the_declaration():
    from agent_civilization.core.task_contract import derive_task_spec
    spec = derive_task_spec({"tool_contract": {
        "capability": "c", "top_k": 5, "max_risk": "high",
        "read_only": True, "granted_scopes": ["a", "b"],
        "timeout_budget_ms": 500, "allow_side_effects": True,
        "allowed_tenant_scopes": ["t1"]}})
    assert spec.capability == "c"
    assert spec.top_k == 5
    assert spec.max_risk == "high"
    assert spec.read_only is True
    assert spec.granted_scopes == ("a", "b")
    assert spec.timeout_budget_ms == 500
    assert spec.allow_side_effects is True
    assert spec.allowed_tenant_scopes == ("t1",)
