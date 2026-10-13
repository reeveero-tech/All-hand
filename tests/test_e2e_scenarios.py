"""R-055: سيناريوهات شاملة من البداية للنهاية.

القبول: "3 سيناريوهات على الأقل (هدف معقد → خطة متعددة الوكلاء → نتيجة
مخزنة) تعمل آلياً في CI أو في اختبار تحقق موثق".

كل سيناريو: هدف → `route()` ينتج خطة متعددة الوكلاء → `execute_plan()`
ينفذها عبر الحضارة الحقيقية → تُوثق النتيجة النهائية (مخزنة في TASKS
مثل مسار الخادم الحي). السيناريوهات تعمل آلياً في CI (لا اعتماد على
شبكة خارجية إلا في السيناريو الأول، ومصدرها مصمم ليكون مستقراً).

السيناريوهات:
1. هدف برمجي معقد: code → review → test (5 مراحل، 5 وكلاء).
2. هدف تحليلي: understand → plan → execute → validate (4 مراحل، 4 وكلاء).
3. هدف بناء معماري: understand → plan → execute → review → test (5 مراحل، 5 وكلاء).

ملاحظة: هذه تختبر المسار الآلي (route + execute_plan) — التخزين الفعلي
يغطيه مسار الخادم (POST /api/tasks) الذي يغطيه R-043.
"""
import asyncio

import pytest
from fastapi.testclient import TestClient

from agent_civilization.agents.real import create_civilization
from agent_civilization.pipeline.orchestrator import execute_plan
from agent_civilization.routing.auto_router import route


def _make_civ():
    return create_civilization(5)


def test_scenario_1_code_review_test_pipeline():
    """هدف معقد: مراجعة واختبار كود — خطة 5 مراحل عبر 5 وكلاء من تخصصات مختلفة."""
    civ = _make_civ()
    goal = "review and test this code: print(1+1)"
    plan = route(goal)
    assert plan["primary"] == "code_generator"
    assert len(plan["stages"]) >= 4, f"خطة قصيرة: {[s['name'] for s in plan['stages']]}"

    result = asyncio.run(execute_plan(plan, civ, data="print(1+1)"))

    # كل المراحل نُفذت بنجاح
    for stage in result["stages"]:
        assert stage["ok"], f"مرحلة {stage['name']} فشلت: {stage['output']}"

    # مراحل مختلفة استخدمت وكلاء مختلفين من تخصصات مختلفة
    specs_used = {s["specialization"] for s in result["stages"]}
    assert len(specs_used) >= 4, f"تخصصات قليلة: {specs_used}"

    # النتيجة النهائية مخزنة (final is the last ok stage's output)
    assert result["final"], "لا نتيجة نهائية"
    assert result["elapsed_s"] >= 0
    # النتيجة النهائية من مرحلة test: exit=0 (نجاح التنفيذ)
    assert "exit=0" in result["final"], f"نتيجة غير متوقعة: {result['final']}"


def test_scenario_2_analysis_pipeline():
    """هدف تحليلي: understand → plan → execute → validate."""
    civ = _make_civ()
    goal = "analyze the data for trends"
    plan = route(goal)
    assert plan["primary"] == "analyst"
    assert len(plan["stages"]) >= 4

    result = asyncio.run(execute_plan(plan, civ, data=[1, 2, 3, 4, 5]))

    for stage in result["stages"]:
        assert stage["ok"], f"مرحلة {stage['name']} فشلت: {stage['output']}"
    assert result["final"], "لا نتيجة نهائية"


def test_scenario_3_architectural_build_pipeline():
    """هدف بناء معماري: understand → plan → execute → review → test."""
    civ = _make_civ()
    goal = "build a Python project structure"
    plan = route(goal)
    assert plan["primary"] in {"code_generator", "builder", "architect"}
    assert len(plan["stages"]) >= 4

    result = asyncio.run(execute_plan(plan, civ, data='print("hello")'))

    for stage in result["stages"]:
        assert stage["ok"], f"مرحلة {stage['name']} فشلت: {stage['output']}"
    assert result["final"], "لا نتيجة نهائية"
    # النتيجة النهائية من مرحلة test
    assert "exit=0" in result["final"], f"نتيجة غير متوقعة: {result['final']}"


