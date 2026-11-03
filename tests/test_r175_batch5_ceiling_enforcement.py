"""R-175 batch 5 (D-129): the phase ceiling is a REGISTRY rule — red-first.

The gap (declared in D-125's honest limits, PROVEN live on main b71abead
before this batch): the phase gate lived only inside ``certify()`` — a direct
front-door walk (``record_test_outcome`` x3 + ``update_status(...,
lifecycle_state="certified")``) certified tool #101 past the phase-1 ceiling
of 100 (reproduced: certified count=101). The fix is at the LOWEST layer
where a lifecycle transition to ``certified`` can happen — the registry
itself — so EVERY path enforces the ceiling:

* ``PHASE_CEILINGS`` / ``PhaseCeilingRefused`` move to ``tool_registry``
  (``tool_certification`` re-exports them unchanged — old imports keep working);
* the registry carries a DECLARED PHASE (default 1) with an audited front door
  ``declare_phase()``; ``update_status`` refuses any NEW transition to
  ``certified`` beyond the declared phase's ceiling (fail-closed before any
  mutation — a refused tool is never left half-walked);
* ``certify()`` keeps its documented D-125 interface: its ``phase=`` argument
  is the operator's explicit declaration and RAISES the registry's declared
  phase (audited; it never lowers it);
* the persistent store carries the declared phase in ``tool_meta`` (durable
  across processes and restarts); a fresh store defaults to phase 1;
* lowering the phase GRANDFATHERS already-certified tools (never silently
  decertifies): new certifications are simply refused until the count fits.
"""
from __future__ import annotations

import pytest

from agent_civilization.core.tool_registry import (PHASE_CEILINGS,
                                                   PhaseCeilingRefused)
from agent_civilization.core import tool_certification
from agent_civilization.core.tool_certification import certify, state_counts


@pytest.fixture()
def store(tmp_path):
    """A DIRECT empty persistent registry on a fresh store (no runtime
    seeding: the exact-count assertions need an empty registry)."""
    from agent_civilization.core.tool_registry_store import PersistentToolRegistry
    path = str(tmp_path / "r175b5.sqlite3")
    reg = PersistentToolRegistry(path)
    yield reg, path
    reg.close()


def _record(tool_id: str, version: str = "1.0.0") -> dict:
    return {"tool_id": tool_id, "version": version, "name": f"{tool_id} tool",
            "description": f"{tool_id} executable tool",
            "source": {"kind": "internal", "uri": "agos.internal:r175b5"},
            "capabilities": ["text-generation"],
            "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
            "auth_scope": [], "tenant_scope": "request_tenant", "risk_class": "low",
            "side_effects": [], "read_only": True, "idempotent": True,
            "requires_approval": False, "timeout_ms": 30_000,
            "network_allowlist": [], "health_status": "healthy",
            "test_status": "not_tested",
            "implementation_ref": "agent_civilization.core.r175b5:"
                                   + tool_id.replace(".", "_"),
            "lifecycle_state": "registered"}


def _evidence() -> str:
    return "smoke pass; contract v1.0 verified; security: allowlist+timeout"


def _walk_test_status(reg, tool_id: str, version: str = "1.0.0") -> None:
    """The governed test-status walk via the front door (never faked)."""
    for step in ("contract_pass", "integration_pass", "certified"):
        reg.record_test_outcome(tool_id, version, actor="test:r175b5", to=step,
                                evidence=_evidence())


# -- the red: the direct front-door walk can no longer bypass the ceiling ----

def test_direct_update_status_cannot_certify_past_the_phase_ceiling(store):
    reg, _ = store
    for i in range(100):
        reg.register(_record(f"tool.{i:03d}"), actor="test:r175b5")
        certify(reg, f"tool.{i:03d}", "1.0.0", actor="test:r175b5", evidence=_evidence())
    assert state_counts(reg)["lifecycle"]["certified"] == 100
    # tool.100 walks the SAME governed front doors certify() uses — then the
    # lifecycle move itself must be refused by the REGISTRY (not just certify).
    reg.register(_record("tool.100"), actor="test:r175b5")
    reg.update_status("tool.100", "1.0.0", actor="test:r175b5",
                      lifecycle_state="validated")
    _walk_test_status(reg, "tool.100")          # test_status: certified — allowed
    with pytest.raises(PhaseCeilingRefused) as ei:
        reg.update_status("tool.100", "1.0.0", actor="test:r175b5",
                          lifecycle_state="certified")
    assert "owner decision" in str(ei.value)
    # honest state: NOT certified, not half-walked past its real walk.
    rec = reg.get("tool.100", "1.0.0")
    assert rec.lifecycle_state == "validated"
    assert rec.test_status == "certified"
    assert state_counts(reg)["lifecycle"]["certified"] == 100


