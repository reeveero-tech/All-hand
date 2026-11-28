"""Deterministic financial model: the agent states tagged inputs, the code computes and renders.

Regression for production run 6a03d324e329 and the v5-v7 studies: financial tasks were rejected for the model's
own arithmetic (2,250,000 + 900,000 + 225,000 + 112,500 written as 1,237,500) and for figures attributed to sources
that did not contain them.
"""
import asyncio
import json

import pytest

from app import finmodel
from app.orchestrator import Orchestrator, Run, apply_audit, audit_arithmetic, audit_tables
from tests.test_core import FakeLLM, plan_of

GOAL = ("ادرس فرصة إطلاق قهوة باردة معلبة في السوق السعودي واكتب تقريرًا ماليًا بميزانية 200 ألف ريال")
MODEL = {"currency": "ريال", "months": 12,
         "price": {"value": 9, "tag": "[E1]"},
         "unit_cost": {"value": 4, "tag": "افتراض: مكونات وعبوة 250 مل"},
         "units_per_month": {"low": 2000, "base": 5000, "high": 9000, "tag": "افتراض: توزيع في 40 منفذًا"},
         "fixed_monthly": [{"name": "إيجار", "value": 6000, "tag": "افتراض: مستودع صغير في جدة"},
                           {"name": "رواتب", "value": 9000, "tag": "افتراض: موظفان"}],
         "startup": [{"name": "معدات", "value": 60000, "tag": "افتراض: آلة تعبئة مستعملة"},
                     {"name": "تسويق الإطلاق", "value": 30000, "tag": "افتراض"}]}
EVID = {"E1": "سعر عبوة القهوة الباردة 250 مل في المتجر 9 ريال", "E2": "عدد السكان 35 مليون"}


def chk(user=GOAL):
    return finmodel.Checker(EVID.get, user)


def test_financial_tasks_are_detected_and_others_are_not():
    assert finmodel.is_financial({"title": "التحليل المالي ونقطة التعادل", "instructions": ""})
    assert finmodel.is_financial({"title": "x", "instructions": "احسب الإيرادات والتكاليف"})
    assert not finmodel.is_financial({"title": "تحليل المنافسين", "instructions": "اذكر الأسعار المعروضة"})


def test_computation_is_exact():
    m, errs = finmodel.normalize(MODEL, chk())
    assert not errs
    c = finmodel.compute(m, 200000)
    assert c["fixed_monthly"] == 15000 and c["startup"] == 90000 and c["margin"] == 5
    assert c["breakeven_units"] == 3000
    base = c["scenarios"]["base"]
    assert (base["revenue"], base["variable"], base["profit"]) == (45000, 20000, 10000)
    assert base["period_profit"] == 120000 and base["net"] == 30000
    assert base["roi_pct"] == 33.33 and base["payback_months"] == 9.0
    low = c["scenarios"]["low"]
    assert low["profit"] == 18000 - 8000 - 15000 == -5000 and low["payback_months"] is None
    assert c["budget"]["need_worst_case"] == 90000 + 5000 * 12 and c["budget"]["fits"] is True


def test_rendered_tables_pass_the_arithmetic_audit():
    info = finmodel.apply({"output": "تحليل.\n\n{{النموذج_المالي}}\n\nخلاصة.", "model": MODEL}, chk(), 200000)
    assert not info["errors"]
    out = info["output"]
    assert "{{" not in out and "محسوب بالنظام" in out and out.index("تحليل") < out.index("محسوب") < out.index("خلاصة")
    assert audit_arithmetic(out) == [] and audit_tables(out) == []
    assert apply_audit({"pass": True, "score": 8, "issues": []}, out)["pass"]


def test_tags_are_checked_against_what_the_system_read():
    m, _ = finmodel.normalize({**MODEL, "price": {"value": 12, "tag": "[E1]"},
                               "unit_cost": {"value": 4, "tag": "[E9]"}}, chk())
    assert m["price"]["kind"] == "assumption" and "لم يثبت" in m["price"]["tag"]  # 12 is not in E1
    assert m["unit_cost"]["kind"] == "assumption"  # E9 was never read
    assert any("الرقم غير موجود في نص الدليل" in p for p in m["problems"])
    assert any("الدليل غير موجود" in p for p in m["problems"])
    ok, _ = finmodel.normalize(MODEL, chk())
    assert ok["price"]["kind"] == "evidence" and ok["price"]["tag"] == "[E1]"


