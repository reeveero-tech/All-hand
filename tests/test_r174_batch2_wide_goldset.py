# -*- coding: utf-8 -*-
"""R-174 batch 2 (D-122): the WIDE gold set — selection quality measured
across many capabilities, tools, constraints, errors and scale.

Owner directive for this batch: اختبر جودة الاختيار على مجموعة أوسع بكثير —
اختبارات تغطي تخصصات مختلفة، أدوات متعددة، أخطاء، صلاحيات، خصوصية، وتعارضات.

The contracts under test (roadmap P5_TOOL_ECOSYSTEM_AR.md, R-174; batch 1 =
D-121 laid the selector; this batch widens the measured evidence):

* 15 capability domains × 8 tools each (120 records) with deliberate,
  deterministic attribute variations — champions, scoped champions, degraded /
  unhealthy / over-timeout / discovered / system-scoped / risky variants;
* gold task cases across every domain: default, risk-ceiling, tenant-scope,
  permissions (scopes), privacy (read_only demand / side effects), conflicts
  (all-excluded selections with recorded reasons), timeout budgets, quality
  signals that must NOT override health/lifecycle;
* error handling through the REAL dispatch gate: a 3-deep fallback chain
  (execution failure → verification failure → success), a failing writer that
  is NEVER silently retried past (even toward a read-only alternative),
  gate-refusal fallback, exhaustion;
* SCALE (the honest limit batch 1 left open — and the bug this batch fixes):
  candidate retrieval must be COMPLETE at >1000 tools per capability. The old
  ``search(..., limit=1000)`` made every tool beyond the first 1000 tool_ids
  INVISIBLE to selection — red proof below. Measured: selection latency at
  1500 tools, end-to-end execution at >1000 records through the persistent
  store and the real gate;
* measured top-1 accuracy printed for the evidence log (gold task set).
"""
from __future__ import annotations

import asyncio
import sys
import time

import pytest

from agent_civilization.core import tool_runtime
from agent_civilization.core.tool_registry import ToolRegistry

DISPATCHER_URI = "agent_civilization/core/agents/real_dispatcher"
REF = f"{__name__}:"
PROBE = {"prompt": "wide gold-set probe", "goal": "wide gold-set probe"}

# ---------------------------------------------------------------- fixtures


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


# ------------------------------------------------- wide gold-set universe
# 15 domains × 8 tools. Index semantics (wide-universe domains only):
#   0 champion      — healthy, certified, low risk, no scopes, 30s timeout
#   1 scoped        — healthy, validated, low risk, auth_scope=[d:read]
#   2 medium-risk   — healthy, registered, MEDIUM risk
#   3 degraded      — degraded health, validated, low risk
#   4 slow          — healthy, registered, low risk, timeout 900s
#   5 sick          — unhealthy, registered
#   6 dead          — discovered lifecycle (not executable)
#   7 system-scoped — healthy, registered, low risk, tenant system_scoped
# WRITE domains (side effects, read_only=False): 0/2 certified champions,
# idempotent except index 2 (idempotent=False).
# Domain overrides: text.translate — the CHAMPION itself is scoped
# (auth_scope=['t:admin']); analysis.numeric — EVERY tool is high risk.
# Ad-hoc capabilities (wide.*) get NO index variations — those tests declare
# every attribute explicitly.

READ_DOMAINS = ("wide.search.web", "wide.search.local", "wide.code.generate",
                "wide.analysis.data", "wide.analysis.numeric", "wide.media.image",
                "wide.media.audio", "wide.media.video", "wide.files.read",
                "wide.db.query", "wide.text.translate", "wide.text.summarize")
WRITE_DOMAINS = ("wide.files.write", "wide.db.write", "wide.code.test")
DOMAINS = READ_DOMAINS + WRITE_DOMAINS

CHAMPION_EVIDENCE = ("wide gold-set certified walk: smoke, contract and "
                     "security proof (log 347)")
VALIDATE_EVIDENCE = "contract and integration proof: wide gold-set (log 347)"


