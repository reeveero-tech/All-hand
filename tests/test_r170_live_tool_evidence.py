#!/usr/bin/env python3
"""R-170 (دفعة 6): تحقق حي — researcher وnetwork_client ضد الإنترنت الحقيقي
عبر مسار الإكمال الكامل (``agent.execute`` + البوابة مفعلة + متطلبات الموجّه).

علامة ``network`` ⇒ تُستبعد من الحزمة الحتمية؛ تعمل بـ``pytest -m network``
(وظيفة CI ``network`` — ليست فحصًا مطلوبًا: انقطاع DuckDuckGo/DNS لا يمنع الدمج
لكنه يبقى ظاهرًا). هذه الاختبارات تثبت ما لم تثبته قراءة الشيفرة في الدفعة 5:
أن النجاح الحي الصادق لهذين المعالجين يجتاز المتطلبات المعلنة فعلًا، وأن
الفحوص مسجلة (لا ``unverified``)، وأن النجاح يُرفض إن غاب الدليل المطلوب.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_civilization.pipeline import dag
from agent_civilization.routing.auto_router import STAGE_VERIFICATION, route

pytestmark = pytest.mark.network

IMPLS = ["real", "specialists"]


def _agent(impl, spec):
    from agent_civilization.agents.real.real_agents import create_civilization
    return create_civilization(1, impl=impl)[spec][0]


def _exec(agent, work):
    return asyncio.run(agent.execute(work))


@pytest.fixture(autouse=True)
def _gate_on(monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")


@pytest.mark.parametrize("impl", IMPLS)
@pytest.mark.parametrize("prompt,method", [
    ("Python programming language", "instant-answer"),
    ("best practices for REST API pagination", "web-search"),
])
def test_live_researcher_passes_router_requirements(impl, prompt, method):
    """حي: الباحث بمتطلب الموجّه (tool_output + citations) — نجاح بفحصين مسجلين.
    المساران الفعليان للمعالج (إجابة فورية/بحث ويب) مغطيان؛ إن أعاد المزود
    مسارًا آخر يُسجل ذلك في رسالة الفشل لا يُخفى."""
    req = STAGE_VERIFICATION["researcher"]
    out = _exec(_agent(impl, "researcher"), {"prompt": prompt, "verification": dict(req)})
    res = out["result"]
    assert out["ok"] is True, res
    assert res["tool_output"]["method"] == method, res["tool_output"].get("method")
    v = res["verification"]
    assert v["ok"] is True and v["unverified"] is False
    assert {"tool_output", "citations"} <= set(v["checks_run"])


@pytest.mark.parametrize("impl", IMPLS)
def test_live_network_client_passes_router_requirements(impl):
    """حي: HEAD حقيقي بمتطلب الموجّه tool_output ⇒ نجاح وفحص مسجل وحالة 200."""
    req = STAGE_VERIFICATION["network_client"]
    out = _exec(_agent(impl, "network_client"),
                {"prompt": "HEAD https://example.com/", "verification": dict(req)})
    res = out["result"]
    assert out["ok"] is True, res
    assert res["tool_output"]["status"] == 200
    assert "tool_output" in res["verification"]["checks_run"]


def test_live_network_client_dns_only_passes():
    """حي: DNS فقط (مسار نجاح آخر للمعالج) يجتاز tool_output أيضًا."""
    out = _exec(_agent("real", "network_client"),
                {"prompt": "resolve example.com", "verification": {"tool_output": True}})
    assert out["ok"] is True, out["result"]
    assert out["result"]["tool_output"].get("addresses") or out["result"]["tool_output"].get("ip")


def test_live_researcher_rejected_when_citations_required_but_json_demanded():
    """حي (وجه سلبي حقيقي): نتيجة الباحث الحية لا تحمل كائن JSON بالمفتاح
    المطلوب ⇒ البوابة ترفض النجاح الحي — المتطلب غير المستوفى لا يُتجاوز."""
    out = _exec(_agent("real", "researcher"),
                {"prompt": "Python programming language",
                 "verification": {"citations": True, "json": ["population"]}})
    assert out["ok"] is False
    assert out["result"]["error"] == "output_verification_failed"
    assert out["result"]["verification"]["original_ok"] is True


def test_live_dag_single_stage_router_policy_researcher():
    """حي عبر DAG: مرحلة researcher بمتطلبات السياسة نفسها ⇒ ``succeeded``."""
    from agent_civilization.agents.real.real_agents import create_civilization
    dag.RUNS.clear()
    plan = {"primary": "researcher", "reason": "live", "goal": "Python programming language",
            "stages": [{"name": "ground", "specialization": "researcher",
                        "prompt": "Python programming language",
                        "verification": dict(STAGE_VERIFICATION["researcher"])}]}
    rep = asyncio.run(dag.run_plan(plan, create_civilization(1, impl="real")))
    assert rep["stages"][0]["status"] == dag.SUCCEEDED, rep["stages"][0]
    dag.RUNS.clear()


def test_live_router_ground_stage_declares_what_live_researcher_satisfies():
    """ربط: ما يعلنه الموجّه لمرحلة ground هو بالضبط ما اختُبر حيًا أعلاه."""
    st = {s["name"]: s for s in route("design a scalable microservice architecture")["stages"]}["ground"]
    assert st["verification"] == STAGE_VERIFICATION["researcher"]