def test_user_tag_must_match_the_users_words():
    m, _ = finmodel.normalize({**MODEL, "startup": [{"name": "تأسيس", "value": 200000, "tag": "من المستخدم"},
                                                    {"name": "مخزون", "value": 50000, "tag": "من المستخدم"}]}, chk())
    assert m["startup"][0]["kind"] == "user"
    assert m["startup"][1]["kind"] == "assumption" and "ليس في طلب المستخدم" in m["startup"][1]["tag"]


def test_untagged_assumption_is_flagged_and_bad_inputs_are_refused():
    m, _ = finmodel.normalize({**MODEL, "startup": [{"name": "x", "value": 1000, "tag": ""}]}, chk())
    assert m["startup"][0]["tag"] == "افتراض بلا تبرير" and m["problems"]
    for bad in [{**MODEL, "price": {"value": 3, "tag": "افتراض: x"}},  # below unit cost
                {**MODEL, "units_per_month": {"low": 9, "base": 5, "high": 1, "tag": "افتراض: x"}},
                {**MODEL, "price": {"value": "غير متاح", "tag": "x"}}]:
        m, errs = finmodel.normalize(bad, chk())
        assert m is None and errs


def test_total_words_in_item_names_cannot_trip_the_table_audit():
    m, _ = finmodel.normalize({**MODEL, "fixed_monthly": [{"name": "إجمالي الرواتب", "value": 9000, "tag": "افتراض: x"}]}, chk())
    assert "إجمالي" not in m["fixed"][0]["name"]


def test_missing_block_is_not_a_failure_but_guides_an_arithmetic_retry():
    info = finmodel.apply({"output": "الميزانية 200,000؛ التسويق 40% منها"}, chk(), None)
    assert info["missing"]
    assert finmodel.verdict({"pass": True, "score": 9, "issues": []}, info)["pass"]
    v = finmodel.verdict({"pass": False, "score": 4, "issues": ["خطأ"], "fix": "", "arithmetic": [{}]}, info)
    assert not v["pass"] and "model" in v["fix"]


def test_invalid_block_fails_with_exact_feedback():
    info = finmodel.apply({"output": "x", "model": {**MODEL, "price": {"value": 2, "tag": "افتراض: x"}}}, chk(), None)
    assert not info.get("missing") and info["errors"]
    v = finmodel.verdict({"pass": True, "score": 9, "issues": []}, info)
    assert not v["pass"] and "model" in v["fix"] and "التكلفة المتغيرة" in v["issues"][0]


def test_fenced_block_is_accepted_and_removed():
    out = "تحليل\n```agos-model\n" + json.dumps(MODEL, ensure_ascii=False) + "\n```"
    info = finmodel.apply({"output": out}, chk(), None)
    assert not info["errors"] and "agos-model" not in info["output"] and "units_per_month" not in info["output"]


def test_orchestrator_computes_the_financial_task_and_tells_the_reviewer():
    task = {"id": "t1", "title": "التحليل المالي ونقطة التعادل", "role": "analyst", "instructions": "احسب الربحية",
            "acceptance": "ok", "depends_on": [], "decompose": False}
    seen = {}

    def agent(messages):
        seen.setdefault("task", messages[1]["content"])
        return json.dumps({"action": "final", "output": "التحليل:\n{{النموذج_المالي}}", "confidence": 0.8,
                           "model": MODEL}, ensure_ascii=False)
    fake = FakeLLM(plan_of(task), agent=agent)
    run = Run(goal=GOAL, llm_public={"provider": "fake"}, max_agents=10, clarify=False)
    asyncio.run(Orchestrator(run, fake).execute())
    t = run.tasks["t1"]
    assert "النموذج المالي يحسبه النظام" in seen["task"]
    assert t["status"] == "done" and "محسوب بالنظام" in t["output"]
    assert t["fin"]["computed"]["scenarios"]["base"]["profit"] == 10000
    assert t["fin"]["computed"]["budget"]["value"] == 200000  # the user's budget, read from the goal
    assert any(e["type"] == "financial_model" and e["ok"] for e in run.events)


def test_non_financial_task_is_untouched():
    task = {"id": "t1", "title": "تحليل المنافسين", "role": "writer", "instructions": "اذكر المنافسين",
            "acceptance": "ok", "depends_on": [], "decompose": False}
    fake = FakeLLM(plan_of(task))
    run = Run(goal=GOAL, llm_public={"provider": "fake"}, max_agents=10, clarify=False)
    asyncio.run(Orchestrator(run, fake).execute())
    assert "fin" not in run.tasks["t1"] and run.tasks["t1"]["output"] == "ناتج الوكيل"


