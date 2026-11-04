"""R-192 — المهارات الإجرائية المقيسة (فوق وحدة الخبرة القائمة).

القاعدة التي يفرضها الخارطة: **لا مساواة حفظ الخبرة باكتساب مهارة**.
طبقة الخبرة (R-167) تسجل ذكريات حلقات المهام وتسترجعها — و D-046 يُبقي
المعالجات حتمية. هذا الملف يختبر الطبقة الجديدة: **المهارة الإجرائية**
— إجراء قابل للاستدعاء، منسوخ من خبرة موثقة المصدر، قابل للتنفيذ كخطوات،
مقيس الأثر على مهمة مرجعية.

صنف الدليل هنا: شيفرة حقيقية بلا محاكاة للمكونات تحت الاختبار —
  * المسار الحقيقي كاملًا (RealSpecialistAgent.execute → run_with_experience
    → المعالج الحتمي الحقيقي) بمستأجر حقيقي (SEC-02)؛
  * طبقة الخبرة الحقيقية تسجل الدليل المصدري الموثق (لا إدخالات مصنوعة
    يدويًا)؛
  * المهارة تُكتسب من تلك الدخلات الموثقة نفسها (المنشأ قابل للتتبع)؛
  * القياس: نفس المهمة المرجعية قبل الاكتساب (أحمر) وبعده (أخضر)،
    بالأرقام.

المهمة المرجعية (حتمية قابلة لإعادة الاختبار): محلل مالي بمدخلات أرقام
مفصولة بفواصل التجميع — «1,200» بدل 1200 — وهو تنسيق شائع حقيقي؛
المحلل الحتمي لا يحلها (float("1,200") يفشل) فيسقط صامتًا إلى تحليل
نصي بلا أرقام: **إجابة خاطئة النوع عن سؤال رقمي** — أسوأ من الفشل
الصريح لأن ok=True.

قبل المهارة (الأحمر): الإجابة نصية، بلا مجموع صحيح.
بعد المهارة (الأخضر): sum=7200 (n=3) — المجموع الصحيح.
"""
import re
import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from agent_civilization.core import tenancy  # noqa: E402
from agent_civilization.core import experience  # noqa: E402

TENANT = "r192_t"
REFERENCE_WORK = {"prompt": "what is the total of the quarterly figures?",
                  "data": ["1,200", "2,400", "3,600"]}
CORRECT_SUM_MARKER = "sum=7200"   # 1200 + 2400 + 3600


def _agent():
    from agent_civilization.agents.real.real_agents import RealSpecialistAgent
    return RealSpecialistAgent(name="r192-analyst", specialization="analyst",
                               agent_id=1)


async def _run_reference_task():
    """المهمة المرجعية عبر المسار الحقيقي (وكيل + خبرة + معالج)."""
    return await _agent().execute(dict(REFERENCE_WORK))


def _measure(out) -> dict:
    """قياس قابل للتحقق: هل أجاب المحلل رقميًا؟ هل المجموع صحيح؟
    (مخرج الوكيل مغلف: الجواب تحت result)"""
    inner = out.get("result") if isinstance(out.get("result"), dict) else out
    answer = str(inner.get("answer") or out.get("answer") or "")
    return {
        "ok": bool(out.get("ok")),
        "numeric_answer": bool(re.search(r"n=\d+", answer)),
        "correct_sum": CORRECT_SUM_MARKER in answer,
    }


# ----------------------------------------------------- 1. الأحمر أولًا (القياس قبل الاكتساب)
@pytest.mark.asyncio
async def test_r192_reference_task_red_without_skill():
    """القياس الأول (أحمر): المهمة المرجعية بلا أي مهارة — المحلل يسقط
    صامتًا إلى تحليل نصي: ok=True لكن الإجابة بلا أرقام وبلا المجموع
    المطلوب. هذا هو الدليل المصدري الذي ستُكتسب منه المهارة."""
    token = tenancy.set_tenant(TENANT)
    try:
        experience.reset()
        out = await _run_reference_task()
        m = _measure(out)
        assert m["ok"] is True          # نجاح زائف: الإجابة خاطئة النوع
        assert m["numeric_answer"] is False, out.get("answer")
        assert m["correct_sum"] is False
        inner = out.get("result") if isinstance(out.get("result"), dict) else out
        assert "no numeric samples" in str(inner.get("answer"))
    finally:
        tenancy.reset_tenant(token)


