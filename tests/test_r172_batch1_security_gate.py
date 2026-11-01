"""R-172 batch 1 (D-115): the record's security metadata is ENFORCED at the
dispatch gate, not just stored.

The registry contract (R-171) already carries per-tool security facts —
``requires_approval``, ``timeout_ms`` — but nothing enforced them: a record
could demand approval and still run without one, and a tool could outlive its
registered budget. These tests prove, against the real shared store and the
real dispatch gate, that:

* a tool whose (new, versioned) record sets ``requires_approval=True`` is
  REFUSED without a valid approval — missing, non-dict, empty-actor and
  blank-actor approvals are all refusals;
* a valid approval (``work['approval'] = {'actor': ...}``) lets the same tool
  run, and the run is counted as approved;
* the registered ``timeout_ms`` is a hard gate limit: a tool that exceeds it
  is refused with a timeout error and counted in ``stats()['timed_out']``
  (fail-closed — the run is cancelled, not left running silently);
* the default seeds are unchanged: none of the 20 internal tools demands an
  approval, and their registered budgets are the seed table's.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_civilization.core import tool_runtime

PROBE = {"prompt": "security gate probe", "goal": "security gate probe"}


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


def _new_version(db, spec: str, **changes):
    """Register a NEW version of an internal tool's record with modified
    fields (versions are immutable — a metadata change is a new version, the
    registry's own rule). Same implementation_ref, same tool_id: no alias."""
    reg = tool_runtime.runtime_registry()
    rec = reg.get(f"agos.internal.{spec}")
    data = rec.model_dump()
    data["version"] = "2.0.0"
    data.update(changes)
    return reg.register(data, actor="test:r172-batch1")


# -- approval gate -----------------------------------------------------------

def test_a_tool_requiring_approval_is_refused_without_one(db):
    _new_version(db, "analyst", requires_approval=True)
    result = run(tool_runtime.dispatch("analyst", dict(PROBE)))
    assert result["ok"] is False
    assert "requires_approval" in result["error"]
    assert tool_runtime.stats()["refused"]["agos.internal.analyst@2.0.0"] == 1


@pytest.mark.parametrize("bad", [
    None, "approved", 1, {}, {"actor": ""}, {"actor": "   "},
    {"approved_by": "x"}, {"actor": 42},
])
def test_bad_approval_shapes_are_all_refused(db, bad):
    _new_version(db, "analyst", requires_approval=True)
    work = dict(PROBE, approval=bad)
    result = run(tool_runtime.dispatch("analyst", work))
    assert result["ok"] is False
    assert "requires_approval" in result["error"]


def test_a_valid_approval_lets_the_tool_run(db):
    _new_version(db, "analyst", requires_approval=True)
    work = dict(PROBE, approval={"actor": "operator:test"})
    result = run(tool_runtime.dispatch("analyst", work))
    assert result["ok"] is True, result
    st = tool_runtime.stats()
    assert st["executed"]["agos.internal.analyst@2.0.0"] == 1
    assert st["refused"].get("agos.internal.analyst@2.0.0", 0) == 0


def test_approval_is_not_sticky_across_work_items(db):
    _new_version(db, "analyst", requires_approval=True)
    ok1 = run(tool_runtime.dispatch("analyst", dict(PROBE, approval={"actor": "o"})))
    assert ok1["ok"] is True
    # the approval belongs to the work item — the next one still needs one
    refused = run(tool_runtime.dispatch("analyst", dict(PROBE)))
    assert refused["ok"] is False
    assert "requires_approval" in refused["error"]


# -- registered timeout --------------------------------------------------------

def test_registered_timeout_ms_is_enforced(db, monkeypatch):
    monkeypatch.setenv("AGOS_ALLOW_CODE_EXEC", "1")   # test_runner policy: allowed
    _new_version(db, "test_runner", timeout_ms=500)    # 0.5s budget
    work = {"data": {"code": "import time\ntime.sleep(6)\n"}}
    result = run(tool_runtime.dispatch("test_runner", work))
    assert result["ok"] is False
    assert "timeout" in result["error"]
    assert "500" in result["error"]
    st = tool_runtime.stats()
    assert st["timed_out"]["agos.internal.test_runner@2.0.0"] == 1


def test_timeout_refusal_is_a_failure_not_a_hang(db, monkeypatch):
    import time
    monkeypatch.setenv("AGOS_ALLOW_CODE_EXEC", "1")
    _new_version(db, "test_runner", timeout_ms=400)
    t0 = time.perf_counter()
    result = run(tool_runtime.dispatch("test_runner", {"data": {"code": "import time\ntime.sleep(6)\n"}}))
    wall = time.perf_counter() - t0
    assert result["ok"] is False and "timeout" in result["error"]
    assert wall < 5.0          # refused fast, not after the handler's own budget


# -- the default seeds are unchanged -------------------------------------------

def test_no_internal_seed_demands_approval_yet(db):
    reg = tool_runtime.runtime_registry()
    demanding = [r.tool_id for r in reg.search(limit=10_000) if r.requires_approval]
    assert demanding == []     # the enforcement exists; policy (who demands) is a later batch


def test_registered_budgets_match_the_seed_table(db):
    reg = tool_runtime.runtime_registry()
    budgets = {r.name: r.timeout_ms for r in reg.search(limit=10_000)}
    assert budgets["analyst"] == 30_000
    assert budgets["test_runner"] == 60_000
    assert budgets["self_developer"] == 120_000