def _walk_certified(rec):
    for to in ("contract_pass", "integration_pass", "certified"):
        _reg().record_test_outcome(rec.tool_id, rec.version,
                                   actor="test:r174-batch2", to=to,
                                   evidence=CHAMPION_EVIDENCE)
    _reg().update_status(rec.tool_id, rec.version, actor="test:r174-batch2",
                         lifecycle_state="validated")
    return _reg().update_status(rec.tool_id, rec.version,
                                actor="test:r174-batch2",
                                lifecycle_state="certified")


def _walk_validated(rec):
    for to in ("contract_pass", "integration_pass"):
        _reg().record_test_outcome(rec.tool_id, rec.version,
                                   actor="test:r174-batch2", to=to,
                                   evidence=VALIDATE_EVIDENCE)
    return _reg().update_status(rec.tool_id, rec.version,
                                actor="test:r174-batch2",
                                lifecycle_state="validated")


def _handler(tag):
    async def _h(work):
        return {"ok": True, "tool_output": tag}
    return _h


def _spec(domain: str, idx: int) -> str:
    return f"wg_{domain.replace('.', '_')}_{idx}"


def _record(domain: str, idx: int, **over) -> dict:
    """The deterministic record for (domain, idx) — see the table above."""
    write = domain in WRITE_DOMAINS
    data = {
        "tool_id": f"agos.internal.{_spec(domain, idx)}",
        "name": _spec(domain, idx),
        "version": "1.0.0",
        "source": {"kind": "internal", "uri": DISPATCHER_URI},
        "description": f"wide gold-set tool {domain} #{idx}",
        "capabilities": [domain],
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "auth_scope": [],
        "tenant_scope": "request_tenant",
        "risk_class": "low",
        "side_effects": ["writes"] if write else [],
        "read_only": not write,
        "idempotent": True,
        "requires_approval": False,
        "timeout_ms": 30_000,
        "network_allowlist": [],
        "health_status": "healthy",
        "test_status": "not_tested",
        "implementation_ref": f"{REF}h_{_spec(domain, idx)}",
        "lifecycle_state": "registered",
    }
    if domain in DOMAINS:
        # index-driven variations (identical for every wide-universe domain)
        if idx == 1:
            data.update(auth_scope=[f"{domain}:read"])
        if idx == 2:
            data.update(risk_class="medium")
        if idx == 3:
            data.update(health_status="degraded")
        if idx == 4:
            data.update(timeout_ms=900_000)
        if idx == 5:
            data.update(health_status="unhealthy")
        if idx == 6:
            data.update(lifecycle_state="discovered")
        if idx == 7:
            data.update(tenant_scope="system_scoped")
        if domain in WRITE_DOMAINS and idx == 2:
            data.update(idempotent=False)
        # domain overrides
        if domain == "wide.text.translate" and idx == 0:
            data.update(auth_scope=["t:admin"])
        if domain == "wide.analysis.numeric":
            data.update(risk_class="high")
    data.update(over)
    return data


def _wide(monkeypatch, db):
    """Seed the whole wide universe into the REAL (persistent) registry in
    ONE transaction, then walk the governed certification paths."""
    from agent_civilization.core.agents import real_dispatcher
    from agent_civilization.core.tool_registry import ToolRecord
    records = [ToolRecord.model_validate(_record(d, i))
               for d in DOMAINS for i in range(8)]
    _reg().seed(records, actor="test:r174-batch2")
    # handlers for every record (honest implementation_refs — one per record)
    for d in DOMAINS:
        for i in range(8):
            fn = _handler(f"{d}:{i}")
            setattr(sys.modules[__name__], f"h_{_spec(d, i)}", fn)
            monkeypatch.setitem(real_dispatcher._HANDLERS, _spec(d, i), fn)
    # governed walks: champions (0) certified everywhere; write-domain index 2
    # certified too; index 1/3 validated in every domain.
    for d in DOMAINS:
        _walk_certified(_reg().get(f"agos.internal.{_spec(d, 0)}"))
        if d in WRITE_DOMAINS:
            _walk_certified(_reg().get(f"agos.internal.{_spec(d, 2)}"))
        for i in (1, 3):
            _walk_validated(_reg().get(f"agos.internal.{_spec(d, i)}"))


