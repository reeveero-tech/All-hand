#!/usr/bin/env python3
"""اختبارات R-170 (دفعة 6): الاستشهادات داخل ``tool_output`` وصلاحية الدليل (أحمر أولًا).

قبل هذه الدفعة:
* ``validate_citations`` يبحث في المستوى الأعلى للنتيجة فقط — والباحث
  (``real_dispatcher._researcher``) يضع مصادره في ``tool_output.sources``
  (بحث ويب) أو ``tool_output.source`` (إجابة فورية) ⇒ نجاح صادق يُرفض.
* الفحص **وجودي**: أي قيمة غير فارغة تمر (``["invented"]``، ``[{}]``،
  ``"javascript:..."``) ⇒ دليل غير صحيح يُقبل؛ و``has_machine_evidence``
  يعدّ تلك القيم دليلًا آليًا فيُفلت نجاح مزوّر من الكشف.

القاعدة الجديدة (فحص صيغة الدليل، لا صحة مضمونه): كتلة استشهادات صالحة =
عنصر واحد على الأقل، و**كل** عنصر إما URL بمخطط http/https ومضيف، أو كائن
يحمل URL كذلك في ``url``/``href``/``link``. عنصر واحد غير صالح يرفض الكتلة
(كما في ``checks``: الكل يجب أن ينجح). إثبات أن المصدر حقيقي ويدعم الادعاء
خارج النطاق (حد معلن).

التصنيف: ``RED`` يفشل سلوكيًا قبل التغيير؛ ``CHAR`` توصيف لسلوك قائم.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_civilization.agents.real import real_agents
from agent_civilization.core.output_validators import (
    has_machine_evidence,
    is_forged_success,
    validate_citations,
    verify_success,
)
from agent_civilization.routing import auto_router
from agent_civilization.routing.auto_router import route

GOOD_URL = "https://en.wikipedia.org/wiki/Paris"


def _web(sources):
    """شكل نجاح ``_researcher`` عبر بحث الويب (مقيس من real_dispatcher)."""
    return {"ok": True, "answer": "3 source(s): Paris; France; Seine",
            "tool_output": {"method": "web-search", "query": "paris",
                            "sources": sources, "abstract": ""}}


def _instant(source, related=None):
    """شكل نجاح ``_researcher`` عبر الإجابة الفورية."""
    return {"ok": True, "answer": "Paris is the capital of France.",
            "tool_output": {"method": "instant-answer", "abstract": "Paris is the capital",
                            "source": source, "related": related or []}}


# ================================================ accepted (valid evidence)

def test_web_search_sources_in_tool_output_are_accepted():
    """RED: مصادر بحث الويب داخل ``tool_output.sources`` (title/url/snippet) — كانت تُرفض."""
    res = _web([{"title": "Paris", "url": GOOD_URL, "snippet": "capital"},
                {"title": "France", "url": "http://example.org/france", "snippet": ""}])
    assert validate_citations(res) == []


def test_instant_answer_source_url_is_accepted():
    """RED: ``tool_output.source`` (AbstractURL) مصدر واحد صالح — كان يُرفض."""
    assert validate_citations(_instant(GOOD_URL)) == []


@pytest.mark.parametrize("block", [
    [GOOD_URL],
    [{"url": GOOD_URL}],
    [{"href": GOOD_URL, "title": "t"}],
    [{"link": "http://example.org/x"}],
    GOOD_URL,
    {"url": GOOD_URL},
])
def test_char_valid_top_level_citations_are_accepted(block):
    """CHAR: استشهادات صالحة في المستوى الأعلى تُقبل (قبل التغيير وبعده)."""
    assert validate_citations({"ok": True, "answer": "fact", "citations": block}) == []


def test_router_researcher_success_passes_gate_with_citations(monkeypatch):
    """RED: مرحلة ``ground`` (researcher) من الموجّه تعلن ``citations``، ونجاح
    الباحث الصادق بمصادر داخل ``tool_output`` يُقبل عبر ``agent.execute``
    والعلم مفعل، والفحص مسجل."""
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    st = {s["name"]: s for s in route("propose a market strategy and roadmap")["stages"]}["ground"]
    assert st["verification"] == {"tool_output": True, "citations": True}

    async def fake_dispatch(spec, work):
        return _web([{"title": "Paris", "url": GOOD_URL, "snippet": ""}])
    monkeypatch.setattr(real_agents, "_dispatch", fake_dispatch)
    from agent_civilization.agents.real.real_agents import create_civilization
    agent = create_civilization(1, impl="real")["researcher"][0]
    out = asyncio.run(agent.execute({"prompt": "x", "verification": st["verification"]}))
    assert out["ok"] is True, out
    assert {"tool_output", "citations"} <= set(out["result"]["verification"]["checks_run"])


# ============================================== rejected (invalid evidence)

@pytest.mark.parametrize("block", [
    ["invented"],                         # ليس URL
    [{}],                                 # كائن فارغ
    [{"title": "Paris"}],                 # عنوان بلا URL
    ["javascript:alert(1)"],              # مخطط غير مسموح
    ["ftp://example.org/x"],              # مخطط غير http(s)
    ["https://"],                         # بلا مضيف
    ["https://exa mple.org/x"],           # مسافة في المضيف
    [GOOD_URL, "invented"],               # مختلط: عنصر غير صالح يرفض الكتلة
    [42],                                 # نوع غير مدعوم
    "see the docs",                       # نص حر
])
def test_invalid_top_level_citations_are_rejected(block):
    """RED: دليل غير صحيح في المستوى الأعلى — كان يُقبل لمجرد وجوده."""
    reasons = validate_citations({"ok": True, "answer": "fact", "citations": block})
    assert reasons and all(r.startswith("citations:") for r in reasons)


@pytest.mark.parametrize("sources", [
    [{"title": "Paris", "snippet": "no url"}],
    [{"title": "Paris", "url": ""}],
    [{"title": "x", "url": "javascript:void(0)"}],
    [{"title": "ok", "url": GOOD_URL}, {"title": "bad", "url": "not a url"}],
])
def test_invalid_tool_output_sources_are_rejected(sources):
    """CHAR (حارس انحدار): مصادر أداة بلا URL صالح ترفض. كانت تُرفض قبل الدفعة
    لأن ``tool_output`` لم يُفحص أصلًا؛ يجب أن تبقى مرفوضة بعد البحث فيه —
    لا يكفي أن المفتاح موجود."""
    assert validate_citations(_web(sources)) != []


@pytest.mark.parametrize("source", ["", None])
def test_char_instant_answer_without_source_is_rejected(source):
    """CHAR: إجابة فورية بلا ``source`` (AbstractURL فارغ) — إجابة واقعية بلا
    استشهاد تُرفض (قبل وبعد)."""
    assert validate_citations(_instant(source, related=["Paris — city"])) != []


def test_char_empty_tool_output_sources_rejected():
    """CHAR: قائمة مصادر فارغة = لا استشهاد."""
    assert validate_citations(_web([])) != []


def test_char_abstention_still_accepted_without_citations():
    """CHAR: الامتناع الصريح مقبول بلا استشهادات (قبل وبعد)."""
    assert validate_citations({"ok": True, "answer": "I cannot answer: not enough information."}) == []


def test_invalid_top_level_block_is_not_rescued_by_tool_output():
    """RED: كتلة مستوى أعلى غير صالحة لا «تُنقذ» بمصادر الأداة — الادعاء المعلن
    نفسه يجب أن يكون صالحًا (لا خلط أدلة)."""
    res = _web([{"title": "Paris", "url": GOOD_URL}])
    res["citations"] = ["invented"]
    assert validate_citations(res) != []


# ===================================== evidence used by forged-success check

def test_invalid_citations_do_not_count_as_machine_evidence():
    """RED: ``has_machine_evidence`` كان يعدّ ``["invented"]`` دليلًا آليًا."""
    assert has_machine_evidence({"ok": True, "answer": "x", "citations": ["invented"]}) is False


def test_forged_success_with_invented_citations_is_detected():
    """RED: ادعاء نجاح نصي + استشهادات مخترعة = نجاح مزوّر (كان يُفلت)."""
    outcome = {"ok": True, "result": {"ok": True, "answer": "The task completed successfully.",
                                      "citations": ["invented"]}}
    assert is_forged_success(outcome) is True
    v = verify_success(outcome, None)
    assert v.ok is False


def test_char_forged_claim_with_valid_citation_is_not_forged():
    """CHAR: ادعاء نجاح مع استشهاد صالح ليس «مزوّرًا» بتعريف الكشف (دليل موجود)."""
    outcome = {"ok": True, "result": {"ok": True, "answer": "The task completed successfully.",
                                      "citations": [GOOD_URL]}}
    assert is_forged_success(outcome) is False


# ============================================================ router policy

def test_router_policy_researcher_requires_citations_and_tool_output():
    """RED: الباحث يعلن ``citations`` إضافة إلى ``tool_output``؛ الباقون كما هم."""
    p = auto_router.STAGE_VERIFICATION
    assert p["researcher"] == {"tool_output": True, "citations": True}
    for spec in ("test_runner", "network_client", "validator"):
        assert p[spec] == {"tool_output": True}


def test_verify_success_reports_citation_reason_for_instant_answer_without_source():
    """RED (سبب صريح): متطلب ``citations`` على إجابة فورية بلا مصدر يرفض بسبب
    يسمي ``tool_output`` كموضع بُحث فيه — لا رسالة مضللة «بلا أي استشهاد»
    حين تكون الأداة قد أعادت قائمة مصادر فارغة."""
    v = verify_success({"ok": True, "result": _instant("")}, {"citations": True})
    assert v.ok is False
    assert any("tool_output" in r for r in v.reasons if r.startswith("citations:"))
