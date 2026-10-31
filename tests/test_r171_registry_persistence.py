"""R-171 batch 3 (D-113): the tool registry is DURABLE and SHARED by every
worker process on the host.

Before this batch (D-112) the registry lived in each process's memory: a tool
disabled in one worker kept running in the others, and every restart forgot
every state change and its audit trail. These tests prove, with real
separate processes where it matters, that:

* state changes and the audit trail survive a restart (a new registry object
  on the same store sees them);
* a change made by ANOTHER process reaches an already-seeded worker's gate on
  its next dispatch — no restart;
* mutations are validated against the LATEST shared state (no lost update: a
  stale process cannot move a tool another process disabled);
* concurrent seeding by several processes registers each tool exactly once
  and the audit sequence stays unique and gap-free;
* a removal (tombstone) survives a restart;
* an unavailable store makes the gate REFUSE (fail-closed), never run on
  stale or empty state;
* the code's seed vs the stored record: a stored record wins and drift is
  reported, never silently rewritten;
* the operator CLI disables/enables with an audited actor, and the env
  disable is persisted and visible to a worker started without it.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from agent_civilization.core import tool_runtime
from agent_civilization.core.agents import real_dispatcher
from agent_civilization.core.tool_registry import ToolNotFound, TransitionRefused, VersionConflict
from agent_civilization.core.tool_registry_store import PersistentToolRegistry, RegistryStoreError

ROOT = Path(__file__).resolve().parents[1]
TID = "agos.internal.analyst"


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


def _child(code: str, db_path: str, **env) -> subprocess.CompletedProcess:
    full = {**os.environ, "AGOS_ENV": "test", "AGOS_TOOL_REGISTRY_DB": db_path}
    full.pop("AGOS_DISABLED_TOOLS", None)
    full.update(env)
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=ROOT, env=full,
                          capture_output=True, text=True, timeout=120)


def _ok(cp):
    assert cp.returncode == 0, cp.stderr[-2000:]
    return cp.stdout


# -- durability ------------------------------------------------------------------

def test_state_and_audit_survive_a_restart(db):
    reg = tool_runtime.runtime_registry()
    reg.update_status(TID, "1.0.0", actor="ops:alice", lifecycle_state="disabled")
    tool_runtime.reset_runtime_registry()           # "restart"
    again = tool_runtime.runtime_registry()
    assert again is not reg
    assert again.get(TID).lifecycle_state == "disabled"
    upd = [e for e in again.audit() if e["op"] == "update" and e["tool_id"] == TID]
    assert [e["actor"] for e in upd] == ["ops:alice"]
    ok, reason = tool_runtime.check("analyst")
    assert not ok and "lifecycle_state=disabled" in reason


def test_reseeding_is_idempotent(db):
    tool_runtime.runtime_registry()
    tool_runtime.reset_runtime_registry()
    reg = tool_runtime.runtime_registry()
    assert len(reg) == len(real_dispatcher._HANDLERS)
    registers = [e for e in reg.audit() if e["op"] == "register"]
    assert len(registers) == len(real_dispatcher._HANDLERS)    # not 2x


def test_tombstone_survives_a_restart(db):
    reg = PersistentToolRegistry(db)
    rec = tool_runtime.runtime_registry().get(TID)
    reg.remove(TID, rec.version, actor="ops:alice")
    reg2 = PersistentToolRegistry(db)
    with pytest.raises(ToolNotFound):
        reg2.get(TID)
    changed = {**rec.model_dump(), "description": rec.description + " (changed)"}
    with pytest.raises(VersionConflict):
        reg2.register(changed, actor="ops:alice")


# -- sharing between processes ---------------------------------------------------

def test_another_process_disable_reaches_this_workers_gate_without_restart(db, monkeypatch):
    calls = []

    async def spy(spec, work):
        calls.append(spec)
        return {"ok": True}
    monkeypatch.setattr(real_dispatcher, "dispatch", spy)
    assert run(tool_runtime.dispatch("analyst", {"x": 1})) == {"ok": True}   # seeded, runs
    _ok(_child(f"""
        from agent_civilization.core import tool_runtime
        tool_runtime.runtime_registry().update_status({TID!r}, "1.0.0", actor="ops:other-worker",
                                                       lifecycle_state="disabled")
        """, db))
    out = run(tool_runtime.dispatch("analyst", {"x": 1}))
    assert out["ok"] is False and "agos.internal.analyst@1.0.0" in out["error"]
    assert "lifecycle_state=disabled" in out["error"]
    assert calls == ["analyst"]                      # the second call never ran
    _ok(_child(f"""
        from agent_civilization.core import tool_runtime
        tool_runtime.runtime_registry().update_status({TID!r}, "1.0.0", actor="ops:other-worker",
                                                       lifecycle_state="registered")
        """, db))
    assert run(tool_runtime.dispatch("analyst", {"x": 1})) == {"ok": True}  # re-enabled shared


def test_mutation_is_validated_against_the_latest_shared_state(db):
    stale = tool_runtime.runtime_registry()
    assert stale.get(TID).lifecycle_state == "registered"
    _ok(_child(f"""
        from agent_civilization.core import tool_runtime
        tool_runtime.runtime_registry().update_status({TID!r}, "1.0.0", actor="ops:b",
                                                       lifecycle_state="disabled")
        """, db))
    # registered -> validated is legal from what THIS process last saw, but the
    # shared state is disabled, and disabled -> validated is not a legal move.
    with pytest.raises(TransitionRefused):
        stale.update_status(TID, "1.0.0", actor="ops:a", lifecycle_state="validated")
    assert stale.get(TID).lifecycle_state == "disabled"
    refused = [e for e in stale.audit() if e["op"] == "update_refused"]
    assert refused and refused[-1]["actor"] == "ops:a"


def test_concurrent_seeding_and_updates_from_many_processes(db):
    specs = sorted(real_dispatcher._HANDLERS)[:6]
    code = """
        import sys
        from agent_civilization.core import tool_runtime
        spec = sys.argv[1]
        reg = tool_runtime.runtime_registry()
        reg.update_status("agos.internal." + spec, "1.0.0", actor="proc:" + spec,
                          health_status="healthy")
        """
    env = {**os.environ, "AGOS_ENV": "test", "AGOS_TOOL_REGISTRY_DB": db}
    env.pop("AGOS_DISABLED_TOOLS", None)
    procs = [subprocess.Popen([sys.executable, "-c", textwrap.dedent(code), s], cwd=ROOT, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for s in specs]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err[-2000:]
    reg = PersistentToolRegistry(db)
    for s in specs:
        assert reg.get("agos.internal." + s).health_status == "healthy"
    audit = reg.audit()
    seqs = [e["seq"] for e in audit]
    assert seqs == list(range(1, len(audit) + 1))              # unique, gap-free
    registers = [e["tool_id"] for e in audit if e["op"] == "register"]
    assert sorted(registers) == sorted(set(registers))         # each seed once
    assert len(registers) == len(real_dispatcher._HANDLERS)


# -- fail-closed -----------------------------------------------------------------

def test_unavailable_store_refuses_and_never_runs_the_handler(tmp_path, monkeypatch):
    bad = tmp_path / "is_a_directory"
    bad.mkdir()
    monkeypatch.setenv("AGOS_TOOL_REGISTRY_DB", str(bad))
    tool_runtime.reset_runtime_registry()
    calls = []

    async def spy(spec, work):
        calls.append(spec)
        return {"ok": True}
    monkeypatch.setattr(real_dispatcher, "dispatch", spy)
    try:
        out = run(tool_runtime.dispatch("analyst", {}))
        ok, reason = tool_runtime.check("analyst")
    finally:
        tool_runtime.reset_runtime_registry()
    assert out["ok"] is False and "registry store unavailable" in out["error"]
    assert not ok and "registry store unavailable" in reason
    assert calls == []
    assert str(bad) not in out["error"]              # no local path in the visible error


def test_store_failure_after_seeding_refuses(db, monkeypatch):
    tool_runtime.runtime_registry()

    def broken(self):
        raise RegistryStoreError("simulated I/O error")
    monkeypatch.setattr(PersistentToolRegistry, "_sync", broken)
    out = run(tool_runtime.dispatch("analyst", {}))
    assert out["ok"] is False and "registry store unavailable" in out["error"]


def test_failed_write_does_not_leave_memory_ahead_of_the_store(db, monkeypatch):
    reg = tool_runtime.runtime_registry()
    from agent_civilization.core import tool_registry_store as trs

    def broken_write(*a, **k):
        raise trs.sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(trs, "_write_changes", broken_write)
    with pytest.raises(RegistryStoreError):
        reg.update_status(TID, "1.0.0", actor="ops:a", lifecycle_state="disabled")
    monkeypatch.undo()
    assert reg.get(TID).lifecycle_state == "registered"
    assert PersistentToolRegistry(db).get(TID).lifecycle_state == "registered"


# -- seed vs stored --------------------------------------------------------------

def test_stored_record_wins_over_a_drifted_seed_and_drift_is_reported(db, monkeypatch):
    tool_runtime.runtime_registry()
    tool_runtime.reset_runtime_registry()
    from agent_civilization.core import internal_tools
    orig = internal_tools.internal_tool_records

    def drifted():
        out = []
        for r in orig():
            if r.tool_id == TID:
                r = r.model_copy(update={"description": r.description + " (drift)"})
            out.append(r)
        return out
    monkeypatch.setattr(internal_tools, "internal_tool_records", drifted)
    reg = tool_runtime.runtime_registry()
    assert not reg.get(TID).description.endswith("(drift)")
    assert tool_runtime.stats()["seed_drift"] == [f"{TID}@1.0.0"]


# -- operations: env + CLI -------------------------------------------------------

def test_env_disable_is_persisted_and_seen_by_a_worker_without_the_env(db):
    _ok(_child("""
        from agent_civilization.core import tool_runtime
        tool_runtime.runtime_registry()
        """, db, AGOS_DISABLED_TOOLS="analyst"))
    ok, reason = tool_runtime.check("analyst")      # this process: no env
    assert not ok and "lifecycle_state=disabled" in reason
    actors = [e["actor"] for e in tool_runtime.runtime_registry().audit() if e["op"] == "update"]
    assert actors == ["env:AGOS_DISABLED_TOOLS"]
    # a second worker with the same env does not write a second audit entry
    _ok(_child("""
        from agent_civilization.core import tool_runtime
        tool_runtime.runtime_registry()
        """, db, AGOS_DISABLED_TOOLS="analyst"))
    actors = [e["actor"] for e in PersistentToolRegistry(db).audit() if e["op"] == "update"]
    assert actors == ["env:AGOS_DISABLED_TOOLS"]


def _cli(db_path, *args):
    env = {**os.environ, "AGOS_ENV": "test", "AGOS_TOOL_REGISTRY_DB": db_path}
    env.pop("AGOS_DISABLED_TOOLS", None)
    return subprocess.run([sys.executable, "scripts/tool_registry_admin.py", *args], cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=120)


def test_operator_cli_disable_enable_list_audit(db):
    cp = _cli(db, "disable", "analyst", "--actor", "ops:alice")
    assert cp.returncode == 0, cp.stderr
    ok, _ = tool_runtime.check("analyst")
    assert not ok
    listing = json.loads(_cli(db, "list", "--json").stdout)
    row = next(r for r in listing if r["tool_id"] == TID)
    assert (row["version"], row["lifecycle_state"]) == ("1.0.0", "disabled")
    cp = _cli(db, "enable", TID, "--actor", "ops:alice")
    assert cp.returncode == 0, cp.stderr
    assert tool_runtime.check("analyst") == (True, "")
    audit = json.loads(_cli(db, "audit", "--json", "--tool", TID).stdout)
    assert [(e["op"], e["actor"]) for e in audit if e["op"] == "update"] == [
        ("update", "ops:alice"), ("update", "ops:alice")]


def test_operator_cli_requires_an_actor_and_rejects_unknown_tools(db):
    assert _cli(db, "disable", "analyst").returncode != 0
    cp = _cli(db, "disable", "no_such_tool", "--actor", "ops:alice")
    assert cp.returncode != 0 and "no_such_tool" in cp.stderr


def test_default_store_is_outside_the_source_tree(monkeypatch):
    from agent_civilization.core.tool_registry_store import default_db_path
    monkeypatch.delenv("AGOS_TOOL_REGISTRY_DB", raising=False)
    assert not Path(default_db_path()).resolve().is_relative_to(ROOT)


def test_a_row_edited_outside_the_registry_fails_integrity_and_refuses(db):
    import sqlite3
    tool_runtime.runtime_registry()
    with sqlite3.connect(db) as conn:
        body = conn.execute("SELECT record_json FROM tool_records WHERE tool_id = ?",
                            (TID,)).fetchone()[0]
        edited = json.loads(body)
        edited["implementation_ref"] = "os:system"          # digest NOT recomputed
        conn.execute("UPDATE tool_records SET record_json = ? WHERE tool_id = ?",
                     (json.dumps(edited), TID))
        conn.execute("UPDATE tool_meta SET v = v + 1 WHERE k = 'revision'")
    ok, reason = tool_runtime.check("analyst")
    assert not ok and "registry store unavailable" in reason
    with pytest.raises(RegistryStoreError, match="integrity"):
        PersistentToolRegistry(db)
