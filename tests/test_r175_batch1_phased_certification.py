"""R-175 batch 1: the phased certification pipeline — the red-first acceptance tests.

They are imported against the missing module first (``ImportError`` on main =
the red), then each scenario walks the governed path via the PUBLIC API:
``reg.register``, ``reg.update_status``, ``reg.record_test_outcome`` — never
private attributes.
"""
from __future__ import annotations

import pytest

from agent_civilization.core import tool_certification, tool_runtime
from agent_civilization.core.tool_certification import (
    PHASE_CEILINGS, PhaseCeilingRefused, certify, certify_batch, state_counts)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AGOS_TOOL_REGISTRY_DB", str(tmp_path / "tools.sqlite3"))
    monkeypatch.delenv("AGOS_DISABLED_TOOLS", raising=False)
    tool_runtime.reset_runtime_registry()
    yield str(tmp_path / "tools.sqlite3")
    tool_runtime.reset_runtime_registry()


@pytest.fixture()
def fresh_reg(tmp_path):
    """A DIRECT empty registry (no runtime seeding): state-count tests assert
    exact numbers, and the runtime's lazily seeded internal tools would
    pollute them. Closed on teardown — no leaked file handles."""
    from agent_civilization.core.tool_registry_store import PersistentToolRegistry
    reg = PersistentToolRegistry(str(tmp_path / "r175.sqlite3"))
    yield reg
    reg.close()


@pytest.fixture()
def keys():
    import secrets
    return {"TENANT": "t-" + secrets.token_urlsafe(24),
            "ADM": "adm-" + secrets.token_urlsafe(24)}


