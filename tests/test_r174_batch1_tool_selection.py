"""R-174 batch 1 (D-121): tool selection quality — retrieval, ranking, verified
run with safe fallback.

The contracts under test (roadmap P5_TOOL_ECOSYSTEM_AR.md, R-174; owner
directive for this batch: اختيار أداة ذات جودة):

* at every task the CALLER declares a TaskSpec (capability, risk ceiling,
  read-only demand, granted scopes, timeout budget, side-effect allowance,
  tenant scope, prior-result quality) — the model never decides permissions;
* candidates are retrieved by exact capability from the real registry and
  every constraint violation is RECORDED with a reason (never silently
  dropped) — a prohibited tool can never be selected or called;
* ranking is deterministic and multi-criteria: health, lifecycle, test
  status, risk, read-only (privacy), prior-result quality, least scopes —
  with a lexicographic tool_id tiebreak (no randomness anywhere);
* the chosen candidate runs through the REAL dispatch gate (all R-171/R-172
  walls apply), the result is verified against the task's verification plan,
  and fallback to the next candidate happens ONLY when safe: the failed
  candidate never ran (gate refusal) or is read-only/idempotent AND the
  alternative is read-only/idempotent — a failed tool with side effects is
  NEVER silently retried past (the failure is surfaced);
* the gold-set acceptance: measured top-1/top-k tool choice accuracy on a
  gold task set, tool failure fallback, no prohibited tool call.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_civilization.core import tool_runtime

DISPATCHER_URI = "agent_civilization/core/agents/real_dispatcher"
REF = f"{__name__}:"
PROBE = {"prompt": "gold selection probe", "goal": "gold selection probe"}


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


def _tool(monkeypatch, spec, handler, capability="gold.search", **fields):
    """Register a gold-set tool the honest way: a real registry record whose
    implementation_ref names the module-level handler, and the frozen
    dispatcher's handler table pointing at the same object."""
    from agent_civilization.core.agents import real_dispatcher
    monkeypatch.setitem(real_dispatcher._HANDLERS, spec, handler)
    data = {
        "tool_id": f"agos.internal.{spec}",
        "name": spec,
        "version": "1.0.0",
        "source": {"kind": "internal", "uri": DISPATCHER_URI},
        "description": "gold-set selection tool",
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
        "health_status": "unknown",
        "test_status": "not_tested",
        "implementation_ref": f"{REF}{handler.__name__}",
        "lifecycle_state": "registered",
    }
    data.update(fields)
    return _reg().register(data, actor="test:r174-batch1")


def _certify(rec):
    """Walk the governed test-status path (D-120) to certified, then the
    lifecycle to certified — the only legal way to a certified record."""
    ev = ("smoke, contract and security proof: gold-set run in "
          "tests/test_r174_batch1_tool_selection.py (evidence log 346)")
    for to in ("contract_pass", "integration_pass", "certified"):
        _reg().record_test_outcome(rec.tool_id, rec.version,
                                   actor="test:r174-batch1", to=to, evidence=ev)
    _reg().update_status(rec.tool_id, rec.version, actor="test:r174-batch1",
                        lifecycle_state="validated")
    return _reg().update_status(rec.tool_id, rec.version,
                               actor="test:r174-batch1", lifecycle_state="certified")


def _validate(rec):
    ev = "contract and integration proof: gold-set run (evidence log 346)"
    for to in ("contract_pass", "integration_pass"):
        _reg().record_test_outcome(rec.tool_id, rec.version,
                                   actor="test:r174-batch1", to=to, evidence=ev)
    return _reg().update_status(rec.tool_id, rec.version,
                               actor="test:r174-batch1", lifecycle_state="validated")


# -- module-level handlers (implementation_ref must resolve to them) --------

async def _gold_fast(work):
    return {"ok": True, "tool_output": "fast"}

async def _gold_ok(work):
    return {"ok": True, "tool_output": "ok"}

async def _gold_dead(work):
    return {"ok": True, "tool_output": "dead"}

async def _gold_laggy(work):
    return {"ok": True, "tool_output": "laggy"}

async def _gold_risky(work):
    return {"ok": True, "tool_output": "risky"}

async def _gold_scoped(work):
    return {"ok": True, "tool_output": "scoped"}

async def _gold_writer(work):
    return {"ok": True, "tool_output": "writer"}

async def _gold_slow(work):
    return {"ok": True, "tool_output": "slow"}

async def _gold_sick(work):
    return {"ok": True, "tool_output": "sick"}

async def _gold_fail(work):
    raise RuntimeError("gold-set primary failure")

async def _gold_wfail(work):
    raise RuntimeError("gold-set writer failure")

async def _gold_wok(work):
    return {"ok": True, "tool_output": "wok"}

async def _gold_efail(work):
    raise RuntimeError("gold-set idempotent failure")

