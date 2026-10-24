#!/usr/bin/env python3
"""اختبارات R-170 (دفعة 5): إعلان متطلبات التحقق في خطط الموجّه (أحمر أولًا).

قبل هذه الدفعة كان ``routing.auto_router.route`` يبني خططًا بلا أي
``verification`` — فتفعيل ``AGOS_OUTPUT_VALIDATORS`` على ``POST
/civilization/execute`` لا يطبق إلا كشف النجاح المزوّر الاستدلالي.

السياسة المختبرة: المراحل التي يُنفذها **معالج أداة** يُرجع عند نجاحه
``tool_output`` غير فارغ دائمًا (مقيس من ``real_dispatcher``: ``_test_runner``،
``_network_client``، ``_validator``، ``_researcher``) تعلن
``{"tool_output": True}``. مراحل نصية بحتة (LLM: understand/plan/review
والتنفيذ النصي) **لا** تعلن متطلبات — لا دليل آلي متاح لها، والادعاء بخلاف
ذلك يولّد رفضًا كاذبًا. ``citations`` لا تُعلن للباحث: المدقق يبحث عن
الاستشهادات في المستوى الأعلى للنتيجة، والباحث يضعها في
``tool_output.sources`` — إعلانها يرفض نجاحًا صادقًا (حد موثق).

التصنيف: ``RED`` = يفشل سلوكيًا قبل التغيير؛ ``CHAR`` = توصيف لسلوك قائم.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_civilization.agents.real import real_agents
from agent_civilization.core.output_validators import KNOWN_REQUIREMENTS
from agent_civilization.pipeline import dag
from agent_civilization.routing import auto_router
from agent_civilization.routing.auto_router import route

TOOL = {"tool_output": True}
# الدفعة 6: الباحث يعلن citations أيضًا (المدقق يبحث داخل tool_output منذها)
RESEARCH = {"tool_output": True, "citations": True}
EXPECTED = {"test_runner": TOOL, "network_client": TOOL, "validator": TOOL, "researcher": RESEARCH,
            "api_integrator": TOOL, "code_generator": {"code": True}}  # batch 10
# التخصصات المدعومة بأداة (مقيسة من real_dispatcher) — مستقلة عن الشيفرة المختبرة
TOOL_SPECS = {"test_runner", "network_client", "validator", "researcher", "api_integrator", "code_generator"}  # batch 10

PROMPTS = {
    "code": "write a python function to compute fibonacci numbers",
    "analysis": "analyze these numbers: 3, 5, 8, 13, 21",
    "api": "integrate with the endpoint https://example.com/api",
    "strategy": "propose a market strategy and roadmap for our product",
    "architecture": "design a scalable microservice architecture",
    "tests": "run tests for: def add(a, b): return a + b",
    "validate": 'validate this JSON: {"name": "x"}',
    "review_test": "review and test this code: def f(): return 1",
}


def _by_name(plan):
    return {s["name"]: s for s in plan["stages"]}


@pytest.fixture(autouse=True)
def _fast_dag(monkeypatch):
    dag.RUNS.clear()
    monkeypatch.setattr(dag, "STAGE_RETRY_BASE_S", 0.0)
    monkeypatch.setattr(dag, "STAGE_RETRY_CAP_S", 0.0)
    yield
    dag.RUNS.clear()


# ============================================================ plan shape

def test_policy_table_exists_and_uses_known_requirements_only():
    """RED: سياسة صريحة قابلة للتدقيق، ومفاتيحها كلها ضمن KNOWN_REQUIREMENTS
    (متطلب غير معروف تُرفضه البوابة — يجب ألا يولده الموجّه)."""
    policy = auto_router.STAGE_VERIFICATION
    assert set(policy) == TOOL_SPECS
    for spec, req in policy.items():
        assert req == EXPECTED[spec], spec
        assert set(req) <= set(KNOWN_REQUIREMENTS), spec


@pytest.mark.parametrize("key,stage,spec", [
    ("code", "test", "test_runner"),
    ("analysis", "validate", "validator"),
    ("api", "probe", "network_client"),
    ("strategy", "ground", "researcher"),
    ("architecture", "ground", "researcher"),
    ("review_test", "test", "test_runner"),
])
def test_tool_backed_verify_stages_declare_tool_output(key, stage, spec):
    """RED: مراحل التحقق المدعومة بأداة تعلن ``tool_output``."""
    st = _by_name(route(PROMPTS[key]))[stage]
    assert st["specialization"] == spec
    assert st.get("verification") == EXPECTED[spec]


@pytest.mark.parametrize("key,spec", [("tests", "test_runner"), ("validate", "validator")])
def test_tool_backed_execute_stage_declares_tool_output(key, spec):
    """RED: مرحلة التنفيذ نفسها تعلن المتطلب حين يكون التخصص الأساسي أداة."""
    plan = route(PROMPTS[key])
    assert plan["primary"] == spec
    assert _by_name(plan)["execute"].get("verification") == TOOL


@pytest.mark.parametrize("key", sorted(PROMPTS))
def test_char_text_only_stages_declare_nothing(key):
    """CHAR (ويبقى بعد التغيير): المراحل النصية بلا مفتاح ``verification``
    إطلاقًا — شكل المرحلة القائم محفوظ، ولا متطلب بلا دليل آلي ممكن."""
    for st in route(PROMPTS[key])["stages"]:
        if st["specialization"] not in TOOL_SPECS:
            assert "verification" not in st, (key, st["name"])


def test_declared_requirements_are_independent_copies():
    """RED: كل مرحلة تحمل نسخة مستقلة — تعديلها لا يمس السياسة ولا خطة أخرى."""
    a = _by_name(route(PROMPTS["code"]))["test"]["verification"]
    a["tool_output"] = False
    a["json"] = True
    assert auto_router.STAGE_VERIFICATION["test_runner"] == TOOL
    assert _by_name(route(PROMPTS["code"]))["test"]["verification"] == TOOL


def test_router_plans_pass_dag_normalization_with_requirements():
    """RED: خطط الموجّه تمر بـ``normalize_plan`` والمتطلبات محفوظة فيها
    (هذا ما يعرضه ``POST /civilization/plan``)."""
    for key, text in PROMPTS.items():
        n = dag.normalize_plan(route(text))
        declared = {s["name"]: s.get("verification") for s in n["stages"]}
        tool_stages = [s["name"] for s in n["stages"] if s["specialization"] in TOOL_SPECS]
        assert tool_stages, key
        spec_of = {s["name"]: s["specialization"] for s in n["stages"]}
        for name in tool_stages:
            assert declared[name] == EXPECTED[spec_of[name]], (key, name)


# ======================================================= through the DAG

def _patch_dispatch(monkeypatch, handler):
    async def fake_dispatch(specialization, work):
        return handler(specialization, work)
    monkeypatch.setattr(real_agents, "_dispatch", fake_dispatch)


def _civ():
    from agent_civilization.agents.real.real_agents import create_civilization
    return create_civilization(1, impl="real")


def _evidence_free(specialization, work):
    """كل مرحلة «تنجح» بإجابة نصية بلا ``tool_output`` ولا ادعاء نصي مزوّر."""
    return {"ok": True, "answer": f"{specialization} output", "error": None,
            "tool_output": None}


def _code_ok_else_evidence_free(specialization, work):
    """Batch 10: code_generator has a default ``code`` requirement; a realistic
    code stage returns the code text (all real paths now do)."""
    if specialization == "code_generator":
        return {"ok": True, "answer": "generated", "error": None,
                "tool_output": {"generated": True, "code": "def f():\n    return 1\n"}}
    return _evidence_free(specialization, work)


def test_router_plan_code_stage_without_code_fails_when_gate_on(monkeypatch):
    """RED (batch 10): the execute stage (code_generator) "succeeds" with no
    code text ⇒ failed (output_verification_failed); downstream is blocked."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    _patch_dispatch(monkeypatch, _evidence_free)
    rep = asyncio.run(dag.run_plan(route(PROMPTS["code"]), _civ()))
    by = {s["name"]: s for s in rep["stages"]}
    assert by["execute"]["status"] == dag.FAILED
    assert by["execute"]["error"] == "output_verification_failed"
    assert rep["status"] in ("failed", "blocked")


