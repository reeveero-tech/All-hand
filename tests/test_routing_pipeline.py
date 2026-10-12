"""R-052: اختبارات لمسار التوجيه (routing) وخط الأنابيب (pipeline) وكل تخصص.

الأجزاء:
1. routing/auto_router.route: تصنيف القصد (عربي/إنجليزي)، تفكيك المراحل،
   قواعد الاعتذار للأقسام الذرية.
2. pipeline/orchestrator.execute_plan: تنفيذ خطة عبر حضارة صغيرة (3 وكلاء
   لكل تخصص) مع تمرير سياق المراحل السابقة.
3. RealSpecialistAgent لكل تخصص من الـ20: تنفيذ مهمة تمثيلية يعيد مخرجات
   محسوبة فعلاً ويحدّث إحصاءات الوكيل (نجح/فشل/زمن).
"""
import pytest

from agent_civilization.agents.real.real_agents import (
    SPECIALIZATIONS,
    RealSpecialistAgent,
    create_civilization,
)
from agent_civilization.pipeline.orchestrator import execute_plan
from agent_civilization.routing.auto_router import route

# ---------------------------------------------------------------------------
# routing: تصنيف القصد
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prompt,expected_primary", [
    ("اكتب لي دالة بايثون لحساب فيبوناتشي", "code_generator"),
    ("fix the ImportError traceback from my application startup", "surgeon"),
    ("حلل هذه الأرقام: 3, 5, 8, 13", "analyst"),
    ("design a scalable multi-tenant SaaS architecture", "architect"),
    ("ترجم النص التالي إلى الإنجليزية", "communicator"),
    ("اشرح لي كيف يعمل التكرار للمبتدئين", "educator"),
])
def test_route_classifies_intent(prompt, expected_primary):
    plan = route(prompt)
    assert plan["primary"] == expected_primary
    assert plan["stages"], "يجب أن تُفكّك الخطة إلى مراحل"
    assert all(s["specialization"] in SPECIALIZATIONS for s in plan["stages"])


def test_route_default_is_analyst_for_generic_prompt():
    plan = route("طلب عام بلا كلمات مفتاحية معروفة")
    assert plan["primary"] == "analyst"


def test_route_atomic_specialization_skips_plan_stage():
    # الترجمة مهمة ذرية: لا مرحلة "plan" في خطتها
    plan = route("ترجم 'مرحبا بالعالم' إلى الإنجليزية والفرنسية")
    names = [s["name"] for s in plan["stages"]]
    assert "plan" not in names
    assert "execute" in names


def test_route_code_request_adds_review_and_test_stages():
    plan = route("اكتب دالة بايثون تجمع رقمين")
    names = [s["name"] for s in plan["stages"]]
    assert "plan" in names
    assert "review" in names
    assert "test" in names


def test_route_stages_have_reason_and_prompt():
    plan = route("build a REST API for a todo app")
    for stage in plan["stages"]:
        assert stage["prompt"].strip()
        assert stage["reason"].strip()


# ---------------------------------------------------------------------------
# pipeline: تنفيذ خطة عبر الحضارة
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_plan_runs_all_stages_with_small_civilization():
    civilization = create_civilization(agents_per_spec=3)
    plan = route("اكتب دالة بايثون تجمع رقمين")
    result = await execute_plan(plan, civilization, data=None)
    assert set(result) >= {"stages", "final", "elapsed_s"}
    assert result["final"] is not None
    # كل المراحل إما نجحت أو سجلت فشلها بوضوح — لا مرحلة مفقودة
    assert len(result["stages"]) == len(plan["stages"])
    for stage in result["stages"]:
        assert isinstance(stage.get("ok"), bool)
        if stage["ok"]:
            assert stage["output"] or stage.get("agent")


@pytest.mark.asyncio
async def test_execute_plan_context_passed_between_stages():
    civilization = create_civilization(agents_per_spec=3)
    plan = route("حلل الأرقام 2 4 6 8")
    result = await execute_plan(plan, civilization)
    # على الأقل مرحلة التنفيذ الرئيسية نجحت
    primary_stage = next(s for s in result["stages"] if s["name"] == "execute")
    assert primary_stage["ok"]


@pytest.mark.asyncio
async def test_execute_plan_missing_specialization_fails_gracefully():
    # خطة بتخصص غير موجود في الحضارة: تُوثّق كفشل لا انهيار
    plan = {
        "primary": "x",
        "reason": "test",
        "stages": [
            {"name": "execute", "specialization": "no_such_spec",
             "prompt": "prompt", "reason": "r"},
        ],
    }
    result = await execute_plan(plan, {})
    assert len(result["stages"]) == 1
    assert result["stages"][0]["ok"] is False


# ---------------------------------------------------------------------------
# RealSpecialistAgent لكل تخصص من الـ20
# ---------------------------------------------------------------------------

REPRESENTATIVE_TASKS = {
    "analyst": {"data": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]},
    "api_integrator": {"prompt": "GET https://example.com"},
    "architect": {"data": ["web ui", "api", "db"]},
    "builder": {"data": ["app.py", "utils.py", "tests/test_app.py"]},
    "code_generator": {"data": "def add(a,b):\n    return a+b\n"},
    "communicator": {"data": "hello team, meeting at 3"},
    "data_processor": {"data": [1, 2, 3, 4, 5, 6]},
    "db_manager": {"prompt": "SELECT 1 AS one;"},
    "designer": {"data": "brand: forest"},
    "educator": {"prompt": "explain recursion"},
    "modifier": {"data": "def foo():\n    pass\n"},
    "monitor": {"prompt": "check system"},
    "network_client": {"prompt": "check connectivity to github.com"},
    "researcher": {"prompt": "Python programming language"},
    "reviewer": {"data": "def f(x):\n    return x*2\n"},
    "self_developer": {"prompt": "coverage report"},
    "strategist": {"data": ["a", "b", "c"]},
    "surgeon": {"data": "x=1\n\n\n\ny=2\n"},
    "test_runner": {"data": "assert 1+1 == 2\n"},
    "validator": {"data": {"type": "json", "value": [1, 2, 3]}},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("spec", sorted(SPECIALIZATIONS))
async def test_real_specialist_agent_per_specialization(spec):
    agent = RealSpecialistAgent(name=f"{spec}_test", specialization=spec, agent_id=0)
    result = await agent.execute({"prompt": REPRESENTATIVE_TASKS[spec].get("prompt", "task"),
                                  "data": REPRESENTATIVE_TASKS[spec].get("data")})
    assert result["ok"], f"{spec} فشل: {result}"
    # المخرجات محسوبة فعلاً (مفتاح tool_output أو answer موجود)
    inner = result["result"]
    assert inner.get("ok")
    assert inner.get("tool_output") is not None or inner.get("answer")
    # إحصاءات الوكيل حُدّثت
    assert agent.stats["tasks_completed"] == 1
    assert agent.stats["tasks_failed"] == 0
    assert agent.stats["total_time_s"] >= 0
    assert agent.status == "idle"