# ----------------------------------------------------- 2. لا مهارة بلا منشأ موثق (سلبي)
@pytest.mark.asyncio
async def test_r192_no_skill_invented_without_documented_source():
    """سلبي: بلا خبرة موثقة من فئة الفشل المعروفة لا تُخترع مهارة —
    الاكتساب يعيد None (لا مهارة بلا مصدر موثق قابل للتتبع)."""
    from agent_civilization.core import skills
    token = tenancy.set_tenant(TENANT)
    try:
        experience.reset()
        skills.reset()
        # لا دخلات خبرة إطلاقًا → لا مهارة
        assert await skills.acquire_from_experience(TENANT, "analyst") is None
        # خبرة موثقة لكنها ليست فئة الفشل المعروفة (نص نظيف بلا أرقام
        # مجمعة) → لا مهارة
        async def _plain_call():
            return {"ok": True, "answer": "done"}
        await experience.run_with_experience(
            "analyst", "t", {"prompt": "plain text task"}, _plain_call)
        assert await skills.acquire_from_experience(TENANT, "analyst") is None
    finally:
        tenancy.reset_tenant(token)


# ----------------------------------------------------- 3. الاكتساب من الخبرة الموثقة
@pytest.mark.asyncio
async def test_r192_acquire_from_documented_experience():
    """المهارة تُكتسب من الدخلات الموثقة نفسها: تشغيل المهمة المرجعية
    (الأحمر أعلاه) سجل دخلات الخبرة الحقيقية؛ الاكتساب يمسحها، يتعرف على
    فئة الفشل (أرقام مجمعة في المدخل + «no numeric samples» في الإجابة
    الموثقة)، ويبني مهارة إجرائية بمصدر قابل للتتبع (معرفات الدخلات)."""
    from agent_civilization.core import skills
    token = tenancy.set_tenant(TENANT)
    try:
        experience.reset()
        skills.reset()
        # الدليل المصدري الموثق: تشغيل المهمة الحقيقي (بلا مهارة بعد)
        out = await _run_reference_task()
        assert _measure(out)["correct_sum"] is False
        skill = await skills.acquire_from_experience(TENANT, "analyst")
        assert skill is not None
        assert skill["skill_id"] == "analyst.normalize_grouped_numbers"
        assert skill["specialization"] == "analyst"
        # المنشأ الموثق: معرفات الدخلات المصدر (ليست فارغة — لا مهارة بلا مصدر)
        assert skill["source_memory_ids"], "the skill must cite its source entries"
        # الإجراء قابل للتنفيذ كخطوات
        assert isinstance(skill["steps"], list) and skill["steps"]
        assert skill["steps"][0]["action"] == "normalize_grouped_numbers"
        # المحفز: نمط الأرقام المجمعة
        assert skill["trigger"]
        # المهارة نشطة وقابلة للاستدعاء
        assert skill["status"] == "active"
        listed = await skills.list_skills(TENANT)
        assert [s["skill_id"] for s in listed] == [skill["skill_id"]]
    finally:
        tenancy.reset_tenant(token)