def _select(task):
    from agent_civilization.core import tool_selector
    return tool_selector.select_tools(_reg(), task)


def _ids(selection):
    return [r.tool_id for r in selection.ranked]


def _reasons(selection):
    return dict(selection.excluded)


def _tid(domain, idx):
    return f"agos.internal.{_spec(domain, idx)}"


def _task(**kw):
    from agent_civilization.core import tool_selector
    base = dict(top_k=8, timeout_budget_ms=60_000)
    base.update(kw)
    return tool_selector.TaskSpec(**base)


# --------------------------------------------- ranking across all domains

def test_wide_default_selection_per_read_domain(db, monkeypatch):
    """Every read domain: the certified champion wins and the full ranked
    order is exactly the designed one (healthy > degraded; certified >
    validated > registered; low risk > medium; tool_id tiebreak)."""
    _wide(monkeypatch, db)
    for d in READ_DOMAINS:
        if d in ("wide.text.translate", "wide.analysis.numeric"):
            continue        # scoped champion / all-high-risk — own tests below
        sel = _select(_task(capability=d))
        assert _ids(sel) == [_tid(d, 0), _tid(d, 7), _tid(d, 2), _tid(d, 3)], d
        assert "scopes not granted: " in _reasons(sel)[_tid(d, 1)]
        assert "timeout_ms=900000 above task budget 60000" in \
            _reasons(sel)[_tid(d, 4)]
        assert "health_status=unhealthy" in _reasons(sel)[_tid(d, 5)]
        assert "lifecycle_state=discovered" in _reasons(sel)[_tid(d, 6)]


def test_wide_low_risk_ceiling_excludes_medium(db, monkeypatch):
    _wide(monkeypatch, db)
    for d in READ_DOMAINS:
        if d in ("wide.text.translate", "wide.analysis.numeric"):
            continue
        sel = _select(_task(capability=d, max_risk="low"))
        assert _tid(d, 2) in _reasons(sel)
        assert "risk_class=medium above task ceiling low" in \
            _reasons(sel)[_tid(d, 2)]
        assert _ids(sel)[0] == _tid(d, 0)


def test_wide_tenant_scope_exclusion(db, monkeypatch):
    _wide(monkeypatch, db)
    d = "wide.search.web"
    sel = _select(_task(capability=d,
                        allowed_tenant_scopes=("request_tenant",)))
    assert _tid(d, 7) in _reasons(sel)
    assert "tenant_scope=system_scoped not allowed by task" in \
        _reasons(sel)[_tid(d, 7)]
    assert _ids(sel) == [_tid(d, 0), _tid(d, 2), _tid(d, 3)]


def test_wide_scoped_champion_permissions(db, monkeypatch):
    """text.translate's champion demands t:admin: excluded (recorded) without
    the grant, selected WITH it — permissions are the caller's, not the
    model's."""
    _wide(monkeypatch, db)
    d = "wide.text.translate"
    sel = _select(_task(capability=d))
    assert "scopes not granted: t:admin" in _reasons(sel)[_tid(d, 0)]
    assert _ids(sel)[0] == _tid(d, 7)
    sel = _select(_task(capability=d, granted_scopes=("t:admin",)))
    assert _ids(sel)[0] == _tid(d, 0)


