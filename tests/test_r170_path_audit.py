#!/usr/bin/env python3
"""R-170 (دفعة 7): تدقيق مسارات إعلان النجاح — لا نجاح بلا التحقق المطلوب (أحمر أولًا).

المسارات التي تُعلن «نجاحًا» في المشروع (مقيسة بالبحث في الشيفرة):

1. ``agent.execute`` للوكتين (``real_agents``/``specialist_runtime``) — مغطاة
   بالبوابة منذ الدفعة 3؛ هنا تُمسح **كل** التخصصات العشرين لا تخصص واحد.
2. ``server._run_task`` (المهام عبر ``POST /api/tasks``) وإنهاء الطابور الدائم
   (``_finish_durable``): الحالة ``completed``/``failed`` مشتقة من
   ``outcome["ok"]`` الذي يعيده ``agent.execute`` المُبوّب. **فجوة**: العمل
   القادم من ``/api/tasks`` بلا ``verification`` ⇒ مهمة ``test_runner`` تنجح
   بلا أي مخرج أداة (تطبق البوابة الكشف الاستدلالي فقط).
3. DAG (``pipeline/dag.py``) — عبر ``agent.execute`` (دفعة 4).
4. مسار المحور القديم ``_execute_work`` في قوالب المتخصصين (20 ملفًا في
   ``_templates/*_base.py``): خامل في الإنتاج (لا شيفرة إنتاج تستدعي
   ``add_work``/``start``) لكنه مسار معلن: كان **بلا بوابة** ويعدّ
   ``tasks_completed`` حتى حين تفشل النتيجة.

الإصلاح المختبر: سياسة متطلبات افتراضية واحدة حسب التخصص في
``core/output_validators.DEFAULT_REQUIREMENTS`` تُطبق حين لا يعلن العمل
``verification`` (والموجّه يقرأ منها)؛ ``verification: {}`` الصريح = بلا
متطلبات (للمستدعين الداخليين فقط — HTTP لا يقبله). وقالب المحور يمر بالبوابة
ويعدّ النجاح/الفشل حسب الحكم.

التصنيف: ``RED`` يفشل سلوكيًا قبل التغيير؛ ``CHAR`` توصيف لسلوك قائم.
"""
from __future__ import annotations

import asyncio
import importlib

import pytest
from starlette.testclient import TestClient

from agent_civilization.agents.real import real_agents
from agent_civilization.core.agents import real_dispatcher

import server
import tests.test_r149_security_qa_gaps as r149

# fixtures shared with R-149 (local PostgREST, SSE capture, isolated TASKS)
postgrest = r149.postgrest
published = r149.published
clean_tasks = r149.clean_tasks

TOOL_SPECS = {"test_runner", "network_client", "validator", "researcher"}
IMPLS = ["real", "specialists"]


def _all_specs():
    from agent_civilization.agents.real.real_agents import create_civilization
    return sorted(create_civilization(1, impl="real"))


ALL_SPECS = _all_specs()
TEXT_SPECS = [s for s in ALL_SPECS if s not in TOOL_SPECS]


def _forged(spec, work):
    return {"ok": True, "answer": "The task completed successfully.",
            "tool_output": {"exit_code": 1, "failed": 3}}


def _evidence_free(spec, work):
    return {"ok": True, "answer": f"{spec} output", "tool_output": None}


def _patch(monkeypatch, fn):
    async def disp(spec, work):
        return fn(spec, work)
    monkeypatch.setattr(real_agents, "_dispatch", disp)
    monkeypatch.setattr(real_dispatcher, "dispatch", disp)


def _agent(impl, spec):
    from agent_civilization.agents.real.real_agents import create_civilization
    return create_civilization(1, impl=impl)[spec][0]


