#!/usr/bin/env python3
"""اختبارات R-170 (دفعة 15): تفعيل بوابة الجودة افتراضيًا (قرار المالك 2026-10-28).

كان الافتراضي «معطل» منذ الدفعة 3 (توافق خلفي)، ودفعة 10 قاست مخاطر التفعيل
على تقسيم test (رفض كاذب لغير النموذج 0/147، كشف التزوير 214/214) وأوصت
بالتفعيل الافتراضي بموافقة المالك. هذه الدفعة تنفذ القرار: **البوابة مفعلة
بلا متغير بيئة، والتعطيل صريح فقط بـ ``AGOS_OUTPUT_VALIDATORS=0``**.

أحمر أولًا: كل اختبارات «الافتراضي الجديد» تفشل قبل قلب الافتراضي لأن
المتغير غير المضبوط يعني اليوم بوابة معطلة.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_civilization.agents.real import real_agents

SPEC = "analyst"


def _civ(impl):
    from agent_civilization.agents.real.real_agents import create_civilization
    return create_civilization(1, impl=impl)[SPEC][0]


def _forged_handler(work):
    """معالج يزعم النجاح بمخرج أداة فاشل (exit_code=1) — نموذج النجاح المزوّر."""
    return {"ok": True, "answer": "The task completed successfully.",
            "error": None, "tool_output": {"exit_code": 1, "failed": 20}}


def _verified_handler(work):
    return {"ok": True, "answer": "computed", "error": None,
            "tool_output": {"exit_code": 0, "rows": 4}}


def _honest_failure_handler(work):
    return {"ok": False, "answer": None, "error": "provider_timeout",
            "tool_output": None}


def _install(monkeypatch, handler):
    async def fake_dispatch(specialization, work):
        return handler(work)

    async def fake_run_inner(work):
        return {"ok": True, "result": handler(work)}

    real = _civ("real")
    spc = _civ("specialists")
    monkeypatch.setattr(real_agents, "_dispatch", fake_dispatch)
    monkeypatch.setattr(spc, "_run_inner", fake_run_inner)
    return real, spc


def _run(agent, work):
    return asyncio.run(agent.execute(work))


# ------------------------------------------------ default on (the new default)

@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_gate_active_by_default_rejects_forged_success(impl, monkeypatch):
    """الافتراضي الجديد: بلا ``AGOS_OUTPUT_VALIDATORS`` على الإطلاق، النجاح
    المزوّر يُرفض — هذه البوابة هي جوهر قرار التفعيل (أحمر قبل القلب)."""
    monkeypatch.delenv("AGOS_OUTPUT_VALIDATORS", raising=False)
    real, spc = _install(monkeypatch, _forged_handler)
    agent = real if impl == "real" else spc
    out = _run(agent, {"prompt": "demo", "verification": {"tool_output": True}})
    assert out["ok"] is False, "النجاح المزوّر مرّ والبوابة مفترضة مفعلة (أحمر أولًا)"
    assert out["result"]["verification"]["ok"] is False


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_gate_active_by_default_passes_honest_output(impl, monkeypatch):
    """الافتراضي الجديد: مخرج صادق مع متطلبات مستوفاة ينجح — لا رفض كاذب."""
    monkeypatch.delenv("AGOS_OUTPUT_VALIDATORS", raising=False)
    real, spc = _install(monkeypatch, _verified_handler)
    agent = real if impl == "real" else spc
    out = _run(agent, {"prompt": "demo", "verification": {"tool_output": True}})
    assert out["ok"] is True
    assert agent.snapshot()["stats"]["tasks_completed"] == 1


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_gate_active_by_default_does_not_touch_honest_failure(impl, monkeypatch):
    """الافتراضي الجديد: الفشل الصادق لا يُمس ولا يُرقى (توافق خلفي سلوكي)."""
    monkeypatch.delenv("AGOS_OUTPUT_VALIDATORS", raising=False)
    real, spc = _install(monkeypatch, _honest_failure_handler)
    agent = real if impl == "real" else spc
    out = _run(agent, {"prompt": "demo", "verification": {"tool_output": True}})
    assert out["ok"] is False
    assert out["result"]["error"] == "provider_timeout"


def test_gate_active_by_default_on_dag_stage(monkeypatch):
    """الافتراضي الجديد عبر مسار DAG: مرحلة تزوير تُرفض (status=failed)
    بدل أن تنجح كما كانت بلا علم (أحمر قبل القلب)."""
    from agent_civilization.pipeline import dag as dag_mod
    monkeypatch.delenv("AGOS_OUTPUT_VALIDATORS", raising=False)
    dag_mod.RUNS.clear()

    async def fake_dispatch(specialization, work):
        return _forged_handler(work)

    agent = _civ("real")
    monkeypatch.setattr(real_agents, "_dispatch", fake_dispatch)
    plan = {"primary": "p", "reason": "r", "goal": "g", "stages": [{
        "name": "s1", "specialization": SPEC, "prompt": "demo",
        "verification": {"tool_output": True}}]}
    report = asyncio.run(dag_mod.run_plan(plan, {SPEC: [agent]}))
    assert report["stages"][0]["status"] == dag_mod.FAILED
    assert report["final"] == ""  # لا مخرج نهائي من خطة مرفوضة
    dag_mod.RUNS.clear()


def test_explicit_zero_opts_out(monkeypatch):
    """التعطيل الصريح الوحيد بعد القلب: ``AGOS_OUTPUT_VALIDATORS=0`` —
    مسار تراجع تشغيلي محفوظ (توافق خلفي للمشغلين الذين عطلوا صراحة)."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "0")
    real, spc = _install(monkeypatch, _forged_handler)
    agent = real
    out = _run(agent, {"prompt": "demo", "verification": {"tool_output": True}})
    assert out["ok"] is True  # السلوك التاريخي عند التعطيل الصريح
    assert "verification" not in out["result"]


def test_explicit_one_still_activates(monkeypatch):
    """``AGOS_OUTPUT_VALIDATORS=1`` يظل تفعيلاً صريحًا صالحًا كما كان."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    real, spc = _install(monkeypatch, _forged_handler)
    out = _run(real, {"prompt": "demo", "verification": {"tool_output": True}})
    assert out["ok"] is False