# ----------------------------------------------------- 4. الأخضر (القياس بعد الاكتساب)
@pytest.mark.asyncio
async def test_r192_reference_task_green_with_skill():
    """القياس الثاني (أخضر): نفس المهمة المرجعية بعد اكتساب المهارة —
    خطوات المهارة تحوّل المدخل (نسخة، الأصل لا يُمس) قبل المعالج،
    والمحلل يجيب المجموع الصحيح. الاستدعاء مسجل بمخرجه ومنشأه."""
    from agent_civilization.core import skills
    token = tenancy.set_tenant(TENANT)
    try:
        experience.reset()
        skills.reset()
        # 1) الأحمر الموثق (نفس تشغيل الاختبار 1 — يسجل الدليل المصدري)
        before = _measure(await _run_reference_task())
        assert before["correct_sum"] is False
        # 2) الاكتساب من الدليل الموثق
        skill = await skills.acquire_from_experience(TENANT, "analyst")
        assert skill is not None
        # 3) الأخضر: نفس المهمة عبر نفس المسار الحقيقي
        out = await _run_reference_task()
        after = _measure(out)
        assert after["correct_sum"] is True, out.get("answer")
        assert after["numeric_answer"] is True
        inner = out.get("result") if isinstance(out.get("result"), dict) else out
        assert "n=3" in str(inner.get("answer"))   # العينات الثلاث حُلّلت فعلًا
        # الاستدعاء مسجل بمخرجه (القياس شرط الادعاء لا الوجود)
        invocations = skills.invocations_of(TENANT, skill["skill_id"])
        assert invocations, "the invocation must be recorded"
        last = invocations[-1]
        assert last["outcome_ok"] is True
        assert last["skill_id"] == "analyst.normalize_grouped_numbers"
        # عدادات المهارة تحركت
        st = skills.stats()[TENANT]["analyst.normalize_grouped_numbers"]
        assert st["invocations"] >= 1 and st["ok"] >= 1
    finally:
        tenancy.reset_tenant(token)


# ----------------------------------------------------- 5. المقارنة قبل/بعد بالأرقام
@pytest.mark.asyncio
async def test_r192_before_after_comparison_numbers():
    """المقارنة الصريحة التي يطلبها المعيار: نفس المهمة المرجعية،
    قياس قبل الاكتساب وقياس بعده، بالأرقام القابلة للتحقق —
    صحة المجموع: 0 → 1؛ إجابة رقمية: لا → نعم؛ عينات محللة: 0 → 3."""
    from agent_civilization.core import skills
    token = tenancy.set_tenant(TENANT)
    try:
        experience.reset()
        skills.reset()
        before = _measure(await _run_reference_task())
        await skills.acquire_from_experience(TENANT, "analyst")
        after = _measure(await _run_reference_task())
        # الأرقام: قبل = الصفر الكامل على السؤال الرقمي، بعد = الصحيح
        assert before == {"ok": True, "numeric_answer": False, "correct_sum": False}
        assert after == {"ok": True, "numeric_answer": True, "correct_sum": True}
    finally:
        tenancy.reset_tenant(token)


# ----------------------------------------------------- 6. لا تحويل بلا تطابق (سلبي)
@pytest.mark.asyncio
async def test_r192_skill_does_not_touch_non_matching_work():
    """سلبي: العمل بلا أرقام مجمعة لا يُحوَّل ولا يُستدعى له شيء —
    المهارة إجرائية مشروطة، ليست تعديلًا عامًا للمعالج."""
    from agent_civilization.core import skills
    token = tenancy.set_tenant(TENANT)
    try:
        experience.reset()
        skills.reset()
        await _run_reference_task()  # يوثق الفشل
        await skills.acquire_from_experience(TENANT, "analyst")
        clean = {"prompt": "what is the total of 1200 and 2400 and 3600?",
                 "data": [1200, 2400, 3600]}
        out = await _agent().execute(dict(clean))
        assert _measure(out)["correct_sum"] is True
        # لا استدعاء مهارة لهذا العمل (المدخل نظيف)
        inv = skills.invocations_of(TENANT, "analyst.normalize_grouped_numbers")
        assert not inv  # لم يُسجل أي استدعاء — العمل النظيف لا يحتاج المهارة
    finally:
        tenancy.reset_tenant(token)


# ----------------------------------------------------- 7. عزل المستأجرين (SEC-02)
@pytest.mark.asyncio
async def test_r192_tenant_isolation_sec02():
    """مهارة المستأجر A لا تعمل لمستأجر B: الاكتساب والاستدعاء
    والعدادات كلها مقيدة بالمستأجر (SEC-02)."""
    from agent_civilization.core import skills
    token = tenancy.set_tenant(TENANT)
    try:
        experience.reset()
        skills.reset()
        await _run_reference_task()  # خبرة المستأجر A
        skill_a = await skills.acquire_from_experience(TENANT, "analyst")
        assert skill_a is not None
        token_b = tenancy.set_tenant("r192_other")
        try:
            # B لا يرى مهارة A ولا يكتسب شيئًا من خبرة A
            assert await skills.acquire_from_experience("r192_other", "analyst") is None
            listed_b = await skills.list_skills("r192_other")
            assert listed_b == []
            # مهمة B المرجعية تبقى حمراء (بلا مهارة)
            out = await _run_reference_task()
            assert _measure(out)["correct_sum"] is False
            inv_b = skills.invocations_of("r192_other", skill_a["skill_id"])
            assert not inv_b
        finally:
            tenancy.reset_tenant(token_b)
        # عدادات A لم تتأثر بـ B
        st_a = skills.stats()[TENANT]["analyst.normalize_grouped_numbers"]
        assert st_a["invocations"] == 0
    finally:
        tenancy.reset_tenant(token)