def test_the_ceiling_refusal_is_audited(store):
    reg, _ = store
    for i in range(100):
        reg.register(_record(f"tool.{i:03d}"), actor="test:r175b5")
        certify(reg, f"tool.{i:03d}", "1.0.0", actor="test:r175b5", evidence=_evidence())
    reg.register(_record("tool.100"), actor="test:r175b5")
    reg.update_status("tool.100", "1.0.0", actor="test:r175b5",
                      lifecycle_state="validated")
    _walk_test_status(reg, "tool.100")
    with pytest.raises(PhaseCeilingRefused):
        reg.update_status("tool.100", "1.0.0", actor="test:r175b5",
                          lifecycle_state="certified")
    entries = [e for e in reg.audit()
               if e["op"] == "update_refused" and e.get("reason") == "phase_ceiling"]
    assert len(entries) == 1
    assert entries[0]["tool_id"] == "tool.100"
    assert entries[0]["phase"] == 1 and entries[0]["ceiling"] == 100


# -- the declared phase: front door, audit, durability -------------------------

def test_a_fresh_store_defaults_to_phase_1(store):
    reg, _ = store
    assert reg.phase() == 1


def test_declare_phase_is_audited_and_durable_across_instances(store):
    reg, path = store
    reg.declare_phase(2, actor="test:r175b5")
    assert reg.phase() == 2
    entries = [e for e in reg.audit() if e["op"] == "phase_declared"]
    assert entries and entries[-1]["phase"] == 2
    reg.close()
    # a NEW instance on the same store (a new process re-reads the same file):
    from agent_civilization.core.tool_registry_store import PersistentToolRegistry
    reg2 = PersistentToolRegistry(path)
    try:
        assert reg2.phase() == 2
    finally:
        reg2.close()


def test_certify_phase_declaration_raises_the_registry_phase(store):
    """D-125's documented interface is preserved: the operator's ``phase=``
    declaration at the certify() call RAISES the registry's declared phase
    (audited) — so the governed path works exactly as before."""
    reg, _ = store
    for i in range(100):
        reg.register(_record(f"tool.{i:03d}"), actor="test:r175b5")
        certify(reg, f"tool.{i:03d}", "1.0.0", actor="test:r175b5", evidence=_evidence())
    reg.register(_record("tool.100"), actor="test:r175b5")
    rec = certify(reg, "tool.100", "1.0.0", actor="test:r175b5", evidence=_evidence(),
                  phase=2)
    assert rec.lifecycle_state == "certified"
    assert reg.phase() == 2
    assert state_counts(reg)["lifecycle"]["certified"] == 101


def test_declare_phase_rejects_out_of_range(store):
    reg, _ = store
    with pytest.raises(ValueError):
        reg.declare_phase(0, actor="test:r175b5")
    with pytest.raises(ValueError):
        reg.declare_phase(4, actor="test:r175b5")
    assert reg.phase() == 1


# -- grandfathering + idempotency ----------------------------------------------

def test_lowering_the_phase_grandfathers_certified_tools(store):
    reg, _ = store
    reg.declare_phase(2, actor="test:r175b5")
    for i in range(101):
        reg.register(_record(f"tool.{i:03d}"), actor="test:r175b5")
        certify(reg, f"tool.{i:03d}", "1.0.0", actor="test:r175b5",
                evidence=_evidence(), phase=2)
    reg.declare_phase(1, actor="test:r175b5")      # explicit operator action
    # the 101 certified stay certified (no silent decertification)...
    assert state_counts(reg)["lifecycle"]["certified"] == 101
    # ...and a NEW certification is refused under the lowered ceiling.
    reg.register(_record("tool.101"), actor="test:r175b5")
    reg.update_status("tool.101", "1.0.0", actor="test:r175b5",
                      lifecycle_state="validated")
    _walk_test_status(reg, "tool.101")
    with pytest.raises(PhaseCeilingRefused):
        reg.update_status("tool.101", "1.0.0", actor="test:r175b5",
                          lifecycle_state="certified")


def test_re_certifying_an_already_certified_tool_consumes_no_slot(store):
    reg, _ = store
    for i in range(100):
        reg.register(_record(f"tool.{i:03d}"), actor="test:r175b5")
        certify(reg, f"tool.{i:03d}", "1.0.0", actor="test:r175b5", evidence=_evidence())
    # an idempotent lifecycle no-op (already certified) is NOT a new slot:
    rec = reg.update_status("tool.000", "1.0.0", actor="test:r175b5",
                            lifecycle_state="certified")
    assert rec.lifecycle_state == "certified"
    assert state_counts(reg)["lifecycle"]["certified"] == 100


def test_certify_batch_still_stops_safely_at_the_ceiling(store):
    reg, _ = store
    for i in range(100):
        reg.register(_record(f"tool.{i:03d}"), actor="test:r175b5")
    items = [{"tool_id": f"tool.{i:03d}", "version": "1.0.0",
              "evidence": _evidence()} for i in range(101)]
    reg.register(_record("tool.100"), actor="test:r175b5")
    report = tool_certification.certify_batch(reg, items, actor="test:r175b5")
    assert report["certified"] == [f"tool.{i:03d}@1.0.0" for i in range(100)]
    assert report["stopped_at"] == {"tool_id": "tool.100", "version": "1.0.0"}
    assert report["error_type"] == "PhaseCeilingRefused"
    assert "owner decision" in report["error"]


# -- re-exports keep old imports working ---------------------------------------

def test_tool_certification_re_exports_the_canonical_names():
    from agent_civilization.core import tool_certification as tc
    assert tc.PHASE_CEILINGS == PHASE_CEILINGS
    assert tc.PhaseCeilingRefused is PhaseCeilingRefused