async def _gold_eok(work):
    return {"ok": True, "tool_output": "eok"}

async def _gold_a1(work):
    return {"ok": True, "tool_output": "a1"}

async def _gold_a2(work):
    return {"ok": True, "tool_output": "a2"}

async def _gold_ffail1(work):
    raise RuntimeError("gold-set exhaustion 1")

async def _gold_ffail2(work):
    raise RuntimeError("gold-set exhaustion 2")


# -- the gold set ------------------------------------------------------------

def _gold(monkeypatch, db):
    """The gold tool ecosystem for capability 'gold.search' (plus dedicated
    fallback capabilities). Expected ranking is asserted literally below."""
    fast = _tool(monkeypatch, "gold_fast", _gold_fast, health_status="healthy")
    _certify(fast)
    ok = _tool(monkeypatch, "gold_ok", _gold_ok, health_status="healthy")
    _validate(ok)
    _tool(monkeypatch, "gold_laggy", _gold_laggy, health_status="degraded")
    _tool(monkeypatch, "gold_risky", _gold_risky, risk_class="critical")
    scoped = _tool(monkeypatch, "gold_scoped", _gold_scoped,
                   health_status="healthy", auth_scope=["gold:read"])
    _certify(scoped)
    _tool(monkeypatch, "gold_writer", _gold_writer, side_effects=["writes"],
          read_only=False, idempotent=False)
    _tool(monkeypatch, "gold_slow", _gold_slow, timeout_ms=600_000)
    _tool(monkeypatch, "gold_sick", _gold_sick, health_status="unhealthy")
    _tool(monkeypatch, "gold_dead", _gold_dead, lifecycle_state="discovered")


GOLD_TASK = dict(capability="gold.search", top_k=8, timeout_budget_ms=60_000)


def _select(task=None):
    from agent_civilization.core import tool_selector
    return tool_selector.select_tools(_reg(), task or tool_selector.TaskSpec(**GOLD_TASK))


def _ids(selection):
    return [r.tool_id for r in selection.ranked]


def _reasons(selection):
    return dict(selection.excluded)


# -- ranking gold set (acceptance: measured top-k / tool choice accuracy) ----

def test_gold_set_ranking_and_measured_accuracy(db, monkeypatch):
    _gold(monkeypatch, db)
    from agent_civilization.core import tool_selector
    quality = {"agos.internal.gold_fast": 0.9, "agos.internal.gold_scoped": 0.8,
               "agos.internal.gold_ok": 0.7}
    base = dict(GOLD_TASK, quality=lambda r: quality.get(r.tool_id, 0.5))

    sel_a = _select(tool_selector.TaskSpec(**base))
    assert _ids(sel_a) == ["agos.internal.gold_fast", "agos.internal.gold_ok",
                           "agos.internal.gold_laggy"]

    sel_b = _select(tool_selector.TaskSpec(**base, granted_scopes=("gold:read",)))
    assert _ids(sel_b) == ["agos.internal.gold_fast", "agos.internal.gold_scoped",
                           "agos.internal.gold_ok", "agos.internal.gold_laggy"]

    sel_c = _select(tool_selector.TaskSpec(**base, max_risk="critical",
                                           allow_side_effects=True))
    # scoped stays excluded (no scopes granted in this task); writer is LOW
    # risk so risk-rank puts it before the critical one; laggy (degraded)
    # last. health > lifecycle > test > risk > read-only > quality.
    assert _ids(sel_c) == ["agos.internal.gold_fast", "agos.internal.gold_ok",
                           "agos.internal.gold_writer", "agos.internal.gold_risky",
                           "agos.internal.gold_laggy"]
    assert "scopes not granted" in _reasons(sel_c)["agos.internal.gold_scoped"]

    # measured top-1 accuracy over the gold task set (printed for the log)
    gold = [(sel_a, "agos.internal.gold_fast"), (sel_b, "agos.internal.gold_fast"),
            (sel_c, "agos.internal.gold_fast")]
    hits = sum(1 for sel, want in gold if _ids(sel)[0] == want)
    print(f"gold set measured: top-1 accuracy {hits}/{len(gold)} "
          f"({hits / len(gold):.0%}); top-3 hit rate "
          f"{sum(1 for sel, want in gold if want in _ids(sel)[:3])}/{len(gold)}")
    assert hits == len(gold)


def test_exclusions_are_recorded_with_reasons(db, monkeypatch):
    _gold(monkeypatch, db)
    sel = _select()
    reasons = _reasons(sel)
    assert "agos.internal.gold_risky" not in _ids(sel)
    assert "risk_class=critical" in reasons["agos.internal.gold_risky"]
    assert "scopes not granted" in reasons["agos.internal.gold_scoped"]
    assert "side effects" in reasons["agos.internal.gold_writer"]
    assert "timeout_ms=600000" in reasons["agos.internal.gold_slow"]
    assert "health_status=unhealthy" in reasons["agos.internal.gold_sick"]
    assert "lifecycle_state=discovered" in reasons["agos.internal.gold_dead"]


