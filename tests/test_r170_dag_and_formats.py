#!/usr/bin/env python3
"""اختبارات R-170 (دفعة 4): مسار DAG وصيغ النتائج الأخرى (أحمر أولًا).

نطاقان:

1. **مسار DAG** (``pipeline/dag.py``): مرحلة خطة تُنفذ عبر ``agent.execute``
   الحقيقي للوكتين (real + specialists) بمعالج بديل محلي — بلا شبكة. البوابة
   مربوطة في ``_execute`` منذ الدفعة 3، والسؤال هنا: هل يصل رفضها إلى حالة
   المرحلة والتشغيل؟ وهل تستطيع مرحلة خطة إعلان متطلبات التحقق
   (``stage["verification"]``)؟ قبل هذه الدفعة لم يكن ``_work_for`` يمرر
   المتطلبات إلى ``work`` — فمرحلة تعلن ``tool_output`` تنجح بلا أي دليل.

2. **صيغ النتائج الأخرى**: ``HandlerResult`` (pydantic) ونتائج غير قاموسية
   (نص/None/قائمة) ونتائج بلا مفتاح ``ok`` وحمولة JSON نصية.

كل اختبار مصنف صراحة: ``RED`` = كان يفشل سلوكيًا قبل تغيير هذه الدفعة؛
``CHAR`` = توصيف لسلوك صحيح قائم (لا يحتاج تغييرًا — لا يُحتسب «أحمر»).
"""
from __future__ import annotations

import asyncio

import pytest

from agent_civilization.agents.real import real_agents
from agent_civilization.core import contracts as C
from agent_civilization.core.output_validators import apply_output_verification
from agent_civilization.pipeline import dag

SPEC = "analyst"


@pytest.fixture(autouse=True)
def _fast_dag(monkeypatch):
    dag.RUNS.clear()
    monkeypatch.setattr(dag, "STAGE_RETRY_BASE_S", 0.0)
    monkeypatch.setattr(dag, "STAGE_RETRY_CAP_S", 0.0)
    yield
    dag.RUNS.clear()


