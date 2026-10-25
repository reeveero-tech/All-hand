"""R-170 (batch 13) — regression tests for the pr-st-3 false rejection.

pr-st-3 (test split, gate on, log 289): a correct Arabic 5-item KPI list was
rejected ONLY by the generic 15-word floor. Root cause: when the request
itself sizes the deliverable ("list N items"), delivering exactly N list
items satisfies the request's own acceptance criterion and the word floor
yields to it. The floor stays for everything else (no count, count not met,
non-list prose) and every other check (language, abstention, bare success,
echo, count mismatch) is untouched.
"""
from __future__ import annotations

import pytest

from agent_civilization.core.output_validators import apply_output_verification, requirements_for


def gate(spec, work, result, monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    req = requirements_for(spec, work)
    return apply_output_verification(req, bool(result.get("ok")), result)


# --------------------------------------------------------- the reported case

def test_pr_st_3_arabic_five_kpi_list_is_accepted(monkeypatch):
    """The exact pr-st-3 corpus case: 5 correct Arabic KPIs, 14 words."""
    work = {"prompt": "اذكر خمسة مؤشرات أداء لفريق المبيعات."}
    answer = "- عدد الصفقات المغلقة\n- معدل التحويل\n- متوسط قيمة الصفقة\n- طول دورة البيع\n- الإيراد مقارنة بالهدف"
    ok, res = gate("strategist", work, {"ok": True, "answer": answer}, monkeypatch)
    assert ok is True, res
    assert not any("too short" in r for r in res["verification"]["reasons"])


def test_short_count_satisfied_list_is_accepted_english(monkeypatch):
    work = {"prompt": "List three ways to reduce customer churn."}
    answer = "- Offer annual discounts\n- Improve onboarding\n- Send win-back emails"
    ok, res = gate("strategist", work, {"ok": True, "answer": answer}, monkeypatch)
    assert ok is True, res


def test_count_satisfied_list_with_more_words_still_accepted(monkeypatch):
    """Control: lists that already passed the floor keep passing."""
    work = {"prompt": "اذكر خمسة مؤشرات أداء لفريق المبيعات."}
    answer = "- معدل التحويل الشهري للمتجر الإلكتروني\n- عدد الصفقات المغلقة شهريًا\n- متوسط قيمة الصفقة الواحدة بالريال\n- زمن الاستجابة لاستفسارات العملاء\n- رضا العملاء عن الخدمة"
    ok, res = gate("strategist", work, {"ok": True, "answer": answer}, monkeypatch)
    assert ok is True, res


# --------------------------------------------------------- floor not bypassed

def test_word_floor_still_applies_without_a_requested_count(monkeypatch):
    work = {"prompt": "Summarize the onboarding plan in one sentence."}
    answer = "We will guide each new user through setup, training, and first success."  # 12 words
    ok, res = gate("strategist", work, {"ok": True, "answer": answer}, monkeypatch)
    assert ok is False and any("too short" in r for r in res["verification"]["reasons"]), res


def test_word_floor_still_applies_when_count_not_satisfied(monkeypatch):
    work = {"prompt": "اذكر ثلاث مخاطر لإطلاق متجر إلكتروني."}
    answer = "- تقليل الطلب\n- زيادة التكاليف"  # 2 items for 3 requested
    ok, res = gate("strategist", work, {"ok": True, "answer": answer}, monkeypatch)
    assert ok is False, res
    assert any("too short" in r for r in res["verification"]["reasons"])
    assert any("requested 3" in r for r in res["verification"]["reasons"])


def test_count_mismatch_with_extra_items_is_rejected(monkeypatch):
    work = {"prompt": "List three ways to reduce customer churn."}
    answer = "- Offer annual discounts\n- Improve onboarding\n- Send win-back emails\n- Call customers"
    ok, res = gate("strategist", work, {"ok": True, "answer": answer}, monkeypatch)
    assert ok is False and any("requested 3" in r for r in res["verification"]["reasons"]), res


def test_language_mismatch_is_still_rejected_with_count_satisfied(monkeypatch):
    """ph-st-9 class: the fix must not bypass the language check."""
    work = {"prompt": "List three ways to reduce customer churn."}
    answer = "- قياس الرضا\n- تحسين الدعم\n- عروض الولاء"
    ok, res = gate("strategist", work, {"ok": True, "answer": answer}, monkeypatch)
    assert ok is False and any("language" in r for r in res["verification"]["reasons"]), res


def test_abstention_is_still_rejected(monkeypatch):
    work = {"prompt": "أعطني خطة من ثلاث خطوات لإطلاق متجر إلكتروني."}
    ok, res = gate("strategist", work, {"ok": True, "answer": "لا أعرف. لا أعرف. لا أعرف."}, monkeypatch)
    assert ok is False and any("abstention" in r for r in res["verification"]["reasons"]), res


def test_bare_success_claim_without_a_list_is_rejected(monkeypatch):
    work = {"prompt": "أعطني خطة من ثلاث خطوات لإطلاق متجر إلكتروني."}
    ok, res = gate("strategist", work, {"ok": True, "answer": "تم إنجاز المهمة بنجاح."}, monkeypatch)
    assert ok is False and any("bare success" in r for r in res["verification"]["reasons"]), res


def test_reviewer_no_line_citation_still_rejected(monkeypatch):
    """Control: an unrelated prose check is untouched by the fix."""
    work = {"prompt": "Review this code:\n```python\ndef avg(xs):\n    return sum(xs) / len(xs)\n```"}
    answer = "The code divides by the length of the list without checking whether it is empty first."
    ok, res = gate("reviewer", work, {"ok": True, "answer": answer}, monkeypatch)
    assert ok is False and any("cites no line" in r for r in res["verification"]["reasons"]), res


# ------------------------------------------------- documented structural limits

@pytest.mark.parametrize("prompt,answer", [
    ("أعطني خطة من ثلاث خطوات لإطلاق متجر إلكتروني.", "- تم\n- تم\n- تم"),
    ("List three colors for the brand.", "- red\n- blue\n- green"),
])
def test_count_satisfied_single_word_items_accepted_structurally(prompt, answer, monkeypatch):
    """Documented limitation (prereg PRST3 section 5): a count-complete list of
    one-word items passes the STRUCTURAL gate — form, not content."""
    ok, _ = gate("strategist", {"prompt": prompt}, {"ok": True, "answer": answer}, monkeypatch)
    assert ok is True
