#!/usr/bin/env python3
"""اختبارات R-170 (دفعة 3): ربط بوابة التحقق بمسار الإكمال (أحمر سلوكي أولًا).

الاختبارات تعمل عبر واجهة ``execute()`` العامة نفسها وبمعالج بديل محلي
(بلا شبكة/أدوات حقيقية)، وتفعيل العلم ``AGOS_OUTPUT_VALIDATORS=1`` عبر
monkeypatch — لا تستورد أي دالة جديدة، لذا فشلها قبل التنفيذ سلوكي:
مخرج ``ok=True`` مزوّر يُعاد كما هو من مسار الإكمال اليوم.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_civilization.agents.real import real_agents

SPEC = "analyst"  # أي تخصص — المعالج بديل محلي


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
    """استبدال المعالج على مساري التشغيل (نمط test_r157_contract_parity)."""
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


def _gate_on(monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")


# ------------------------------------------------------------- red-first core


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_forged_success_downgraded_at_completion(impl, monkeypatch):
    """الاختبار الأحمر الحاسم: نجاح مزوّر بمتطلب tool_output يُخفَّض عند الإكمال.

    قبل الربط يفشل هذا الاختبار سلوكيًا: مسار الإكمال يعيد ok=True اليوم.
    بعد الربط: ok الخارجي والداخلي False، رمز خطأ ثابت، الحكم مسجل،
    والعدادات تحسب فشلًا (لا نجاحًا).
    """
    _gate_on(monkeypatch)
    real, spc = _install(monkeypatch, _forged_handler)
    agent = real if impl == "real" else spc
    work = {"prompt": "demo", "verification": {"tool_output": True}}
    out = _run(agent, work)
    assert out["ok"] is False, f"{impl}: forged success must not survive completion"
    assert out["result"]["ok"] is False
    assert out["result"]["error"] == "output_verification_failed"
    verdict = out["result"]["verification"]
    assert verdict["ok"] is False and verdict["reasons"]
    snap = agent.snapshot()["stats"]
    assert snap["tasks_completed"] == 0 and snap["tasks_failed"] == 1


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_forged_success_without_requirements_also_rejected(impl, monkeypatch):
    """بلا متطلبات صريحة: كشف النجاح المزوّر الافتراضي يعمل أيضًا عند الإكمال."""
    _gate_on(monkeypatch)
    real, spc = _install(monkeypatch, _forged_handler)
    agent = real if impl == "real" else spc
    out = _run(agent, {"prompt": "demo"})
    assert out["ok"] is False
    assert out["result"]["error"] == "output_verification_failed"


# ------------------------------------------------------------------ positives


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_verified_success_passes_with_recorded_verdict(impl, monkeypatch):
    _gate_on(monkeypatch)
    real, spc = _install(monkeypatch, _verified_handler)
    agent = real if impl == "real" else spc
    out = _run(agent, {"prompt": "demo", "verification": {"tool_output": True}})
    assert out["ok"] is True
    assert out["result"]["verification"]["ok"] is True
    assert agent.snapshot()["stats"]["tasks_completed"] == 1


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_honest_failure_untouched_by_gate(impl, monkeypatch):
    """الفشل الصادق لا يُرقّى ولا يُطمس خطؤه — البوابة لا تلمسه."""
    _gate_on(monkeypatch)
    real, spc = _install(monkeypatch, _honest_failure_handler)
    agent = real if impl == "real" else spc
    out = _run(agent, {"prompt": "demo", "verification": {"tool_output": True}})
    assert out["ok"] is False
    assert out["result"]["error"] == "provider_timeout"
    assert "verification" not in out["result"]


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_unverified_success_is_visible_not_proven(impl, monkeypatch):
    """ok=True بلا متطلبات وبلا أدلة وبلا ادعاء: يمر مع unverified=True ظاهر."""
    _gate_on(monkeypatch)
    def noop(work):
        return {"ok": True, "answer": "here is the summary", "tool_output": None}

    real, spc = _install(monkeypatch, noop)
    agent = real if impl == "real" else spc
    out = _run(agent, {"prompt": "demo"})
    assert out["ok"] is True
    assert out["result"]["verification"]["unverified"] is True


# ------------------------------------------------------------- flag off / robust


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_flag_off_preserves_current_behavior(impl, monkeypatch):
    """العلم معطل (الافتراضي): السلوك الحالي كما هو — لا بوابة ولا تسجيل."""
    monkeypatch.delenv("AGOS_OUTPUT_VALIDATORS", raising=False)
    real, spc = _install(monkeypatch, _forged_handler)
    agent = real if impl == "real" else spc
    out = _run(agent, {"prompt": "demo", "verification": {"tool_output": True}})
    assert out["ok"] is True  # السلوك القائم المحفوظ (توافق خلفي موثق)
    assert "verification" not in out["result"]
    assert agent.snapshot()["stats"]["tasks_completed"] == 1


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_malformed_requirements_fail_explicitly(impl, monkeypatch):
    """متطلبات مشوهة (ليست كائنًا) لا تتحول صمتًا إلى «لا متطلبات» — رفض صريح."""
    _gate_on(monkeypatch)
    real, spc = _install(monkeypatch, _verified_handler)
    agent = real if impl == "real" else spc
    out = _run(agent, {"prompt": "demo", "verification": "yes"})
    assert out["ok"] is False
    assert "malformed" in " ".join(out["result"]["verification"]["reasons"])


@pytest.mark.parametrize("impl", ["real", "specialists"])
def test_gate_error_never_passes_unchecked_success(impl, monkeypatch):
    """تعطل البوابة وهي مفعلة لا يعيد نجاحًا غير مفحوص — فشل تحقق واضح."""
    _gate_on(monkeypatch)
    real, spc = _install(monkeypatch, _verified_handler)
    agent = real if impl == "real" else spc

    import agent_civilization.core.output_validators as ov
    def boom(outcome, requirements=None):
        raise RuntimeError("gate crashed")
    monkeypatch.setattr(ov, "verify_success", boom)
    out = _run(agent, {"prompt": "demo", "verification": {"tool_output": True}})
    assert out["ok"] is False