def test_router_plan_tool_stage_without_evidence_fails_when_gate_on(monkeypatch):
    """RED: خطة كود من الموجّه، والعلم مفعل، ومرحلة ``test`` «تنجح» بلا مخرج
    أداة ⇒ ``failed`` (output_verification_failed). قبل الدفعة 5 كانت تنجح
    لأن الخطة لا تعلن شيئًا. المراحل النصية تنجح ``unverified`` كما هي."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    _patch_dispatch(monkeypatch, _code_ok_else_evidence_free)
    rep = asyncio.run(dag.run_plan(route(PROMPTS["code"]), _civ()))
    by = {s["name"]: s for s in rep["stages"]}
    assert by["test"]["status"] == dag.FAILED
    assert by["test"]["error"] == "output_verification_failed"
    for name in ("understand", "plan", "execute", "review"):
        assert by[name]["status"] == dag.SUCCEEDED, name
    assert rep["status"] == "failed"


def test_char_router_plan_gate_off_is_unchanged(monkeypatch):
    """CHAR: العلم معطل — المتطلبات المعلنة لا أثر لها (السلوك التاريخي)."""
    monkeypatch.delenv("AGOS_OUTPUT_VALIDATORS", raising=False)
    _patch_dispatch(monkeypatch, _evidence_free)
    rep = asyncio.run(dag.run_plan(route(PROMPTS["code"]), _civ()))
    assert all(s["status"] == dag.SUCCEEDED for s in rep["stages"])
    assert rep["status"] == "completed"


def test_router_plan_with_tool_evidence_succeeds_and_records_check(monkeypatch):
    """RED: المعالج يعيد دليل أداة لمرحلة ``test`` ⇒ ``succeeded`` والفحص
    ``tool_output`` مسجل فعلًا (لا مجرد ``unverified``)."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    seen = {}

    def handler(spec, work):
        seen[work.get("stage")] = work.get("verification")
        if spec == "test_runner":
            return {"ok": True, "answer": "exit=0; 1 test(s) passed, 0 failed",
                    "tool_output": {"stdout": "PASS t\n", "tests_passed": 1,
                                    "tests_failed": 0, "sandbox": True}}
        return _code_ok_else_evidence_free(spec, work)

    _patch_dispatch(monkeypatch, handler)
    rep = asyncio.run(dag.run_plan(route(PROMPTS["code"]), _civ()))
    assert rep["status"] == "completed"
    assert seen["test"] == TOOL
    assert seen["execute"] == {"code": True}          # batch 10: code_generator default
    assert all(seen[n] is None for n in ("understand", "plan", "review"))