def test_wide_all_high_risk_conflict(db, monkeypatch):
    """analysis.numeric: every tool is high risk — the conservative default
    ceiling yields an EMPTY selection with every exclusion recorded (a
    conflict surfaced, never a silent downgrade to a riskier tool)."""
    _wide(monkeypatch, db)
    d = "wide.analysis.numeric"
    sel = _select(_task(capability=d))
    assert _ids(sel) == []
    assert len(_reasons(sel)) == 8
    for i in (0, 1, 2, 3, 4, 7):
        assert "risk_class=high above task ceiling medium" in \
            _reasons(sel)[_tid(d, i)]
    assert "health_status=unhealthy" in _reasons(sel)[_tid(d, 5)]
    assert "lifecycle_state=discovered" in _reasons(sel)[_tid(d, 6)]
    sel = _select(_task(capability=d, max_risk="high"))
    assert _ids(sel)[0] == _tid(d, 0)


def test_wide_privacy_read_only_demand(db, monkeypatch):
    """A task demanding read_only tools never sees a writer, and a writer
    domain under a read_only demand is an empty selection (privacy conflict
    surfaced with reasons)."""
    _wide(monkeypatch, db)
    d = "wide.files.write"
    sel = _select(_task(capability=d, read_only=True, allow_side_effects=True))
    assert _ids(sel) == []
    for i in (0, 1, 2, 3, 4, 7):
        assert "read_only=False (task demands True)" in _reasons(sel)[_tid(d, i)]
    assert "health_status=unhealthy" in _reasons(sel)[_tid(d, 5)]
    assert "lifecycle_state=discovered" in _reasons(sel)[_tid(d, 6)]
    # and the read domain under the same demand is unaffected
    sel = _select(_task(capability="wide.files.read", read_only=True))
    assert _ids(sel)[0] == _tid("wide.files.read", 0)


def test_wide_write_domain_default_conflict(db, monkeypatch):
    """Writers are excluded by default (side effects not allowed) — an empty
    selection with the reason recorded for every writer."""
    _wide(monkeypatch, db)
    d = "wide.db.write"
    sel = _select(_task(capability=d))
    assert _ids(sel) == []
    for i in (0, 1, 2, 3, 4, 7):
        assert "side effects not allowed by task: writes" in _reasons(sel)[_tid(d, i)]
    assert "health_status=unhealthy" in _reasons(sel)[_tid(d, 5)]
    assert "lifecycle_state=discovered" in _reasons(sel)[_tid(d, 6)]


def test_wide_write_domain_allowed_side_effects(db, monkeypatch):
    _wide(monkeypatch, db)
    d = "wide.code.test"
    sel = _select(_task(capability=d, allow_side_effects=True))
    # certified champions first (tool_id tiebreak), then validated (1) ahead
    # of the registered system-scoped (7), then the medium-risk one (2),
    # then the degraded (3).
    assert _ids(sel) == [_tid(d, 0), _tid(d, 2), _tid(d, 7), _tid(d, 3)]


def test_wide_timeout_budget_boundary(db, monkeypatch):
    """The 900s tool is excluded under the 60s budget and eligible when the
    task genuinely raises it — the budget is the caller's declaration."""
    _wide(monkeypatch, db)
    d = "wide.media.image"
    sel = _select(_task(capability=d))
    assert "above task budget 60000" in _reasons(sel)[_tid(d, 4)]
    sel = _select(_task(capability=d, timeout_budget_ms=1_000_000))
    assert _tid(d, 4) in _ids(sel)


def test_wide_quality_signal_cannot_override_lifecycle(db, monkeypatch):
    """A terrible quality score for the champion and a great one for the
    runner-up changes nothing: health and lifecycle dominate quality."""
    _wide(monkeypatch, db)
    d = "wide.media.audio"
    quality = {_tid(d, 0): 0.01, _tid(d, 1): 0.99}
    sel = _select(_task(capability=d,
                        quality=lambda r: quality.get(r.tool_id, 0.5)))
    assert _ids(sel)[0] == _tid(d, 0)