def test_method_and_budget_need_are_explicit():
    loss = {**MODEL, "units_per_month": {"low": 1000, "base": 5000, "high": 9000, "tag": "افتراض: x"}}
    gain = {**MODEL, "units_per_month": {"low": 4000, "base": 5000, "high": 9000, "tag": "افتراض: x"}}
    for m_raw, phrase in [(gain, "تكاليف التأسيس وحدها"), (loss, "وتغطية الخسارة 12 شهرًا")]:
        m, _ = finmodel.normalize(m_raw, chk())
        out = finmodel.render(m, finmodel.compute(m, 200000))
        assert "طريقة الحساب" in out and phrase in out
        assert audit_arithmetic(out) == [] and audit_tables(out) == []
    m, _ = finmodel.normalize(loss, chk())
    assert "90,000 + 10,000 × 12 = 210,000" in finmodel.render(m, finmodel.compute(m, 200000))
    assert finmodel.compute(m, 200000)["budget"]["fits"] is False


def test_display_is_not_padded_and_roi_keeps_two_decimals():
    assert finmodel._f(-33.33) == "-33.33" and finmodel._f(2.6) == "2.6" and finmodel._f(5.0) == "5"
    m, _ = finmodel.normalize({**MODEL, "units_per_month": {"low": 4000, "base": 6000, "high": 10000, "tag": "افتراض: x"},
                               "fixed_monthly": [{"name": "ثابتة", "value": 15000, "tag": "افتراض: x"}],
                               "startup": [{"name": "تأسيس", "value": 90000, "tag": "افتراض: x"}]}, chk())
    c = finmodel.compute(m)
    assert c["scenarios"]["low"]["roi_pct"] == -33.33 and c["scenarios"]["high"]["payback_months"] == 2.57


def test_user_tag_check_is_per_sentence():
    from app import quality
    para = ("الوحدات منخفض 4,000 وأساسي 6,000 (افتراض: للمقارنة). سعر العبوة والتكاليف والميزانية مأخوذة من المستخدم.")
    assert quality.false_user_tags(para, "سعر 9 ريال") == []
    assert quality.false_user_tags("الطلب 50,000 عبوة (من المستخدم).", "سعر 9 ريال")


# ------------------------------------------------------------------ production run 755e194ad2fd
def test_placeholder_never_reaches_the_report_when_the_block_is_missing():
    # Attempt 1 wrote the {{النموذج_المالي}} line and no model field; that attempt was kept and the final file showed
    # the raw placeholder where the tables should be.
    info = finmodel.apply({"output": "تحليل\n\n{{النموذج_المالي}}\n\nخلاصة"}, chk(), None)
    assert "{{" not in info["output"] and "النموذج المالي لم يُحسب" in info["output"]
    assert not info["missing"]  # the agent meant to send a model: the attempt fails with feedback
    v = finmodel.verdict({"pass": True, "score": 9, "issues": []}, info)
    assert not v["pass"] and "{{النموذج_المالي}}" in v["issues"][0]


def test_placeholder_never_reaches_the_report_when_the_block_is_invalid():
    bad = {**MODEL, "units_per_month": {"low": "قليل", "base": "متوسط", "high": "كثير"}}
    info = finmodel.apply({"output": "تحليل\n{{النموذج_المالي}}", "model": bad}, chk(), None)
    assert info["errors"] and "{{" not in info["output"] and "النموذج المالي لم يُحسب" in info["output"]
    assert "قليل" in info["errors"][0] and "units_per_month" in info["raw"]  # what was received is kept for diagnosis


@pytest.mark.parametrize("units", [
    {"low": {"value": 2000, "tag": "افتراض: x"}, "base": {"value": 5000, "tag": "افتراض: x"},
     "high": {"value": 9000, "tag": "افتراض: x"}},
    {"low": "2,000 عبوة", "base": "5,000 عبوة", "high": "9,000 عبوة", "tag": "افتراض: x"},
    {"متحفظ": 2000, "أساسي": 5000, "متفائل": 9000, "tag": "افتراض: x"},
    {"conservative": 2000, "base": 5000, "optimistic": 9000, "tag": "افتراض: x"},
    [2000, 5000, 9000],
])
def test_common_forms_of_monthly_units_are_read(units):
    info = finmodel.apply({"output": "{{النموذج_المالي}}", "model": {**MODEL, "units_per_month": units}}, chk(), None)
    assert not info["errors"], info["errors"]
    assert info["model"]["units"] == {"low": 2000, "base": 5000, "high": 9000}


@pytest.mark.parametrize("bad", ["8,000–12,000 عبوة", "نحو عشرة آلاف", None, -5])
def test_ranges_words_and_negatives_are_still_refused(bad):
    info = finmodel.apply({"output": "x", "model": {**MODEL, "units_per_month": {"low": bad, "base": 5000, "high": 9000}}},
                          chk(), None)
    assert info["errors"] and info["model"] is None