def test_scoped_tool_selected_when_scopes_granted(db, monkeypatch):
    _gold(monkeypatch, db)
    sel = _select(_scoped_task())
    assert "agos.internal.gold_scoped" in _ids(sel)


def _scoped_task():
    from agent_civilization.core import tool_selector
    return tool_selector.TaskSpec(**GOLD_TASK, granted_scopes=("gold:read",))


def test_timeout_budget_excludes_over_budget_tool(db, monkeypatch):
    _gold(monkeypatch, db)
    sel = _select()
    assert "agos.internal.gold_slow" in _reasons(sel)


def test_quality_signal_reorders_candidates(db, monkeypatch):
    _gold(monkeypatch, db)
    from agent_civilization.core import tool_selector
    # laggy is degraded — health dominates; give ok a terrible quality score
    # and laggy an excellent one: health still wins (quality is a tiebreaker
    # WITHIN equal health), then flip quality between two healthy tools.
    sel = _select(tool_selector.TaskSpec(
        **GOLD_TASK,
        quality=lambda r: {"agos.internal.gold_ok": 0.95,
                           "agos.internal.gold_fast": 0.5}.get(r.tool_id, 0.5)))
    # fast is certified vs ok validated — lifecycle dominates quality
    assert _ids(sel)[:2] == ["agos.internal.gold_fast", "agos.internal.gold_ok"]


def test_selection_is_deterministic(db, monkeypatch):
    _gold(monkeypatch, db)
    assert _ids(_select()) == _ids(_select())


def test_top_k_slices_ranked_list(db, monkeypatch):
    _gold(monkeypatch, db)
    sel = _select()
    assert len(sel.ranked) == 3            # exactly the eligible candidates
    from agent_civilization.core import tool_selector
    sel2 = tool_selector.select_tools(_reg(), tool_selector.TaskSpec(
        **{**GOLD_TASK, "top_k": 2}))
    assert len(sel2.ranked) == 2


# -- integration with the real seeded registry -------------------------------

def test_real_seed_capability_selection(db):
    from agent_civilization.core import tool_selector
    sel = tool_selector.select_tools(_reg(), tool_selector.TaskSpec(
        capability="code.validate", max_risk="low"))
    assert _ids(sel) == ["agos.internal.validator"]
    sel = tool_selector.select_tools(_reg(), tool_selector.TaskSpec(
        capability="analysis.numeric"))
    assert _ids(sel) == ["agos.internal.analyst"]


def test_real_seed_prohibited_tool_never_selected(db):
    """code.test is test_runner (high risk + sandboxed execution side effect):
    with the default ceiling it is excluded with a reason and can never be
    selected — the acceptance 'no prohibited tool call'."""
    from agent_civilization.core import tool_selector
    sel = tool_selector.select_tools(_reg(), tool_selector.TaskSpec(
        capability="code.test"))
    assert _ids(sel) == []
    assert "risk_class=high" in _reasons(sel)["agos.internal.test_runner"]
    result = run(tool_selector.run_selected(sel, dict(PROBE)))
    assert result["ok"] is False


def test_real_seed_effectful_tool_excluded_by_default(db):
    from agent_civilization.core import tool_selector
    sel = tool_selector.select_tools(_reg(), tool_selector.TaskSpec(
        capability="code.test", max_risk="high"))
    assert "side effects" in _reasons(sel)["agos.internal.test_runner"]
    sel = tool_selector.select_tools(_reg(), tool_selector.TaskSpec(
        capability="code.test", max_risk="high", allow_side_effects=True))
    assert _ids(sel) == ["agos.internal.test_runner"]


# -- verified run through the real gate + safe fallback ----------------------

def test_verified_run_through_the_real_gate(db, monkeypatch):
    _gold(monkeypatch, db)
    from agent_civilization.core import tool_selector
    sel = _select()
    result = run(tool_selector.run_selected(sel, dict(PROBE)))
    assert result["ok"] is True
    assert result["tool"] == "agos.internal.gold_fast"
    assert result["verified"] is True
    assert result["fallbacks"] == []


