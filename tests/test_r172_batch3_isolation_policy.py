"""R-172 batch 3 (D-117): execution isolation, the approval POLICY, and a
sanitized failure channel — the three declared residuals of batches 1-2.

Batch 2 proved the gate refuses oversized results, undeclared network use and
missing tool scopes — but declared honestly what it did NOT do:

* threads a handler spawns escape the per-task network hook (a fresh
  context), and nothing bounds a handler's memory or contains its crash —
  execution isolation was "a later R-172 batch";
* ``requires_approval`` accepted any non-empty ``actor`` string — no TTL, no
  approver scopes, no tool binding: the "who may approve / for how long"
  surface was a claim, not a policy;
* an error text crossing the gate carried whatever the handler raised — the
  R-159/R-160 redaction boundaries existed elsewhere, but the TOOL gate was
  not one of them.

These tests run against the real shared store and the real dispatch gate, with
swapped-in adversarial handlers registered the registry's own way (a new
version whose ``implementation_ref`` resolves to the handler — the swap is
EXPLICIT, so ``_own_check`` passes). The isolated-worker tests spawn REAL
worker processes (``spawn`` context): the handler they run is resolved by its
dotted reference in the fresh interpreter, so the adversarial fakes below must
be module-level (picklable by reference), exactly like the handlers they
impersonate.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import socket as socketlib
import threading
import time

import pytest

from agent_civilization.core import tool_runtime

PROBE = {"prompt": "isolation policy probe", "goal": "isolation policy probe"}
JWT = ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
       "dozjgNryP4J3jVmNHl0w5N69gSbXGk2K1zD5eXzW3qY")

_REF = f"{__name__}:"          # implementation_ref prefix for the fakes below


# module-level adversarial handlers (the isolated worker resolves them by
# dotted reference in a FRESH interpreter — they must be importable).

def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


async def _fake_ok(work):
    return {"ok": True, "probe": True}


async def _fake_threaded_net(work):
    """The batch-2 residual made adversarial: a handler-spawned THREAD tries
    to open a socket — in the parent's per-task hook it escapes (fresh
    context); in the isolated worker the hook is process-wide, so the thread
    is blocked too. The failure text is reported honestly."""
    errs = []

    def _phone_home():
        try:
            s = socketlib.create_connection(("127.0.0.1", 9), timeout=2.0)
            s.close()
            errs.append("connected")            # must not happen
        except Exception as e:                   # noqa: BLE001
            errs.append(f"{type(e).__name__}: {e}")

    t = threading.Thread(target=_phone_home)
    t.start()
    t.join(3.0)
    if errs and errs[0] == "connected":
        return {"ok": True, "connected": True}
    return {"ok": False, "error": errs[0] if errs else "no result"}


async def _fake_crasher(work):
    os._exit(1)                                 # never returns


async def _fake_hog(work):
    try:
        big = bytearray(600 * 1024 * 1024)       # 600MB vs a 256MB cap
        return {"ok": True, "alloc": len(big)}
    except MemoryError as e:
        return {"ok": False, "error": f"MemoryError: {e}"}


async def _fake_sleeper(work):
    await asyncio.sleep(30)
    return {"ok": True}


async def _fake_secret_failure(work):
    raise ValueError(f"call failed with Bearer {JWT}")


async def _fake_secret_error_dict(work):
    return {"ok": False, "error": "key=sk-projAbc123XYZ987defGHI456jklMNO"}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "tools.sqlite3")
    monkeypatch.setenv("AGOS_TOOL_REGISTRY_DB", path)
    monkeypatch.delenv("AGOS_DISABLED_TOOLS", raising=False)
    monkeypatch.delenv("AGOS_TOOL_MAX_OUTPUT_BYTES", raising=False)
    tool_runtime.reset_runtime_registry()
    yield path
    tool_runtime.reset_runtime_registry()


def _new_version(db, spec: str, **changes):
    """Register a NEW version of an internal tool's record (the registry's own
    rule: versions are immutable, a metadata change is a new version). The
    version is the next minor above the current latest — reviewer and modifier
    seed at 1.1.0 after this batch, so nothing may hardcode their seed."""
    reg = tool_runtime.runtime_registry()
    rec = reg.get(f"agos.internal.{spec}")
    data = rec.model_dump()
    maj, minor, patch = (int(p) for p in rec.version.split("."))
    data["version"] = f"{maj}.{minor + 1}.0"
    data.update(changes)
    return reg.register(data, actor="test:r172-batch3")


def _swap_in(monkeypatch, spec: str, handler, db, **changes):
    """Register the adversarial handler the honest way: a new record version
    whose implementation_ref resolves to it, and the handler table pointing at
    the same object — so the gate's own-handler check passes and the swap is
    visible in the audit trail, not hidden."""
    from agent_civilization.core.agents import real_dispatcher
    name = handler.__name__
    monkeypatch.setitem(real_dispatcher._HANDLERS, spec, handler)
    return _new_version(db, spec, implementation_ref=_REF + name, **changes)


# -- the approval POLICY (D-117) ----------------------------------------------

def test_modifier_seed_declares_the_approval_policy(db):
    """A REAL internal tool now demands a real approval — the policy is not a
    test-only fiction: modifier 1.1.0 carries it in the seed."""
    rec = tool_runtime.runtime_registry().get("agos.internal.modifier")
    assert rec.requires_approval is True
    assert rec.approval_ttl_s == 600
    assert rec.approval_scopes == ["ops:approve-exec"]
    # the legacy row stays byte-identical to what hosts already store
    rows = tool_runtime.runtime_registry().search(
        text="modifier", include_disabled=True, latest_only=False, limit=10)
    versions = sorted(r.version for r in rows
                     if r.tool_id == "agos.internal.modifier")
    assert versions == ["1.0.0", "1.1.0"]


def test_modifier_refused_without_approval(db, monkeypatch):
    rec = _swap_in(monkeypatch, "modifier", _fake_ok, db)  # policy inherited
    result = run(tool_runtime.dispatch("modifier", dict(PROBE)))
    assert result["ok"] is False
    assert "requires_approval" in result["error"]
    assert tool_runtime.stats()["refused"][f"agos.internal.modifier@{rec.version}"] == 1


def test_ttl_demands_a_fresh_granted_at(db, monkeypatch):
    _swap_in(monkeypatch, "modifier", _fake_ok, db)
    base = dict(PROBE, approval={"actor": "ops"})
    # no granted_at at all
    result = run(tool_runtime.dispatch("modifier", dict(base)))
    assert result["ok"] is False and "granted_at" in result["error"]
    # malformed timestamp
    result = run(tool_runtime.dispatch("modifier", dict(
        base, approval={"actor": "ops", "granted_at": "yesterday"})))
    assert result["ok"] is False and "ISO-8601" in result["error"]
    # stale: older than the registered approval_ttl_s=600
    old = (dt.datetime.now(dt.timezone.utc)
           - dt.timedelta(seconds=3600)).isoformat()
    result = run(tool_runtime.dispatch("modifier", dict(
        base, approval={"actor": "ops", "granted_at": old})))
    assert result["ok"] is False and "stale" in result["error"]


def test_approver_scopes_are_required_and_distinct_from_tool_scopes(db,
                                                                     monkeypatch):
    _swap_in(monkeypatch, "modifier", _fake_ok, db)
    fresh = _now_iso()
    # the TOOL's scopes (work['scopes']) do NOT satisfy the APPROVER policy
    result = run(tool_runtime.dispatch("modifier", dict(
        PROBE, scopes=["ops:approve-exec"],
        approval={"actor": "ops", "granted_at": fresh})))
    assert result["ok"] is False and "approval.scopes" in result["error"]
    # the approver holding the scope in approval['scopes'] runs
    result = run(tool_runtime.dispatch("modifier", dict(
        PROBE, approval={"actor": "ops", "granted_at": fresh,
                         "scopes": ["ops:approve-exec"]})))
    assert result["ok"] is True


def test_approval_binds_to_the_tool_it_names(db, monkeypatch):
    _swap_in(monkeypatch, "modifier", _fake_ok, db)
    fresh = _now_iso()
    result = run(tool_runtime.dispatch("modifier", dict(
        PROBE, approval={"actor": "ops", "granted_at": fresh,
                         "scopes": ["ops:approve-exec"],
                         "tool": "agos.internal.builder"})))
    assert result["ok"] is False and "bound to tool" in result["error"]
    result = run(tool_runtime.dispatch("modifier", dict(
        PROBE, approval={"actor": "ops", "granted_at": fresh,
                         "scopes": ["ops:approve-exec"],
                         "tool": "agos.internal.modifier"})))
    assert result["ok"] is True


def test_approval_policy_without_policy_fields_keeps_the_batch1_contract(
        db, monkeypatch):
    """A record that only sets requires_approval (no TTL/scopes) keeps the
    batch-1 minimal contract — the policy is additive, not a breaking
    change."""
    _swap_in(monkeypatch, "builder", _fake_ok, db, requires_approval=True,
             approval_ttl_s=None, approval_scopes=[])
    result = run(tool_runtime.dispatch("builder", dict(PROBE)))
    assert result["ok"] is False and "requires_approval" in result["error"]
    result = run(tool_runtime.dispatch("builder", dict(
        PROBE, approval={"actor": "o"})))
    assert result["ok"] is True


# -- execution isolation (D-117) ----------------------------------------------

def test_no_seeded_tool_opts_into_process_isolation_yet(db):
    """The capability is enforced and adversarially tested (below), but NO
    seeded tool opts into it yet — a DELIBERATE, declared deferral (D-117):
    binding a seeded tool changes the seams of the generic
    all-specialization test loops (they patch the frozen dispatcher's
    dispatch function, which a spawned worker legitimately bypasses). This
    test pins that honest state so the deferral cannot silently become an
    untested opt-in."""
    reg = tool_runtime.runtime_registry()
    isolated = [r.tool_id for r in reg.search(limit=10_000)
                if (r.resource_budget or {}).get("isolation") == "process"]
    assert isolated == []


def test_the_real_reviewer_handler_runs_in_an_isolated_worker(db):
    """The REAL reviewer handler code (not a fake) runs in a SPAWNED worker:
    a test-registered 1.1.0 carries the real implementation_ref plus the
    isolation budget, and the real gate dispatches it — the worker runs
    the frozen dispatcher's own ``_reviewer`` in a fresh interpreter and
    its real result comes back."""
    reg = tool_runtime.runtime_registry()
    rec = reg.get("agos.internal.reviewer")
    data = rec.model_dump()
    data.update({"version": "1.1.0",
                 "resource_budget": {"isolation": "process"}})
    new = reg.register(data, actor="test:r172-batch3")
    assert tool_runtime._wants_isolation(new) is True
    before = tool_runtime.stats()["isolated"].get(
        "agos.internal.reviewer@1.1.0", 0)
    result = run(tool_runtime.dispatch(
        "reviewer", {"data": {"code": "import os\nos.system('ls')\n"}}))
    assert result["ok"] is True
    assert tool_runtime.stats()["isolated"]["agos.internal.reviewer@1.1.0"] \
        == before + 1
    assert tool_runtime.stats()["isolated_crashed"].get(
        "agos.internal.reviewer@1.1.0", 0) == 0


def test_an_isolated_worker_blocks_a_thread_spawned_by_the_handler(db,
                                                                  monkeypatch):
    """The batch-2 residual, closed: a handler-spawned THREAD cannot escape
    the network policy inside the isolated worker (the hook is process-wide
    there). The adversarial handler below is exactly the phone-home attempt
    that escaped the parent's per-task gate in batch 2."""
    rec = _swap_in(monkeypatch, "analyst", _fake_threaded_net, db,
                   resource_budget={"isolation": "process"})
    result = run(tool_runtime.dispatch("analyst", dict(PROBE)))
    assert result["ok"] is False
    assert "network use blocked" in result["error"]
    key = f"agos.internal.analyst@{rec.version}"
    assert tool_runtime.stats()["net_blocked"][key] == 1
    assert tool_runtime.stats()["isolated"][key] == 1