def test_scenario_stages_use_different_agents():
    """كل مرحلة تُسند لوكيل مختلف (خطة متعددة الوكلاء فعلياً)."""
    civ = _make_civ()
    goal = "review and test this code: print(1+1)"
    plan = route(goal)
    result = asyncio.run(execute_plan(plan, civ, data="print(1+1)"))
    agents_used = [s["agent"] for s in result["stages"]]
    assert len(set(agents_used)) == len(agents_used), f"وكيل واحد نفذ كل المراحل: {agents_used}"


def test_scenario_report_contains_timing_and_outputs():
    """التقرير يحوي زمن كل مرحلة ومخرجاتها (توثيق قابل للتحقق)."""
    civ = _make_civ()
    plan = route("analyze the data for trends")
    result = asyncio.run(execute_plan(plan, civ, data=[1, 2, 3, 4, 5]))
    for stage in result["stages"]:
        assert isinstance(stage["elapsed_s"], float)
        assert stage["elapsed_s"] >= 0
        assert isinstance(stage["output"], str)
    assert result["elapsed_s"] >= 0


@pytest.fixture
def e2e_client(monkeypatch):
    """Client with auth disabled and feature flag set (R-055 e2e).

    A prior test (tests/test_security.py) may have left `server` reloaded
    with AGOS_API_KEY set (ENABLE_AUTH=True). We reload server explicitly
    with the env cleared so auth is off for this test.
    """
    monkeypatch.delenv("AGOS_API_KEY", raising=False)
    monkeypatch.setenv("ROUTES_FEATURE", "1")
    import importlib
    import server as srv
    importlib.reload(srv)
    return TestClient(app=srv.app)


def test_scenario_full_pipeline_stores_result_in_task_store(e2e_client):
    """سيناريو كامل: POST /civilization/execute → GET /api/tasks/{id} يعيد النتيجة المخزنة.

    يثبت أن هدف → خطة متعددة الوكلاء → نتيجة مخزنة (المعيار الحرفي لـR-055)
    يعمل عبر مسار الخادم الحي — لا فقط في مسار الاختبار المباشر.
    """
    c = e2e_client
    resp = c.post("/civilization/execute",
                  json={"message": "analyze the data for trends", "data": [1, 2, 3, 4, 5]})
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["final"] == "valid python object"
    assert all(s["ok"] for s in d["stages"]), d["stages"]
    # النتيجة مخزنة في TASKS عبر معرف المهمة
    resp2 = c.get(f"/api/tasks/{d['task_id']}")
    assert resp2.status_code == 200
    stored = resp2.json()
    assert stored["status"] == "completed"
    assert stored["result"]["final"] == "valid python object"
    assert len(stored["result"]["stages"]) >= 4


def test_validator_rejects_invalid_json_string():
    """R-055a: الـvalidator يرفض نصاً ليس JSON — لا تساهل يمرر أي سلسلة.

    إصلاح لتساهل أُدخل في R-055 (كان يعيد ok=True لأي نص) ثم ثبت أنه غير
    لازم: السيناريوهات تنجح لأن البيانات تُمرر لكل المراحل، لا لأن التحقق ضعيف.
    """
    from agent_civilization.core.agents import real_dispatcher as rd
    bad = asyncio.run(rd.dispatch("validator", {"prompt": "not json at all", "data": None}))
    assert bad["ok"] is False and "invalid JSON" in bad["error"]
    good = asyncio.run(rd.dispatch("validator", {"prompt": '{"a": 1}'}))
    assert good["ok"] is True and good["answer"] == "valid JSON"