# ================================== real handlers satisfy their requirement

def test_real_validator_success_satisfies_router_requirement(monkeypatch):
    """RED: المعالج الحقيقي ``_validator`` (بلا شبكة ولا LLM) عبر
    ``agent.execute`` والعلم مفعل والمتطلب من الموجّه ⇒ مقبول بفحص
    ``tool_output`` مسجل — السياسة لا ترفض نجاحًا صادقًا."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    plan = route(PROMPTS["validate"])
    st = _by_name(plan)["execute"]
    agent = _civ()["validator"][0]
    out = asyncio.run(agent.execute({"prompt": st["prompt"],
                                     "verification": st.get("verification")}))
    assert out["ok"] is True, out
    assert "tool_output" in out["result"]["verification"]["checks_run"]


@pytest.mark.code_exec
def test_real_test_runner_success_satisfies_router_requirement(monkeypatch):
    """RED: المعالج الحقيقي ``_test_runner`` (sandbox محلي) يمر بمتطلب
    الموجّه — نجاحه الصادق يحمل ``tool_output`` بلا ``exit_code`` فاشل."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    plan = route(PROMPTS["tests"])
    st = _by_name(plan)["execute"]
    agent = _civ()["test_runner"][0]
    out = asyncio.run(agent.execute({"prompt": st["prompt"],
                                     "verification": st.get("verification")}))
    assert out["ok"] is True, out
    assert "tool_output" in out["result"]["verification"]["checks_run"]