# ----------------------------------------------------- 8. الأصل لا يُمس (نسخة)
@pytest.mark.asyncio
async def test_r192_original_work_not_mutated():
    """خطوات المهارة تحوّل نسخة من العمل؛ الأصل لا يُعدل أبدًا."""
    from agent_civilization.core import skills
    token = tenancy.set_tenant(TENANT)
    try:
        experience.reset()
        skills.reset()
        await _run_reference_task()
        await skills.acquire_from_experience(TENANT, "analyst")
        original = {"prompt": "total of 1,200 and 2,400?", "data": ["1,200", "2,400"]}
        snapshot = {"prompt": original["prompt"], "data": list(original["data"])}
        work, inv = await skills.maybe_apply("analyst", original, TENANT)
        assert inv is not None
        # الأصل لم يتغير
        assert original == snapshot
        # النسخة المحولة تغيرت (فواصل التجميع أزيلت)
        assert work["data"] == ["1200", "2400"]
        assert work["prompt"] == "total of 1200 and 2400?"
    finally:
        tenancy.reset_tenant(token)


# ----------------------------------------------------- 9. تعطيل الطبقة (سلبي)
@pytest.mark.asyncio
async def test_r192_skills_layer_can_be_disabled(monkeypatch):
    """سلبي: AGOS_SKILLS=0 يعطل الطبقة كلها — لا اكتساب ولا استدعاء،
    والمهمة المرجعية تعود حمراء كما قبل."""
    from agent_civilization.core import skills
    monkeypatch.setenv("AGOS_SKILLS", "0")
    token = tenancy.set_tenant(TENANT)
    try:
        experience.reset()
        skills.reset()
        await _run_reference_task()
        assert await skills.acquire_from_experience(TENANT, "analyst") is None
        out = await _run_reference_task()
        assert _measure(out)["correct_sum"] is False
    finally:
        tenancy.reset_tenant(token)


# ----------------------------------------------------- 10. الخبرة تبقى سليمة (انحدار)
@pytest.mark.asyncio
async def test_r192_experience_contract_untouched():
    """انحدار: الطبقة الجديدة لا تكسر عقد الخبرة (R-167) — التسجيل
    والاسترجاع يعملان كما قبل، ومدخل خبرة المهمة المساعدة للمهارة يحمل
    أثر المهارة (المنشأ قابل للتتبع في الدخلة نفسها)."""
    from agent_civilization.core import skills
    token = tenancy.set_tenant(TENANT)
    try:
        experience.reset()
        skills.reset()
        await _run_reference_task()
        await skills.acquire_from_experience(TENANT, "analyst")
        out = await _run_reference_task()
        assert _measure(out)["correct_sum"] is True
        st = experience.mem_store()
        entries = [e for e in st.entries()
                   if e.metadata.get("tenant_id") == TENANT
                   and e.metadata.get("layer") == "experience"]
        assert len(entries) >= 2   # الأحمر + الأخضر على الأقل
        # دخلة التشغيل الأخضر تحمل أثر المهارة
        skilled = [e for e in entries if e.metadata.get("skill_invoked")]
        assert skilled, "the skilled run's entry must carry the skill trace"
        assert skilled[-1].metadata["skill_invoked"] == "analyst.normalize_grouped_numbers"
        # والاسترجاع ما زال يعمل (عقد R-167)
        rec = await experience.recall("analyst", REFERENCE_WORK, tenant_id=TENANT)
        assert isinstance(rec, list)
    finally:
        tenancy.reset_tenant(token)