def test_wide_determinism_across_insertion_order(db):
    """The same universe registered in REVERSE order yields the same ranked
    selection — no randomness, no insertion-order sensitivity (in-memory
    registry: selection never touches the store's own filters)."""
    from agent_civilization.core import tool_selector
    d = "wide.text.summarize"
    fwd = ToolRegistry()
    rev = ToolRegistry(clock=lambda: 0.0)
    for i in range(8):
        fwd.register(_record(d, i), actor="test:r174-batch2")
    for i in reversed(range(8)):
        rev.register(_record(d, i), actor="test:r174-batch2")
    a = tool_selector.select_tools(fwd, _task(capability=d))
    b = tool_selector.select_tools(rev, _task(capability=d))
    assert _ids(a) == _ids(b) == [_tid(d, 0), _tid(d, 7), _tid(d, 2),
                                  _tid(d, 3)]


def test_search_limit_none_and_default_still_caps():
    """The registry API change: limit=None means NO truncation; the default
    50 still caps — no other caller's contract shifts."""
    reg = ToolRegistry(clock=lambda: 0.0)
    d = "wide.search.local"
    for i in range(8):
        reg.register(_record(d, i), actor="test:r174-batch2")
    assert len(reg.search(capability=d)) <= 50
    assert len(reg.search(capability=d, limit=None)) == 8
    assert len(reg.search(capability=d, limit=2)) == 2


# ------------------------------------------------------ errors through gate

def test_fallback_chain_three_deep(db, monkeypatch):
    """Execution failure → verification failure → success: a 3-deep chain of
    read-only candidates, each fallback safe, the result verified."""
    from agent_civilization.core import tool_selector
    from agent_civilization.core.agents import real_dispatcher

    async def _boom(work):
        raise RuntimeError("wide chain primary failure")

    async def _wrong(work):
        return {"ok": True, "tool_output": "wrong-shape"}

    async def _good(work):
        return {"ok": True, "tool_output": "good"}
    for i, fn in ((0, _boom), (1, _wrong), (2, _good)):
        spec = _spec("wide.fb3", i)
        setattr(sys.modules[__name__], f"h_{spec}", fn)
        monkeypatch.setitem(real_dispatcher._HANDLERS, spec, fn)
        rec = _reg().register(_record("wide.fb3", i), actor="test:r174-batch2")
        if i == 0:
            _walk_certified(rec)
        elif i == 1:
            _walk_validated(rec)
    sel = _select(_task(capability="wide.fb3", top_k=3))
    result = run(tool_selector.run_selected(
        sel, dict(PROBE), verify=lambda r: r.get("tool_output") == "good"))
    assert result["ok"] is True
    assert result["tool"] == _tid("wide.fb3", 2)
    assert result["fallbacks"] == [
        {"from": _tid("wide.fb3", 0), "to": _tid("wide.fb3", 1),
         "reason": "execution failure"},
        {"from": _tid("wide.fb3", 1), "to": _tid("wide.fb3", 2),
         "reason": "verification failed"}]


def test_writer_failure_never_falls_back_even_to_safer(db, monkeypatch):
    """A failing WRITER is surfaced — never silently retried past, even when
    the next candidate is a harmless read-only tool (the alternative's
    handler must stay untouched)."""
    from agent_civilization.core import tool_selector
    from agent_civilization.core.agents import real_dispatcher

    async def _wfail(work):
        raise RuntimeError("wide writer failure")

    async def _wok(work):
        return {"ok": True, "tool_output": "wok"}
    spec = _spec("wide.wfx", 0)
    setattr(sys.modules[__name__], f"h_{spec}", _wfail)
    monkeypatch.setitem(real_dispatcher._HANDLERS, spec, _wfail)
    rec = _reg().register(
        _record("wide.wfx", 0, read_only=False, side_effects=["writes"],
                idempotent=False),
        actor="test:r174-batch2")
    _walk_certified(rec)
    calls = []

    async def _spy(work):
        calls.append(1)
        return await _wok(work)
    spec = _spec("wide.wfx", 1)
    setattr(sys.modules[__name__], f"h_{spec}", _spy)
    monkeypatch.setitem(real_dispatcher._HANDLERS, spec, _spy)
    _reg().register(_record("wide.wfx", 1, read_only=True),
                    actor="test:r174-batch2")
    sel = _select(_task(capability="wide.wfx", top_k=2,
                        allow_side_effects=True, read_only=None))
    assert len(sel.ranked) == 2
    result = run(tool_selector.run_selected(sel, dict(PROBE)))
    assert result["ok"] is False
    assert result["fallbacks"] == []
    assert calls == []          # the read-only alternative never ran


