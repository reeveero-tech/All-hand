"""R-171 batch 4 (D-114): a REAL health check and an admin API for the shared
tool registry.

Before this batch ``health_status`` was a hand-set field (the batch-2 gate
reads it, nothing measured it) and the only management surface was the host
CLI. These tests prove, against the real shared store and the real dispatch
gate, that:

* the health checker EXECUTES the safe tools (read-only, low risk, no side
  effects) through the production gate and marks them ``healthy`` only on an
  ok-shaped answer — an actual execution, not a metadata claim;
* tools with declared side effects (model, network, sandboxed execution,
  local metrics) are NEVER executed by the checker: static availability only,
  reported as ``degraded`` — honest, not optimistic;
* a tool the gate would refuse (missing handler) is ``unhealthy``, never
  healthy;
* a ``disabled`` tool is skipped, not probed and not overwritten — disabling
  is an operator decision;
* health writes are audited (actor ``health:checker``) and idempotent (an
  unchanged re-check writes nothing);
* the admin API is role-gated (admin only), lists the registry, runs the
  real health check, and its disable/enable reach the dispatch gate without a
  restart; an unavailable store is a 503 (fail-closed), never a silent empty
  answer.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import secrets

import pytest
from fastapi.testclient import TestClient

from agent_civilization.core import tool_runtime
from agent_civilization.core import tool_health
from agent_civilization.core.agents import real_dispatcher
from agent_civilization.core.tool_registry_store import RegistryStoreError

TID = "agos.internal.analyst"
PROBEABLE = {"surgeon", "validator", "analyst", "data_processor", "architect",
             "strategist", "reviewer", "designer", "builder"}
STATIC_ONLY = {"code_generator", "test_runner", "researcher", "network_client",
               "api_integrator", "educator", "communicator", "db_manager",
               "monitor", "modifier", "self_developer"}


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


def _sha(key: str) -> str:
    import hashlib
    return hashlib.sha256(key.encode()).hexdigest()


@pytest.fixture()
def keys():
    return {"TENANT": "t-" + secrets.token_urlsafe(24),
            "ADM": "adm-" + secrets.token_urlsafe(24)}


@pytest.fixture()
def client(monkeypatch, keys, db):
    monkeypatch.setenv("AGOS_TENANT_KEYS", json.dumps([
        {"tenant": "tenant_a", "key_sha256": _sha(keys["TENANT"]), "role": "user"},
        {"tenant": "ops", "key_sha256": _sha(keys["ADM"]), "role": "admin"},
    ]))
    monkeypatch.setenv("AGOS_RATE_LIMIT_REQUESTS", "100000")
    import server
    srv = importlib.reload(server)
    with TestClient(srv.app, raise_server_exceptions=False) as c:
        yield c
    for k in ("AGOS_TENANT_KEYS", "AGOS_RATE_LIMIT_REQUESTS"):
        monkeypatch.delenv(k, raising=False)
    importlib.reload(server)


def H(key):
    return {"X-API-Key": key}


# -- the health checker -----------------------------------------------------

def test_health_check_executes_safe_tools_and_marks_them_healthy(db):
    report = run(tool_health.run_health_check())
    for spec in PROBEABLE:
        key = f"agos.internal.{spec}@1.0.0"
        assert report["tools"][key]["probe"] == "execution", (key, report["tools"][key])
        assert report["tools"][key]["status"] == "healthy", (key, report["tools"][key])
    # the record itself was updated and audited
    reg = tool_runtime.runtime_registry()
    rec = reg.get("agos.internal.analyst")
    assert rec.health_status == "healthy"
    assert rec.last_verified_at is not None
    assert any(e["op"] == "update" and e["actor"] == "health:checker"
              and e["tool_id"] == "agos.internal.analyst" for e in reg.audit())


def test_side_effect_tools_are_static_only_and_degraded(db):
    report = run(tool_health.run_health_check())
    for spec in STATIC_ONLY:
        key = f"agos.internal.{spec}@1.0.0"
        assert report["tools"][key]["probe"] == "static", (key, report["tools"][key])
        assert report["tools"][key]["status"] == "degraded", (key, report["tools"][key])
    rec = tool_runtime.runtime_registry().get("agos.internal.code_generator")
    assert rec.health_status == "degraded"


def test_a_tool_the_gate_would_refuse_is_unhealthy(db, monkeypatch):
    monkeypatch.delitem(real_dispatcher._HANDLERS, "analyst", raising=False)
    report = run(tool_health.run_health_check())
    key = "agos.internal.analyst@1.0.0"
    assert report["tools"][key]["status"] == "unhealthy"
    assert "analyst" in report["tools"][key]["detail"]
    rec = tool_runtime.runtime_registry().get("agos.internal.analyst")
    assert rec.health_status == "unhealthy"


def test_disabled_tool_is_skipped_not_overwritten(db):
    reg = tool_runtime.runtime_registry()
    reg.update_status("agos.internal.analyst", reg.get("agos.internal.analyst").version,
                      actor="operator:test", lifecycle_state="disabled")
    report = run(tool_health.run_health_check())
    key = "agos.internal.analyst@1.0.0"
    assert report["tools"][key]["probe"] == "skipped"
    assert report["tools"][key]["status"] == "skipped_disabled"
    # the gate refuses it; the checker did not touch its health
    ok, _ = tool_runtime.check("analyst")
    assert not ok
    assert tool_runtime.runtime_registry().get("agos.internal.analyst").health_status == "unknown"


def test_health_check_is_idempotent_and_audited_once(db):
    reg = tool_runtime.runtime_registry()
    run(tool_health.run_health_check())
    first = [e for e in reg.audit() if e["op"] == "update"]
    report = run(tool_health.run_health_check())
    second = [e for e in reg.audit() if e["op"] == "update"]
    assert len(second) == len(first)  # unchanged health writes nothing
    assert report["tools"]["agos.internal.analyst@1.0.0"]["unchanged"] is True


def test_probeable_predicate_matches_the_seed_table(db):
    reg = tool_runtime.runtime_registry()
    probeable = {r.name for r in reg.search(limit=10_000) if tool_health.is_probeable(r)}
    assert probeable == PROBEABLE


# -- the admin API -----------------------------------------------------------

def test_admin_tools_endpoints_require_the_admin_role(client, keys):
    r = client.get("/api/admin/tools", headers=H(keys["TENANT"]))
    assert r.status_code == 403, r.text
    r = client.get("/api/admin/tools")
    assert r.status_code in (401, 403), r.text
    r = client.post("/api/admin/tools/health-check", headers=H(keys["TENANT"]))
    assert r.status_code == 403, r.text


def test_admin_list_returns_the_shared_registry(client, keys):
    r = client.get("/api/admin/tools", headers=H(keys["ADM"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 20
    assert "gate_stats" in body
    row = next(t for t in body["tools"] if t["tool_id"] == "agos.internal.analyst")
    assert row["lifecycle_state"] == "registered"
    assert row["health_status"] == "unknown"
    assert row["probeable"] is True
    assert any(t["probeable"] is False for t in body["tools"])


def test_admin_health_check_runs_the_real_check(client, keys):
    r = client.post("/api/admin/tools/health-check", headers=H(keys["ADM"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"]["healthy"] == len(PROBEABLE)
    assert body["summary"]["degraded"] == len(STATIC_ONLY)
    assert body["tools"]["agos.internal.analyst@1.0.0"]["probe"] == "execution"


def test_admin_disable_and_enable_reach_the_gate(client, keys):
    r = client.post("/api/admin/tools/analyst/disable", json={"actor": "admin:test"},
                    headers=H(keys["ADM"]))
    assert r.status_code == 200, r.text
    assert r.json()["lifecycle_state"] == "disabled"
    ok, reason = tool_runtime.check("analyst")
    assert not ok and "disabled" in reason
    # it is durable in the shared store and audited with the given actor
    reg = tool_runtime.runtime_registry()
    assert reg.get("agos.internal.analyst").lifecycle_state == "disabled"
    assert any(e["actor"] == "admin:test" for e in reg.audit())
    r = client.post("/api/admin/tools/analyst/enable", json={"actor": "admin:test"},
                    headers=H(keys["ADM"]))
    assert r.status_code == 200, r.text
    ok, _ = tool_runtime.check("analyst")
    assert ok


def test_admin_lifecycle_rejects_unknown_tool_and_requires_actor(client, keys):
    r = client.post("/api/admin/tools/no_such_tool/disable", json={"actor": "admin:test"},
                    headers=H(keys["ADM"]))
    assert r.status_code == 404, r.text
    r = client.post("/api/admin/tools/analyst/disable", json={},
                    headers=H(keys["ADM"]))
    assert r.status_code == 422, r.text


def test_admin_endpoints_fail_closed_on_an_unavailable_store(client, keys, tmp_path):
    tool_runtime.reset_runtime_registry()
    # a directory as the store file: opening it fails => RegistryStoreError
    import os
    os.environ["AGOS_TOOL_REGISTRY_DB"] = str(tmp_path / "as_a_directory")
    os.makedirs(os.environ["AGOS_TOOL_REGISTRY_DB"], exist_ok=True)
    try:
        r = client.get("/api/admin/tools", headers=H(keys["ADM"]))
        assert r.status_code == 503, r.text
        r = client.post("/api/admin/tools/health-check", headers=H(keys["ADM"]))
        assert r.status_code == 503, r.text
        r = client.post("/api/admin/tools/analyst/disable", json={"actor": "admin:test"},
                        headers=H(keys["ADM"]))
        assert r.status_code == 503, r.text
    finally:
        os.environ["AGOS_TOOL_REGISTRY_DB"] = ""  # cleaned by the db fixture reset