def test_an_isolated_worker_contains_a_crashing_handler(db, monkeypatch):
    """os._exit(1) inside the handler kills the WORKER, not the host: the
    gate returns an honest error dict and the process running the tests is
    alive to assert it."""
    _swap_in(monkeypatch, "analyst", _fake_crasher, db,
             resource_budget={"isolation": "process"})
    rec = _swap_in(monkeypatch, "analyst", _fake_crasher, db,
                   resource_budget={"isolation": "process"})
    result = run(tool_runtime.dispatch("analyst", dict(PROBE)))
    assert result["ok"] is False
    assert "crashed" in result["error"]
    key = f"agos.internal.analyst@{rec.version}"
    assert tool_runtime.stats()["isolated_crashed"][key] == 1


def test_an_isolated_worker_caps_the_address_space(db, monkeypatch):
    """600MB requested against a 256MB RLIMIT_AS: the worker reports
    MemoryError honestly — the host's memory is not the handler's to spend."""
    _swap_in(monkeypatch, "analyst", _fake_hog, db,
             resource_budget={"isolation": "process",
                              "max_memory_bytes": 256 * 1024 * 1024})
    result = run(tool_runtime.dispatch("analyst", dict(PROBE)))
    assert result["ok"] is False
    assert "MemoryError" in result["error"]