def test_gate_refusal_fallback_wide(db, monkeypatch):
    """An approval-demanding top choice never runs (gate refusal) — trying
    the next candidate is safe."""
    from agent_civilization.core import tool_selector
    from agent_civilization.core.agents import real_dispatcher

    async def _a(work):
        return {"ok": True, "tool_output": "a"}

    async def _b(work):
        return {"ok": True, "tool_output": "b"}
    for i, fn in ((0, _a), (1, _b)):
        spec = _spec("wide.approval", i)
        setattr(sys.modules[__name__], f"h_{spec}", fn)
        monkeypatch.setitem(real_dispatcher._HANDLERS, spec, fn)
    rec = _reg().register(
        _record("wide.approval", 0, requires_approval=True),
        actor="test:r174-batch2")
    _walk_certified(rec)
    _reg().register(_record("wide.approval", 1), actor="test:r174-batch2")
    sel = _select(_task(capability="wide.approval", top_k=2))
    result = run(tool_selector.run_selected(sel, dict(PROBE)))
    assert result["ok"] is True
    assert result["tool"] == _tid("wide.approval", 1)
    assert result["fallbacks"][0]["reason"] == "gate refusal (tool never ran)"


def test_exhaustion_of_read_only_candidates(db, monkeypatch):
    from agent_civilization.core import tool_selector
    from agent_civilization.core.agents import real_dispatcher

    async def _f1(work):
        raise RuntimeError("wide exhaustion 1")

    async def _f2(work):
        raise RuntimeError("wide exhaustion 2")
    for i, fn in ((0, _f1), (1, _f2)):
        spec = _spec("wide.exhaust", i)
        setattr(sys.modules[__name__], f"h_{spec}", fn)
        monkeypatch.setitem(real_dispatcher._HANDLERS, spec, fn)
        _reg().register(_record("wide.exhaust", i), actor="test:r174-batch2")
    sel = _select(_task(capability="wide.exhaust", top_k=2))
    result = run(tool_selector.run_selected(sel, dict(PROBE)))
    assert result["ok"] is False
    assert result["exhausted"] is True
    assert len(result["fallbacks"]) == 1


# ------------------------------------------------------------- scale (red)

BULK_CAP = "scale.bulk"


def _bulk_record(i: int, risk: str) -> dict:
    return {
        "tool_id": f"agos.internal.wg_scale_bulk_{i:04d}",
        "name": f"wg_scale_bulk_{i:04d}",
        "version": "1.0.0",
        "source": {"kind": "internal", "uri": DISPATCHER_URI},
        "description": f"scale bulk tool {i}",
        "capabilities": [BULK_CAP],
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "auth_scope": [],
        "tenant_scope": "request_tenant",
        "risk_class": risk,
        "side_effects": [],
        "read_only": True,
        "idempotent": True,
        "requires_approval": False,
        "timeout_ms": 30_000,
        "network_allowlist": [],
        "health_status": "healthy",
        "test_status": "not_tested",
        "implementation_ref": f"{REF}h_scale_bulk_{i:04d}",
        "lifecycle_state": "registered",
    }


def test_scale_candidate_retrieval_is_complete_beyond_1000():
    """RED on main: with 1500 tools in ONE capability, retrieval truncated at
    1000 (search limit) — the eligible champion sorting LAST was invisible and
    the selection came back EMPTY. The fix: complete retrieval (limit=None).
    This is the R-175 scale requirement: a 10K catalog must be fully visible
    to selection."""
    from agent_civilization.core import tool_selector
    reg = ToolRegistry(clock=lambda: 0.0)
    for i in range(1499):
        reg.register(_bulk_record(i, "high"), actor="test:r174-batch2")
    reg.register(_bulk_record(1499, "low"), actor="test:r174-batch2")
    sel = tool_selector.select_tools(reg, _task(capability=BULK_CAP,
                                                max_risk="low"))
    assert _ids(sel) == ["agos.internal.wg_scale_bulk_1499"]
    assert len(sel.excluded) == 1499