def test_safe_fallback_on_execution_failure(db, monkeypatch):
    """The top choice's handler raises: both tools are read-only, so the
    fallback to the next candidate is SAFE and the result comes from it."""
    from agent_civilization.core import tool_selector
    top = _tool(monkeypatch, "gold_top_fail", _gold_fail, capability="gold.fb",
                health_status="healthy")
    _certify(top)
    _tool(monkeypatch, "gold_fb_ok", _gold_ok, capability="gold.fb",
          health_status="healthy")
    _validate(_reg().get("agos.internal.gold_fb_ok"))
    sel = tool_selector.select_tools(_reg(), tool_selector.TaskSpec(
        capability="gold.fb", top_k=2))
    result = run(tool_selector.run_selected(sel, dict(PROBE)))
    assert result["ok"] is True
    assert result["tool"] == "agos.internal.gold_fb_ok"
    assert result["fallbacks"] == [{"from": "agos.internal.gold_top_fail",
                                    "to": "agos.internal.gold_fb_ok",
                                    "reason": "execution failure"}]


def test_no_fallback_after_side_effect_failure(db, monkeypatch):
    """A failing tool with side effects is never silently retried past: the
    alternative's handler must stay untouched and the failure is surfaced."""
    from agent_civilization.core import tool_selector
    _tool(monkeypatch, "gold_w1", _gold_wfail, capability="gold.write",
          side_effects=["writes"], read_only=False, idempotent=False,
          health_status="healthy")
    _certify(_reg().get("agos.internal.gold_w1"))
    _tool(monkeypatch, "gold_w2", _gold_wok, capability="gold.write",
          side_effects=["writes"], read_only=False, idempotent=False,
          health_status="healthy")
    sel = tool_selector.select_tools(_reg(), tool_selector.TaskSpec(
        capability="gold.write", allow_side_effects=True, top_k=2))
    assert len(sel.ranked) == 2
    calls = []

    async def _spy(work):
        calls.append(1)
        return await _gold_wok(work)
    from agent_civilization.core.agents import real_dispatcher
    monkeypatch.setitem(real_dispatcher._HANDLERS, "gold_w2", _spy)
    result = run(tool_selector.run_selected(sel, dict(PROBE)))
    assert result["ok"] is False
    assert result["fallbacks"] == []
    assert calls == []                      # the alternative never ran


def test_idempotent_effectful_failure_falls_back_to_idempotent(db, monkeypatch):
    from agent_civilization.core import tool_selector
    _tool(monkeypatch, "gold_e1", _gold_efail, capability="gold.write2",
          side_effects=["writes"], read_only=False, idempotent=True,
          health_status="healthy")
    _certify(_reg().get("agos.internal.gold_e1"))
    _tool(monkeypatch, "gold_e2", _gold_eok, capability="gold.write2",
          side_effects=["writes"], read_only=False, idempotent=True,
          health_status="healthy")
    sel = tool_selector.select_tools(_reg(), tool_selector.TaskSpec(
        capability="gold.write2", allow_side_effects=True, top_k=2))
    result = run(tool_selector.run_selected(sel, dict(PROBE)))
    assert result["ok"] is True
    assert result["tool"] == "agos.internal.gold_e2"


def test_gate_refusal_never_ran_falls_back(db, monkeypatch):
    """The top choice demands an approval the work lacks: the gate refuses it
    BEFORE it runs, so trying the next candidate is safe."""
    from agent_civilization.core import tool_selector
    _tool(monkeypatch, "gold_a1", _gold_a1, capability="gold.approval",
          requires_approval=True, health_status="healthy")
    _certify(_reg().get("agos.internal.gold_a1"))
    _tool(monkeypatch, "gold_a2", _gold_a2, capability="gold.approval",
          health_status="healthy")
    sel = tool_selector.select_tools(_reg(), tool_selector.TaskSpec(
        capability="gold.approval", top_k=2))
    result = run(tool_selector.run_selected(sel, dict(PROBE)))
    assert result["ok"] is True
    assert result["tool"] == "agos.internal.gold_a2"
    assert result["fallbacks"][0]["reason"] == "gate refusal (tool never ran)"


def test_verification_failure_triggers_safe_fallback(db, monkeypatch):
    from agent_civilization.core import tool_selector
    _gold(monkeypatch, db)
    sel = _select(tool_selector.TaskSpec(**{**GOLD_TASK, "top_k": 2}))
    result = run(tool_selector.run_selected(
        sel, dict(PROBE),
        verify=lambda r: r.get("tool_output") != "fast"))
    assert result["ok"] is True
    assert result["tool"] == "agos.internal.gold_ok"
    assert result["fallbacks"][0]["reason"] == "verification failed"


def test_exhausted_selection_reports_failure(db, monkeypatch):
    from agent_civilization.core import tool_selector
    for spec, handler in (("gold_f1", _gold_ffail1), ("gold_f2", _gold_ffail2)):
        _tool(monkeypatch, spec, handler, capability="gold.fail",
              health_status="healthy")
    sel = tool_selector.select_tools(_reg(), tool_selector.TaskSpec(
        capability="gold.fail", top_k=2))
    result = run(tool_selector.run_selected(sel, dict(PROBE)))
    assert result["ok"] is False
    assert len(result["fallbacks"]) == 1
    assert "exhausted" in result