def test_an_isolated_worker_is_killed_on_the_registered_timeout(db,
                                                               monkeypatch):
    """The registered timeout_ms is enforced against the WORKER: kill + join,
    counted in stats()['timed_out'], never a hang."""
    rec = _swap_in(monkeypatch, "analyst", _fake_sleeper, db,
                   resource_budget={"isolation": "process"}, timeout_ms=1_500)
    t0 = time.monotonic()
    result = run(tool_runtime.dispatch("analyst", dict(PROBE)))
    assert time.monotonic() - t0 < 10        # killed at ~1.5s, not 30s
    assert result["ok"] is False
    assert "timeout_ms=1500" in result["error"]
    assert tool_runtime.stats()["timed_out"][f"agos.internal.analyst@{rec.version}"] == 1


def test_isolation_does_not_touch_concurrent_in_process_tools(db,
                                                              monkeypatch):
    """An isolated no-network tool and a normal in-process tool run
    concurrently: the normal tool's path is unchanged (no global hook, no
    global cap)."""
    _swap_in(monkeypatch, "analyst", _fake_threaded_net, db,
             resource_budget={"isolation": "process"})
    _swap_in(monkeypatch, "builder", _fake_ok, db)

    async def _both():
        r1 = tool_runtime.dispatch("analyst", dict(PROBE))
        r2 = tool_runtime.dispatch("builder", dict(PROBE))
        return await asyncio.gather(r1, r2)

    isolated_res, normal_res = run(_both())
    assert isolated_res["ok"] is False       # blocked in the worker
    assert normal_res["ok"] is True           # the in-process path is fine


