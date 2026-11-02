"""R-174 batch 4 (D-124): the quality signal fed from runtime telemetry.

The contracts under test (roadmap P5_TOOL_ECOSYSTEM_AR.md, R-174; the last
declared piece: تغذية الجودة من telemetry — batch 1 wired no automatic
feed, the quality callable was operator-supplied only):

* a task contract may declare ``quality_source: "telemetry"`` — the TaskSpec's
  quality callable is then built from the runtime's OWN operational counters
  (``tool_runtime.stats()``), a snapshot taken when the contract is derived;
* the score is an OPERATIONAL RELIABILITY ratio, honestly defined: of all
  gate-passing runs (``executed``), the share that hit no operational failure
  (``timed_out`` / ``output_capped`` / ``isolated_crashed``). Gate refusals
  (``refused``) and network-policy blocks are NOT quality failures — they are
  task-policy mismatches, not the tool misbehaving; a tool with no signal
  scores 0.0 (neutral, never negative);
* quality stays a TIEBREAKER: it can reorder equal-profile candidates but
  never overrides health, lifecycle, test status or risk;
* the feed reaches the AGENT PATH: a contracted work item with
  ``quality_source: "telemetry"`` selects the reliable sibling through
  ``agent.execute``.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_civilization.agents.real.real_agents import RealSpecialistAgent
from agent_civilization.core import tool_runtime

DISPATCHER_URI = "agent_civilization/core/agents/real_dispatcher"
REF = f"{__name__}:"
CAP = "q.feed"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    global _flaky_sleep
    _flaky_sleep = False
    path = str(tmp_path / "tools.sqlite3")
    monkeypatch.setenv("AGOS_TOOL_REGISTRY_DB", path)
    monkeypatch.delenv("AGOS_DISABLED_TOOLS", raising=False)
    tool_runtime.reset_runtime_registry()
    yield path
    _flaky_sleep = False
    tool_runtime.reset_runtime_registry()


def _reg():
    return tool_runtime.runtime_registry()


def _tool(monkeypatch, spec, handler, capability=CAP, **fields):
    from agent_civilization.core.agents import real_dispatcher
    monkeypatch.setitem(real_dispatcher._HANDLERS, spec, handler)
    data = {
        "tool_id": f"agos.internal.{spec}",
        "name": spec,
        "version": "1.0.0",
        "source": {"kind": "internal", "uri": DISPATCHER_URI},
        "description": "quality-feed test tool",
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
    return _reg().register(data, actor="test:r174-batch4")


# -- module-level handlers (implementation_ref must resolve to them) --------

#: per-test control of the flaky tool's first-run behaviour (module state is
#: NOT trusted across tests — each test sets it explicitly around its
#: warm-up dispatches; the fixture resets it).
_flaky_sleep = False


async def h_q_flaky(work):
    # when _flaky_sleep is set, the call exceeds the registered 150ms budget
    # (a REAL gate timeout); otherwise it returns immediately. Same object
    # throughout, so the gate's handler-identity check holds.
    import asyncio as _aio
    if _flaky_sleep:
        await _aio.sleep(0.4)
    return {"ok": True, "tool_output": "flaky-ran"}


async def h_q_reliable(work):
    return {"ok": True, "tool_output": "reliable-ran"}


# -- helpers ---------------------------------------------------------------

def _contract(capability=CAP, **extra):
    c = {"capability": capability}
    c.update(extra)
    return {"prompt": "quality feed test", "tool_contract": c}


def _seed_pair(monkeypatch, reliable_spec="q.reliable", flaky_spec="q.aflaky",
               flaky_timeout_ms=150):
    """Two identical-profile candidates for CAP; the UNRELIABLE one sorts
    FIRST by tool_id (so without a quality signal the lexicographic
    tiebreak picks it)."""
    global _flaky_sleep
    _tool(monkeypatch, flaky_spec, h_q_flaky, timeout_ms=flaky_timeout_ms)
    _tool(monkeypatch, reliable_spec, h_q_reliable)
    # give the unreliable one its real operational failure: run 1 exceeds the
    # 150ms budget and is killed by the gate; run 2 is clean —
    # executed=2, timed_out=1 -> quality 0.5. The reliable sibling runs
    # clean twice -> quality 1.0 (a real signal on both sides).
    _flaky_sleep = True
    r1 = run(tool_runtime.dispatch(flaky_spec, {"prompt": "warm"}))
    _flaky_sleep = False
    r2 = run(tool_runtime.dispatch(flaky_spec, {"prompt": "warm"}))
    assert r2.get("ok") is True
    run(tool_runtime.dispatch(reliable_spec, {"prompt": "warm"}))
    run(tool_runtime.dispatch(reliable_spec, {"prompt": "warm"}))
    return r1


def _quality_of(rec):
    from agent_civilization.core.tool_quality import runtime_quality
    return runtime_quality()(rec)


# -- the contract declaration (red: quality_source is unknown today) -------

def test_contract_quality_source_telemetry_builds_the_callable(db):
    from agent_civilization.core.task_contract import derive_task_spec
    spec = derive_task_spec(_contract(quality_source="telemetry"))
    assert spec.quality is not None


def test_contract_unknown_quality_source_rejected(db):
    from agent_civilization.core.task_contract import derive_task_spec
    with pytest.raises(ValueError, match="quality_source"):
        derive_task_spec(_contract(quality_source="hand-wave"))


def test_contract_without_quality_source_stays_operator_free(db):
    from agent_civilization.core.task_contract import derive_task_spec
    assert derive_task_spec(_contract()).quality is None


# -- the score itself (red: the module does not exist today) ---------------

def test_runtime_quality_is_the_operational_reliability_ratio(db, monkeypatch):
    _seed_pair(monkeypatch)
    flaky_rec = _reg().get("agos.internal.q.aflaky")
    reliable_rec = _reg().get("agos.internal.q.reliable")
    # the flaky tool ran twice, one run timed out -> 0.5
    assert _quality_of(flaky_rec) == pytest.approx(0.5)
    # the reliable tool ran twice clean -> 1.0
    run(tool_runtime.dispatch("q.reliable", {"prompt": "warm"}))
    run(tool_runtime.dispatch("q.reliable", {"prompt": "warm"}))
    assert _quality_of(reliable_rec) == pytest.approx(1.0)


def test_runtime_quality_no_signal_is_neutral_zero(db, monkeypatch):
    _tool(monkeypatch, "q.silent", h_q_reliable)
    rec = _reg().get("agos.internal.q.silent")
    assert _quality_of(rec) == 0.0


def test_runtime_quality_excludes_gate_refusals(db, monkeypatch):
    # a tool refused by the gate (policy, not the tool misbehaving) must not
    # be scored as a quality failure: one clean run + one refusal -> 1.0.
    _tool(monkeypatch, "q.refused", h_q_reliable)
    assert run(tool_runtime.dispatch("q.refused", {"prompt": "warm"})).get("ok") is True
    _reg().update_status("agos.internal.q.refused", "1.0.0", actor="test",
                         lifecycle_state="disabled")
    out = run(tool_runtime.dispatch("q.refused", {"prompt": "warm"}))
    assert out.get("ok") is False           # refused: lifecycle is disabled
    rec = _reg().get("agos.internal.q.refused")
    assert _quality_of(rec) == pytest.approx(1.0)


# -- the ranking property (regression + new feed) ---------------------------

def test_quality_feed_reorders_equal_profile_candidates(db, monkeypatch):
    _seed_pair(monkeypatch)
    from agent_civilization.core.task_contract import derive_task_spec
    from agent_civilization.core.tool_selector import select_tools
    # without the feed: the lexicographically-first (unreliable) tool wins
    plain = select_tools(_reg(), derive_task_spec(_contract()))
    assert plain.ranked[0].tool_id == "agos.internal.q.aflaky"
    # with the telemetry feed: the reliable sibling wins on the tiebreak
    fed = select_tools(_reg(), derive_task_spec(_contract(quality_source="telemetry")))
    assert fed.ranked[0].tool_id == "agos.internal.q.reliable"


def test_quality_never_overrides_health_or_lifecycle(db, monkeypatch):
    # a DEGRADED tool with a perfect reliability record still loses to a
    # healthy one with a worse record — quality is a tiebreak, not an
    # override (the batch-2 property, now through the telemetry feed).
    _tool(monkeypatch, "q.aflaky", h_q_flaky, timeout_ms=150)
    _tool(monkeypatch, "q.reliable", h_q_reliable, health_status="degraded")
    # flaky: one timeout + one clean run -> 0.5; degraded-reliable: clean -> 1.0
    global _flaky_sleep
    _flaky_sleep = True
    run(tool_runtime.dispatch("q.aflaky", {"prompt": "warm"}))
    _flaky_sleep = False
    run(tool_runtime.dispatch("q.aflaky", {"prompt": "warm"}))
    run(tool_runtime.dispatch("q.reliable", {"prompt": "warm"}))
    from agent_civilization.core.task_contract import derive_task_spec
    from agent_civilization.core.tool_selector import select_tools
    fed = select_tools(_reg(), derive_task_spec(_contract(quality_source="telemetry")))
    # health dominates quality: the healthy flaky tool wins
    assert fed.ranked[0].tool_id == "agos.internal.q.aflaky"
    # sanity: the quality callable really did see the reversed signal
    flaky_rec = _reg().get("agos.internal.q.aflaky")
    reliable_rec = _reg().get("agos.internal.q.reliable")
    assert _quality_of(reliable_rec) > _quality_of(flaky_rec)


# -- the agent path (the point of the feed) ---------------------------------

def test_agent_path_selects_the_reliable_tool_via_telemetry(db, monkeypatch):
    _seed_pair(monkeypatch)
    out = run(RealSpecialistAgent(name="analyst_0001", specialization="analyst",
                                 agent_id=1).execute(
        _contract(quality_source="telemetry")))
    assert out["ok"] is True
    assert out["result"]["tool"] == "agos.internal.q.reliable"
    assert out["result"]["result"]["tool_output"] == "reliable-ran"


def test_quality_snapshot_taken_at_derive_time(db, monkeypatch):
    # the score reflects the counters when the contract was derived, not a
    # later mutation (documented snapshot semantics).
    _seed_pair(monkeypatch)
    from agent_civilization.core.task_contract import derive_task_spec
    spec = derive_task_spec(_contract(quality_source="telemetry"))
    flaky_rec = _reg().get("agos.internal.q.aflaky")
    before = spec.quality(flaky_rec)
    assert before == pytest.approx(0.5)
    # one more clean run changes the live counters but not the snapshot
    run(tool_runtime.dispatch("q.aflaky", {"prompt": "warm"}))
    assert spec.quality(flaky_rec) == pytest.approx(0.5)