@pytest.fixture()
def client(monkeypatch, keys, db):
    import hashlib
    import importlib
    from fastapi.testclient import TestClient

    def _sha(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    monkeypatch.setenv("AGOS_TENANT_KEYS", __import__("json").dumps([
        {"tenant": "tenant_a", "key_sha256": _sha(keys["TENANT"]), "role": "user"},
        {"tenant": "ops", "key_sha256": _sha(keys["ADM"]), "role": "admin"},
    ]))
    monkeypatch.setenv("AGOS_RATE_LIMIT_REQUESTS", "100000")
    import server
    srv = importlib.reload(server)
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        yield c
    monkeypatch.delenv("AGOS_TENANT_KEYS", raising=False)
    monkeypatch.delenv("AGOS_RATE_LIMIT_REQUESTS", raising=False)
    importlib.reload(server)


# -- the module's declared surface --------------------------------------------------

def test_module_declares_the_spec_phases():
    assert PHASE_CEILINGS == (100, 1000, 10_000)


def test_state_counts_declares_every_number_separately(fresh_reg):
    reg = fresh_reg
    reg.register(_record("disc.1", lifecycle_state="discovered"), actor="test:r175")
    reg.register(_record("tool.a"), actor="test:r175")
    reg.update_status("tool.a", "1.0.0", actor="test:r175",
                      lifecycle_state="validated")
    reg.register(_record("tool.b"), actor="test:r175")
    reg.update_status("tool.b", "1.0.0", actor="test:r175",
                      lifecycle_state="validated")
    for step in ("contract_pass", "integration_pass", "certified"):
        reg.record_test_outcome("tool.b", "1.0.0", actor="test:r175", to=step,
                                evidence="smoke contract security")
    reg.update_status("tool.b", "1.0.0", actor="test:r175",
                      lifecycle_state="certified")
    reg.update_status("tool.b", "1.0.0", actor="test:r175",
                      lifecycle_state="disabled")
    reg.register(_record("tool.c"), actor="test:r175")
    reg.update_status("tool.c", "1.0.0", actor="test:r175",
                      lifecycle_state="failed")
    reg.register(_record("tool.d"), actor="test:r175")          # stays registered
    reg.register(_record("tool.e"), actor="test:r175")          # -> deprecated
    reg.update_status("tool.e", "1.0.0", actor="test:r175",
                      lifecycle_state="validated")
    for step in ("contract_pass", "integration_pass", "certified"):
        reg.record_test_outcome("tool.e", "1.0.0", actor="test:r175", to=step,
                                evidence="smoke contract security")
    reg.update_status("tool.e", "1.0.0", actor="test:r175",
                      lifecycle_state="certified")
    reg.update_status("tool.e", "1.0.0", actor="test:r175",
                      lifecycle_state="deprecated-compatible")

    counts = state_counts(reg)
    assert counts["lifecycle"] == {"discovered": 1, "registered": 1,
                                   "validated": 1, "certified": 0,
                                   "deprecated-compatible": 1, "disabled": 1,
                                   "failed": 1}
    # tool.b was moved certified -> disabled: the certified number is honest.
    assert counts["lifecycle"]["certified"] == 0
    # test_status is independent of lifecycle: tool.b and tool.e both carry
    # their certified test evidence even though neither IS certified now.
    assert counts["test_status"] == {"not_tested": 4, "contract_pass": 0,
                                     "integration_pass": 0, "certified": 2,
                                     "failed": 0, "blocked": 0}
    assert counts["unique_tools"] == 6


def test_state_counts_counts_unique_tool_ids_not_versions(fresh_reg):
    reg = fresh_reg
    reg.register(_record("tool.a"), actor="test:r175")
    reg.register(_record("tool.a", version="1.1.0"), actor="test:r175")
    counts = state_counts(reg)
    assert counts["unique_tools"] == 1
    assert counts["lifecycle"]["registered"] == 1


def test_aliases_are_not_counted_as_tools(db):
    reg = _reg()
    reg.register(_record("tool.a"), actor="test:r175")
    before = state_counts(reg)
    with pytest.raises(Exception):
        reg.register(_record("tool.a", "1.0.0", name="The analyst",
                             label="Core analyzer tool",
                             description="Alias must be refused",
                             specialization="analyst",
                             input_schema={"type": "object"},
                             output_schema={"type": "object"}, capable=True),
                     actor="test:r175")
    assert state_counts(reg) == before


def test_discovered_records_never_count_as_certified(fresh_reg):
    reg = fresh_reg
    reg.register(_record("disc.1", lifecycle_state="discovered"), actor="test:r175")
    assert state_counts(reg)["lifecycle"]["certified"] == 0


# -- the governed walk -----------------------------------------------------------

def _evidence():
    return "smoke pass; contract v1.0 verified; security: allowlist+timeout"


def test_certify_walks_the_governed_path(db):
    reg = _reg()
    reg.register(_record("tool.a"), actor="test:r175")
    rec = certify(reg, "tool.a", "1.0.0", actor="test:r175", evidence=_evidence())
    assert rec.lifecycle_state == "certified"
    assert rec.test_status == "certified"
    assert state_counts(reg)["lifecycle"]["certified"] == 1


def test_certify_refuses_discovery_records(fresh_reg):
    reg = fresh_reg
    reg.register(_record("disc.1", lifecycle_state="discovered"), actor="test:r175")
    with pytest.raises(Exception):
        certify(reg, "disc.1", "1.0.0", actor="test:r175", evidence=_evidence())
    assert state_counts(reg)["lifecycle"]["certified"] == 0


def test_certify_is_idempotent_and_consumes_one_slot(db):
    reg = _reg()
    reg.register(_record("tool.a"), actor="test:r175")
    first = certify(reg, "tool.a", "1.0.0", actor="test:r175", evidence=_evidence())
    second = certify(reg, "tool.a", "1.0.0", actor="test:r175", evidence=_evidence())
    assert second == first
    assert state_counts(reg)["lifecycle"]["certified"] == 1


def test_certify_evidence_requirements_flow_through(db):
    reg = _reg()
    reg.register(_record("tool.a"), actor="test:r175")
    with pytest.raises(Exception) as ei:
        certify(reg, "tool.a", "1.0.0", actor="test:r175",
                evidence="runs twice, output looks reasonable")
    assert "security" in str(ei.value)
    assert state_counts(reg)["lifecycle"]["certified"] == 0


# -- the phase gate: fail-closed BEFORE any mutation ------------------------------

def test_phase_one_ceiling_is_100_and_the_101st_is_refused_before_any_mutation(db):
    reg = _reg()
    for i in range(100):
        reg.register(_record(f"tool.{i:03d}"), actor="test:r175")
        certify(reg, f"tool.{i:03d}", "1.0.0", actor="test:r175",
                evidence=_evidence())
    assert state_counts(reg)["lifecycle"]["certified"] == 100
    # the 101st: registered, its lifecycle untouched by the refusal.
    reg.register(_record("tool.100"), actor="test:r175")
    with pytest.raises(PhaseCeilingRefused) as ei:
        certify(reg, "tool.100", "1.0.0", actor="test:r175", evidence=_evidence())
    assert "100" in str(ei.value) and "owner decision" in str(ei.value)
    after = state_counts(reg)
    assert after["lifecycle"]["certified"] == 100
    rec = reg.get("tool.100", "1.0.0")
    assert rec.lifecycle_state == "registered"  # not half-walked
    assert rec.test_status == "not_tested"


def test_phase_two_raises_the_ceiling_to_1000(db):
    reg = _reg()
    for i in range(100):
        reg.register(_record(f"tool.{i:03d}"), actor="test:r175")
        certify(reg, f"tool.{i:03d}", "1.0.0", actor="test:r175",
                evidence=_evidence())
    reg.register(_record("tool.100"), actor="test:r175")
    with pytest.raises(PhaseCeilingRefused):
        certify(reg, "tool.100", "1.0.0", actor="test:r175", evidence=_evidence())
    # the operator declares phase 2 (an owner decision, recorded in D-125):
    rec = certify(reg, "tool.100", "1.0.0", actor="test:r175",
                  evidence=_evidence(), phase=2)
    assert rec.lifecycle_state == "certified"
    assert state_counts(reg)["lifecycle"]["certified"] == 101


def test_registration_is_not_phase_gated(fresh_reg):
    reg = fresh_reg
    for i in range(105):
        reg.register(_record(f"tool.{i:03d}"), actor="test:r175")
    assert state_counts(reg)["unique_tools"] == 105
    assert state_counts(reg)["lifecycle"]["certified"] == 0


def test_certify_rejects_an_out_of_range_phase(db):
    reg = _reg()
    reg.register(_record("tool.a"), actor="test:r175")
    with pytest.raises(ValueError):
        certify(reg, "tool.a", "1.0.0", actor="test:r175",
                evidence=_evidence(), phase=0)
    with pytest.raises(ValueError):
        certify(reg, "tool.a", "1.0.0", actor="test:r175",
                evidence=_evidence(), phase=4)


# -- the safe stop -----------------------------------------------------------------

def test_certify_batch_stops_safely_on_the_first_error(db):
    reg = _reg()
    items = [{"tool_id": f"tool.{i:02d}", "version": "1.0.0",
              "evidence": _evidence()} for i in range(5)]
    for item in items[:3]:
        reg.register(_record(item["tool_id"]), actor="test:r175")
    # item[2] is poisoned: no 'security' evidence -> the registry refuses.
    items[2]["evidence"] = "smoke contract only"
    report = certify_batch(reg, items, actor="test:r175")
    assert report["certified"] == ["tool.00@1.0.0", "tool.01@1.0.0"]
    assert report["stopped_at"] == {"tool_id": "tool.02", "version": "1.0.0"}
    assert report["error_type"] == "TransitionRefused"
    assert "security" in report["error"]
    # item 2 is stuck mid-walk (validated, contract_pass) — reported honestly,
    # items 3-4 untouched (they were never even registered):
    rec = reg.get("tool.02", "1.0.0")
    assert rec.lifecycle_state == "validated"
    assert rec.test_status == "integration_pass"
    with pytest.raises(Exception):
        reg.get("tool.03", "1.0.0")


def test_certify_batch_reports_a_clean_run(db):
    reg = _reg()
    items = []
    for i in range(3):
        reg.register(_record(f"tool.{i:02d}"), actor="test:r175")
        items.append({"tool_id": f"tool.{i:02d}", "version": "1.0.0",
                      "evidence": _evidence()})
    report = certify_batch(reg, items, actor="test:r175")
    assert report["certified"] == ["tool.00@1.0.0", "tool.01@1.0.0",
                                   "tool.02@1.0.0"]
    assert report["stopped_at"] is None
    assert state_counts(reg)["lifecycle"]["certified"] == 3


# -- the admin declaration surface --------------------------------------------------

def test_admin_api_declares_the_certification_counts(client, keys):
    r = client.get("/api/admin/tools", headers={"X-API-Key": keys["ADM"]})
    assert r.status_code == 200
    counts = r.json()["certification_counts"]
    assert counts["lifecycle"]["certified"] >= 0
    assert counts["test_status"]["contract_pass"] >= 0
    assert counts["unique_tools"] >= 0
    # non-admin stays refused:
    r = client.get("/api/admin/tools", headers={"X-API-Key": keys["TENANT"]})
    assert r.status_code == 403


# -- shared fixtures ----------------------------------------------------------------

def _reg():
    from agent_civilization.core import tool_runtime
    return tool_runtime.runtime_registry()


def _record(tool_id: str, version: str = "1.0.0", **overrides):
    base = {"tool_id": tool_id, "version": version,
            "name": f"{tool_id} core tool",
            "description": f"{tool_id} executable tool",
            "source": {"kind": "internal", "uri": "agos.internal:r175"},
            "capabilities": ["text-generation"],
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
            "auth_scope": [], "tenant_scope": "request_tenant",
            "risk_class": "low", "side_effects": [], "read_only": True,
            "idempotent": True, "requires_approval": False, "timeout_ms": 30_000,
            "network_allowlist": [], "health_status": "healthy",
            "test_status": "not_tested",
            "implementation_ref": "agent_civilization.core.r175:"
                                   + tool_id.replace(".", "_"),
            "lifecycle_state": "registered"}
    base.update(overrides)
    return base


def test_r175_batch1_doc_reference():
    """The batch number is declared in the module docstring (D-125)."""
    import inspect
    doc = inspect.getdoc(tool_certification)
    assert "R-175 batch 1" in doc