def _gate_on(monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")


def _agent(monkeypatch, impl, handler):
    """وكيل حقيقي (``execute`` الحقيقي) بمعالج بديل — نمط دفعة 3."""
    from agent_civilization.agents.real.real_agents import create_civilization
    agent = create_civilization(1, impl=impl)[SPEC][0]
    if impl == "real":
        async def fake_dispatch(specialization, work):
            return handler(work)
        monkeypatch.setattr(real_agents, "_dispatch", fake_dispatch)
    else:
        async def fake_run_inner(work):
            return {"ok": True, "result": handler(work)}
        monkeypatch.setattr(agent, "_run_inner", fake_run_inner)
    return agent


def _plan(*stages):
    return {"primary": "p", "reason": "r", "goal": "g", "stages": list(stages)}


def _stage(name="s1", deps=None, **kw):
    d = {"name": name, "specialization": SPEC, "prompt": f"do {name}"}
    if deps is not None:
        d["depends_on"] = deps
    d.update(kw)
    return d


def _run_plan(plan, agent):
    return asyncio.run(dag.run_plan(plan, {SPEC: [agent]}))


def _forged(work):
    return {"ok": True, "answer": "The task completed successfully.",
            "error": None, "tool_output": {"exit_code": 1, "failed": 20}}


def _bare_claim(work):
    """نجاح بلا دليل أداة ولا ادعاء نصي صريح — يمر بلا متطلبات."""
    return {"ok": True, "answer": "here is the report", "error": None,
            "tool_output": None}


def _verified(work):
    return {"ok": True, "answer": "computed", "error": None,
            "tool_output": {"exit_code": 0, "rows": 4}}


IMPLS = ["real", "specialists"]


# ================================================================ DAG path

@pytest.mark.parametrize("impl", IMPLS)
def test_char_dag_forged_stage_fails_when_gate_on(impl, monkeypatch):
    """CHAR: نجاح مزوّر (ادعاء نصي + exit_code=1) بلا متطلبات مرحلة —
    كشف «النجاح المزوّر» في البوابة يكفي: المرحلة ``failed`` لا ``succeeded``،
    والخطأ رمز البوابة الثابت، والتشغيل ``failed``."""
    _gate_on(monkeypatch)
    agent = _agent(monkeypatch, impl, _forged)
    rep = _run_plan(_plan(_stage()), agent)
    s = rep["stages"][0]
    assert s["status"] == dag.FAILED and s["ok"] is False
    assert s["error"] == "output_verification_failed"
    assert rep["status"] == "failed"
    assert rep["final"] == ""  # لا مخرج نهائي من مرحلة مرفوضة


@pytest.mark.parametrize("impl", IMPLS)
def test_char_dag_gate_off_keeps_legacy_success(impl, monkeypatch):
    """CHAR (توافق خلفي): التعطيل الصريح (0): المرحلة المزوّرة تنجح كما كانت."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "0")
    agent = _agent(monkeypatch, impl, _forged)
    rep = _run_plan(_plan(_stage()), agent)
    assert rep["stages"][0]["status"] == dag.SUCCEEDED
    assert rep["status"] == "completed"


@pytest.mark.parametrize("impl", IMPLS)
def test_dag_stage_verification_reaches_the_gate(impl, monkeypatch):
    """RED: مرحلة تعلن ``verification: {tool_output: true}`` ومعالج ينجح بلا
    أي مخرج أداة — يجب أن تفشل. قبل الدفعة 4 لم يمرر ``_work_for`` المتطلبات
    إلى ``work`` فنجحت المرحلة بلا دليل."""
    _gate_on(monkeypatch)
    seen = []

    def handler(work):
        seen.append(work.get("verification"))
        return _bare_claim(work)

    agent = _agent(monkeypatch, impl, handler)
    rep = _run_plan(_plan(_stage(verification={"tool_output": True})), agent)
    assert seen == [{"tool_output": True}], "stage verification must reach work"
    s = rep["stages"][0]
    assert s["status"] == dag.FAILED, f"{impl}: unverified stage must not succeed"
    assert s["error"] == "output_verification_failed"


@pytest.mark.parametrize("impl", IMPLS)
def test_dag_stage_verification_accepts_real_evidence(impl, monkeypatch):
    """RED (الوجه الإيجابي): المتطلب نفسه مع دليل أداة سليم → ``succeeded``
    والحكم مسجل في القطعة الناتجة؛ ويُثبت أن التمرير لا يكسر النجاح الصادق."""
    _gate_on(monkeypatch)
    seen = []

    def handler(work):
        seen.append(work.get("verification"))
        return _verified(work)

    agent = _agent(monkeypatch, impl, handler)
    rep = _run_plan(_plan(_stage(verification={"tool_output": True})), agent)
    assert seen == [{"tool_output": True}]
    assert rep["stages"][0]["status"] == dag.SUCCEEDED
    assert rep["status"] == "completed"


@pytest.mark.parametrize("impl", IMPLS)
def test_dag_gated_failure_is_retried_then_succeeds(impl, monkeypatch):
    """RED: رفض البوابة فشل مرحلة عادي يخضع لإعادة المحاولة المحلية —
    المحاولة 1 بلا دليل (مرفوضة) ثم المحاولة 2 بدليل → ``succeeded``
    بـ``attempts=2``. (يحتاج تمرير المتطلبات — بدونها تنجح المحاولة 1.)"""
    _gate_on(monkeypatch)
    calls = {"n": 0}

    def handler(work):
        calls["n"] += 1
        return _bare_claim(work) if calls["n"] == 1 else _verified(work)

    agent = _agent(monkeypatch, impl, handler)
    rep = _run_plan(_plan(_stage(max_attempts=2, verification={"tool_output": True})), agent)
    s = rep["stages"][0]
    assert s["status"] == dag.SUCCEEDED and s["attempts"] == 2
    assert rep["budget"]["stage_runs_used"] == 2


@pytest.mark.parametrize("impl", IMPLS)
def test_dag_dependents_of_a_gated_stage_do_not_run(impl, monkeypatch):
    """RED: مرحلة مرفوضة بالبوابة لا تُشغّل تابعيها — التابع ``blocked`` ولا
    يصله أي استدعاء، فلا ينتشر نجاح غير موثق عبر الرسم."""
    _gate_on(monkeypatch)
    stages_seen = []

    def handler(work):
        stages_seen.append(work.get("stage"))
        return _bare_claim(work)

    agent = _agent(monkeypatch, impl, handler)
    rep = _run_plan(_plan(_stage("a", [], verification={"tool_output": True}),
                          _stage("b", ["a"])), agent)
    by = {s["name"]: s for s in rep["stages"]}
    assert by["a"]["status"] == dag.FAILED
    assert by["b"]["status"] == dag.BLOCKED  # حالة الإيقاف الموثقة للتابع (R-165)
    assert stages_seen == ["a"]


def test_char_dag_stage_without_verification_keeps_work_shape():
    """CHAR: مرحلة بلا ``verification`` لا تضيف المفتاح إلى ``work`` (شكل
    الحمولة القائم محفوظ للمعالجات الحالية)."""
    run = dag.Run(dag.normalize_plan(_plan(_stage())), None, None, None, None)
    work = dag._work_for(run, "s1", "g", [])
    assert "verification" not in work


@pytest.mark.parametrize("bad", ["tool_output", ["tool_output"], 1, True])
def test_normalize_plan_rejects_malformed_stage_verification(bad):
    """RED: متطلبات مرحلة ليست كائنًا تُرفض عند التحقق من الخطة (PlanError) —
    فشل مبكر صريح بدل اكتشافه بعد تشغيل الوكيل."""
    with pytest.raises(dag.PlanError, match="verification must be an object"):
        dag.normalize_plan(_plan(_stage(verification=bad)))


def test_normalize_plan_accepts_object_and_null_verification():
    """RED (حد التحقق): الكائن مقبول ويُنسخ (لا يشارك المرجع مع المدخل)؛
    و``None`` يعني «بلا متطلبات»."""
    req = {"json": ["rows"]}
    n = dag.normalize_plan(_plan(_stage(verification=req)))
    assert n["stages"][0]["verification"] == {"json": ["rows"]}
    assert n["stages"][0]["verification"] is not req
    n2 = dag.normalize_plan(_plan(_stage(verification=None)))
    assert n2["stages"][0].get("verification") is None


def test_dag_unknown_stage_requirement_is_rejected_not_ignored(monkeypatch):
    """RED: متطلب مرحلة غير معروف يصل إلى البوابة فيُرفض صراحة (لا تجاهل
    صامت) — المرحلة ``failed`` والسبب يسمي المتطلب."""
    _gate_on(monkeypatch)
    agent = _agent(monkeypatch, "real", _verified)
    rep = _run_plan(_plan(_stage(verification={"sandbox_proof": True})), agent)
    s = rep["stages"][0]
    assert s["status"] == dag.FAILED
    assert s["error"] == "output_verification_failed"


# ================================================== other result formats

def _hr(**kw):
    return C.HandlerResult(**kw)


def test_handler_result_forged_success_is_rejected(monkeypatch):
    """RED: ``HandlerResult`` (pydantic) يدّعي النجاح بمخرج أداة فاشل —
    قبل الدفعة 4 كانت البوابة تفحص المغلف لا الحقول (``_result_of`` لا يرى
    نموذج pydantic كـMapping) فيمر النجاح المزوّر ``unverified``."""
    _gate_on(monkeypatch)
    ok, res = apply_output_verification(
        None, True, _hr(ok=True, answer="The task completed successfully.",
                        tool_output={"exit_code": 1}))
    assert ok is False
    assert res["error"] == "output_verification_failed"
    assert res["verification"]["original_ok"] is True


def test_handler_result_with_real_evidence_is_accepted(monkeypatch):
    """RED: ``HandlerResult`` بدليل أداة سليم ومتطلب ``tool_output`` — كان
    يُرفض خطأً (المغلف بلا ``tool_output``)؛ الآن يُقبل والحكم مسجل."""
    _gate_on(monkeypatch)
    ok, res = apply_output_verification(
        {"tool_output": True}, True,
        _hr(ok=True, answer="computed", tool_output={"exit_code": 0, "rows": 4}))
    assert ok is True
    assert res["verification"]["ok"] is True
    assert "tool_output" in res["verification"]["checks_run"]


def test_handler_result_inner_contradiction_is_rejected(monkeypatch):
    """RED: نجاح خارجي مع ``HandlerResult.ok=False`` تناقض يُرفض."""
    _gate_on(monkeypatch)
    ok, res = apply_output_verification(None, True, _hr(ok=False, error="boom"))
    assert ok is False
    assert any("contradiction" in r for r in res["verification"]["reasons"])
    assert res["verification"]["original_error"] == "boom"


def test_char_handler_result_gate_off_is_untouched(monkeypatch):
    """CHAR: التعطيل الصريح (0): النموذج يعود كما هو (الكائن نفسه)."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "0")
    hr = _hr(ok=True, answer="The task completed successfully.")
    ok, res = apply_output_verification(None, True, hr)
    assert ok is True and res is hr


@pytest.mark.parametrize("raw,expect_ok", [
    ("The task completed successfully.", False),   # نص يدّعي النجاح بلا دليل
    ("here is the summary", True),                 # نص بلا ادعاء — unverified
    (None, True),                                  # بلا إجابة ولا ادعاء
    (["row1", "row2"], True),                      # قائمة — لا نص إجابة
])
def test_char_specialists_non_dict_results_through_execute(raw, expect_ok, monkeypatch):
    """CHAR: نتائج غير قاموسية في specialists تُلف ``{"ok", "answer": r}`` ثم
    تمر بالبوابة: النص المدّعي للنجاح يُرفض، والباقي يُقبل ``unverified``."""
    _gate_on(monkeypatch)
    agent = _agent(monkeypatch, "specialists", lambda w: raw)
    out = asyncio.run(agent.execute({"prompt": "x"}))
    assert out["ok"] is expect_ok
    if expect_ok:
        assert out["result"]["verification"]["unverified"] is True
    else:
        assert out["result"]["error"] == "output_verification_failed"


@pytest.mark.parametrize("raw", [None, "plain", ["a"]])
def test_char_specialists_non_dict_results_fail_requirements(raw, monkeypatch):
    """CHAR: النتائج نفسها مع متطلب ``tool_output`` تُرفض كلها — لا دليل أداة."""
    _gate_on(monkeypatch)
    agent = _agent(monkeypatch, "specialists", lambda w: raw)
    out = asyncio.run(agent.execute({"prompt": "x", "verification": {"tool_output": True}}))
    assert out["ok"] is False
    assert out["result"]["error"] == "output_verification_failed"


@pytest.mark.parametrize("impl", IMPLS)
def test_char_result_without_ok_key_is_honest_failure(impl, monkeypatch):
    """CHAR: نتيجة بلا مفتاح ``ok`` = لا ادعاء نجاح → فشل صادق لا تمسه البوابة."""
    _gate_on(monkeypatch)
    agent = _agent(monkeypatch, impl, lambda w: {"answer": "completed successfully"})
    out = asyncio.run(agent.execute({"prompt": "x"}))
    assert out["ok"] is False
    assert "verification" not in out["result"]


@pytest.mark.parametrize("payload,expect_ok", [
    ('{"rows": 4, "total": 10}', True),
    ('{"total": 10}', False),        # مفتاح مطلوب مفقود
    ("not json", False),
    ("[1, 2]", False),               # JSON ليس كائنًا
])
def test_char_json_string_payload_through_execute(payload, expect_ok, monkeypatch):
    """CHAR: حمولة ``tool_output`` نصية JSON مع ``json: ["rows"]`` عبر execute()."""
    _gate_on(monkeypatch)
    agent = _agent(monkeypatch, "real",
                   lambda w: {"ok": True, "answer": "computed", "tool_output": payload})
    out = asyncio.run(agent.execute({"prompt": "x", "verification": {"json": ["rows"]}}))
    assert out["ok"] is expect_ok