def test_scale_end_to_end_through_persistent_store_and_gate(db, monkeypatch):
    """>1000 records in the REAL persistent registry: selection finds the
    champion (sorting last) and it EXECUTES through the real dispatch gate —
    the full path at scale, not a record count."""
    from agent_civilization.core import tool_selector
    from agent_civilization.core.agents import real_dispatcher
    from agent_civilization.core.tool_registry import ToolRecord

    async def _live(work):
        return {"ok": True, "tool_output": "scale-live"}
    records = [ToolRecord.model_validate(_bulk_record(i, "high"))
               for i in range(1199)]
    records.append(ToolRecord.model_validate(_bulk_record(1199, "low")))
    _reg().seed(records, actor="test:r174-batch2")
    setattr(sys.modules[__name__], "h_scale_bulk_1199", _live)
    monkeypatch.setitem(real_dispatcher._HANDLERS, "wg_scale_bulk_1199",
                        _live)
    sel = _select(_task(capability=BULK_CAP, max_risk="low"))
    assert _ids(sel) == ["agos.internal.wg_scale_bulk_1199"]
    result = run(tool_selector.run_selected(sel, dict(PROBE)))
    assert result["ok"] is True
    assert result["tool"] == "agos.internal.wg_scale_bulk_1199"
    assert result["verified"] is True


def test_scale_selection_latency(db):
    """Measured selection latency at 1500 tools (in-memory): printed for the
    evidence log; the bound is generous (CI machines vary) — the point is
    the measurement, not a microbenchmark."""
    from agent_civilization.core import tool_selector
    reg = ToolRegistry(clock=lambda: 0.0)
    for i in range(1499):
        reg.register(_bulk_record(i, "high"), actor="test:r174-batch2")
    reg.register(_bulk_record(1499, "low"), actor="test:r174-batch2")
    task = _task(capability=BULK_CAP, max_risk="low")
    tool_selector.select_tools(reg, task)      # warm-up
    samples = []
    for _ in range(50):
        t0 = time.perf_counter()
        sel = tool_selector.select_tools(reg, task)
        samples.append(time.perf_counter() - t0)
    assert sel.ranked
    p50 = sorted(samples)[len(samples) // 2]
    print(f"scale selection latency (1500 tools, 50 runs): "
          f"p50={p50 * 1000:.2f}ms max={max(samples) * 1000:.2f}ms")
    assert max(samples) < 1.0


# ------------------------------------------------------ measured accuracy

def test_wide_measured_top1_accuracy(db, monkeypatch):
    """The gold task set measured: top-1 accuracy across every domain and
    constraint variant — printed for the evidence log, asserted at 100%."""
    _wide(monkeypatch, db)
    cases = []
    for d in READ_DOMAINS:
        if d == "wide.analysis.numeric":
            cases.append((_task(capability=d, max_risk="high"), _tid(d, 0)))
        elif d == "wide.text.translate":
            cases.append((_task(capability=d), _tid(d, 7)))
            cases.append((_task(capability=d, granted_scopes=("t:admin",)),
                          _tid(d, 0)))
        else:
            cases.append((_task(capability=d), _tid(d, 0)))
            cases.append((_task(capability=d, max_risk="low"), _tid(d, 0)))
    for d in WRITE_DOMAINS:
        cases.append((_task(capability=d, allow_side_effects=True),
                      _tid(d, 0)))
    hits = sum(1 for task, want in cases
               if _ids(_select(task))[:1] == [want])
    print(f"wide gold set measured: top-1 accuracy {hits}/{len(cases)} "
          f"({hits / len(cases):.0%}) over {len(DOMAINS)} domains")
    assert hits == len(cases)