@pytest.fixture
def gate_on(monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")


# ======================================= 1. agent.execute — all 20 specs

def test_char_civilization_has_twenty_specializations():
    assert len(ALL_SPECS) == 20 and TOOL_SPECS <= set(ALL_SPECS)


@pytest.mark.parametrize("impl", IMPLS)
@pytest.mark.parametrize("spec", ALL_SPECS)
def test_char_forged_success_rejected_for_every_specialization(spec, impl, monkeypatch, gate_on):
    """CHAR (مسح جديد لسلوك الدفعة 3): نجاح مزوّر يُرفض في كل تخصص ووكتة،
    والعداد ``tasks_failed`` لا ``tasks_completed``."""
    _patch(monkeypatch, _forged)
    a = _agent(impl, spec)
    out = asyncio.run(a.execute({"prompt": "x"}))
    assert out["ok"] is False and out["result"]["error"] == "output_verification_failed"
    assert a.stats["tasks_completed"] == 0 and a.stats["tasks_failed"] == 1


@pytest.mark.parametrize("impl", IMPLS)
@pytest.mark.parametrize("spec", sorted(TOOL_SPECS))
def test_tool_specs_require_evidence_by_default(spec, impl, monkeypatch, gate_on):
    """RED: عمل بلا ``verification`` (شكل ``/api/tasks``) لتخصص أداة ونجاح بلا
    مخرج أداة ⇒ مرفوض بالمتطلب الافتراضي (كان مقبولًا ``unverified``)."""
    _patch(monkeypatch, _evidence_free)
    out = asyncio.run(_agent(impl, spec).execute({"prompt": "x"}))
    assert out["ok"] is False, out["result"]
    assert out["result"]["error"] == "output_verification_failed"


@pytest.mark.parametrize("impl", IMPLS)
@pytest.mark.parametrize("spec", TEXT_SPECS)
def test_char_text_specs_without_evidence_are_unverified_not_rejected(spec, impl, monkeypatch, gate_on):
    """CHAR: التخصصات النصية (16) بلا متطلبات افتراضية ⇒ قبول ``unverified``
    (لا دليل آلي لها — رفضها يكون رفضًا كاذبًا)."""
    _patch(monkeypatch, _evidence_free)
    out = asyncio.run(_agent(impl, spec).execute({"prompt": "x"}))
    assert out["ok"] is True and out["result"]["verification"]["unverified"] is True


@pytest.mark.parametrize("impl", IMPLS)
def test_char_explicit_empty_requirements_opt_out(impl, monkeypatch, gate_on):
    """CHAR: ``verification: {}`` صريح = بلا متطلبات (مستدع داخلي يعلن ذلك)."""
    _patch(monkeypatch, _evidence_free)
    out = asyncio.run(_agent(impl, "test_runner").execute({"prompt": "x", "verification": {}}))
    assert out["ok"] is True


def test_default_policy_is_single_source_for_router():
    """RED: سياسة واحدة في core يقرأها الموجّه (لا جدولان قد يتباعدان)."""
    from agent_civilization.core import output_validators as ov
    from agent_civilization.routing import auto_router
    assert ov.DEFAULT_REQUIREMENTS == auto_router.STAGE_VERIFICATION
    assert set(ov.DEFAULT_REQUIREMENTS) == TOOL_SPECS
    assert ov.requirements_for("test_runner", {"prompt": "x"}) == {"tool_output": True}
    assert ov.requirements_for("analyst", {"prompt": "x"}) is None
    assert ov.requirements_for("test_runner", {"verification": {"json": True}}) == {"json": True}
    assert ov.requirements_for("test_runner", {"verification": {}}) == {}
    got = ov.requirements_for("researcher", {})
    got["x"] = 1
    assert "x" not in ov.DEFAULT_REQUIREMENTS["researcher"]  # a copy


# ============================================ 2. HTTP /api/tasks (server)

def _post_task(c, tenant_auth, spec, prompt):
    r = c.post("/api/tasks", json={"specialization": spec, "prompt": prompt},
               headers=tenant_auth.headers)
    assert r.status_code == 202, r.text
    return r.json()["task_id"]


def test_http_task_tool_spec_without_evidence_ends_failed(
        tenant_auth, postgrest, published, clean_tasks, monkeypatch, gate_on):
    """RED: ``POST /api/tasks`` لـtest_runner، المعالج «ينجح» بلا مخرج أداة ⇒
    المهمة ``failed`` وسبب البوابة في النتيجة والحدث ``ok=False``."""
    _patch(monkeypatch, _evidence_free)
    with TestClient(server.app) as c:
        tid = _post_task(c, tenant_auth, r149.SPEC, r149.MESSAGE)
        assert r149._wait_final(tid, published) == "failed"
    rec = server.TASKS[tid]
    assert rec["result"]["ok"] is False
    assert rec["result"]["result"]["error"] == "output_verification_failed"
    assert ("test_tenant", "task_completed", False) in [(t, e["type"], e.get("ok")) for t, e in published]


def test_char_http_task_forged_success_ends_failed(
        tenant_auth, postgrest, published, clean_tasks, monkeypatch, gate_on):
    """CHAR: نجاح مزوّر عبر HTTP ⇒ ``failed`` (البوابة في execute منذ الدفعة 3)."""
    _patch(monkeypatch, _forged)
    with TestClient(server.app) as c:
        tid = _post_task(c, tenant_auth, r149.SPEC, r149.MESSAGE)
        assert r149._wait_final(tid, published) == "failed"


def test_char_http_task_with_tool_evidence_completes(
        tenant_auth, postgrest, published, clean_tasks, monkeypatch, gate_on):
    """CHAR (يبقى بعد التغيير): دليل أداة سليم ⇒ ``completed`` والفحص مسجل."""
    def honest(s, w):
        return {"ok": True, "answer": "exit=0; 1 test(s) passed, 0 failed",
                "tool_output": {"stdout": "PASS\n", "tests_passed": 1, "tests_failed": 0}}
    _patch(monkeypatch, honest)
    with TestClient(server.app) as c:
        tid = _post_task(c, tenant_auth, r149.SPEC, r149.MESSAGE)
        assert r149._wait_final(tid, published) == "completed"
    assert server.TASKS[tid]["result"]["result"]["verification"]["ok"] is True


def test_char_http_task_gate_off_is_unchanged(
        tenant_auth, postgrest, published, clean_tasks, monkeypatch):
    """CHAR: العلم معطل — السلوك التاريخي (نجاح بلا دليل ⇒ completed)."""
    monkeypatch.delenv("AGOS_OUTPUT_VALIDATORS", raising=False)
    _patch(monkeypatch, _evidence_free)
    with TestClient(server.app) as c:
        tid = _post_task(c, tenant_auth, r149.SPEC, r149.MESSAGE)
        assert r149._wait_final(tid, published) == "completed"


# ========================================= 4. legacy hub path (templates)

class _Hub:
    def __init__(self):
        self.sent = []

    async def send_message(self, msg):
        self.sent.append(msg)

    def register_agent(self, *a, **k):
        pass


def _template_agent(spec):
    mod = importlib.import_module(f"agent_civilization.agents.specialists._templates.{spec}_base")
    cls = next(v for k, v in vars(mod).items() if k.endswith("BaseAgent") and k != "BaseAgent")
    return cls(f"{spec}-hub-test", _Hub())


def _hub_run(agent, work):
    asyncio.run(agent._execute_work(work))
    return agent.communication_hub.sent[-1].content


@pytest.mark.parametrize("spec", ALL_SPECS)
def test_hub_path_failed_result_counts_as_failed(spec, monkeypatch):
    """RED: نتيجة ``ok=False`` في مسار المحور كانت تُعدّ ``tasks_completed``
    (حتى والعلم معطل) — إعلان إكمال كاذب في الإحصاءات."""
    monkeypatch.delenv("AGOS_OUTPUT_VALIDATORS", raising=False)
    _patch(monkeypatch, lambda s, w: {"ok": False, "error": "boom"})
    a = _template_agent(spec)
    content = _hub_run(a, {"task_id": "t1", "prompt": "x"})
    assert a.performance_metrics["tasks_completed"] == 0
    assert a.performance_metrics["tasks_failed"] == 1
    assert content["result"]["ok"] is False


@pytest.mark.parametrize("spec", ALL_SPECS)
def test_hub_path_forged_success_rejected_when_gate_on(spec, monkeypatch, gate_on):
    """RED: مسار المحور بلا بوابة — نجاح مزوّر كان يُرسل ``task_complete``
    بنتيجة ``ok=True`` ويُعدّ مكتملًا."""
    _patch(monkeypatch, _forged)
    a = _template_agent(spec)
    content = _hub_run(a, {"task_id": "t1", "prompt": "x"})
    assert content["result"]["ok"] is False
    assert content["result"]["error"] == "output_verification_failed"
    assert a.performance_metrics["tasks_failed"] == 1
    assert a.performance_metrics["tasks_completed"] == 0


def test_char_hub_path_honest_success_counts_completed(monkeypatch):
    """CHAR: نجاح صادق والعلم معطل ⇒ مكتمل (السلوك المقصود محفوظ)."""
    monkeypatch.delenv("AGOS_OUTPUT_VALIDATORS", raising=False)
    _patch(monkeypatch, lambda s, w: {"ok": True, "answer": "fine"})
    a = _template_agent("analyst")
    content = _hub_run(a, {"task_id": "t1", "prompt": "x"})
    assert a.performance_metrics["tasks_completed"] == 1
    assert content["result"]["ok"] is True