# -- sanitized failure channel (D-117) -----------------------------------------

def test_a_secret_in_a_raised_error_text_is_redacted_at_the_gate(db,
                                                                  monkeypatch):
    _swap_in(monkeypatch, "analyst", _fake_secret_failure, db)
    result = run(tool_runtime.dispatch("analyst", dict(PROBE)))
    assert result["ok"] is False
    assert JWT not in result["error"]
    assert "<redacted>" in result["error"]


def test_a_secret_in_an_error_dict_is_redacted_at_the_gate(db, monkeypatch):
    _swap_in(monkeypatch, "analyst", _fake_secret_error_dict, db)
    result = run(tool_runtime.dispatch("analyst", dict(PROBE)))
    assert result["ok"] is False
    assert "sk-projAbc123XYZ987defGHI456jklMNO" not in result["error"]
    assert "<redacted>" in result["error"]


def test_non_secret_diagnostics_survive_redaction(db, monkeypatch):
    _swap_in(monkeypatch, "analyst", _fake_threaded_net, db,
             resource_budget={"isolation": "process"})
    result = run(tool_runtime.dispatch("analyst", dict(PROBE)))
    # the diagnosis (network use blocked) is kept: redaction removes
    # credentials, not the reason the tool failed
    assert "network use blocked" in result["error"]
