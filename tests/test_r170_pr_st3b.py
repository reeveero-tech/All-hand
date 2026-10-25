"""R-170 batch 13b — regression tests for bulleted bare-claim lists.

The batch-13 fix (word floor yields to a satisfied requested count) opened a
hollow-success path: a list whose EVERY item is a bare success claim ("- تم"
x3) or an abstention ("- لا أعرف" x3) used to be rejected by the word floor
alone and became formally acceptable. Batch 13b rejects any list whose every
item — bullet/numbering prefix and trailing punctuation stripped — is nothing
but a bare claim. Correct terse lists (KPIs, colors) stay accepted.
"""
from __future__ import annotations

import pytest

from agent_civilization.core.output_validators import apply_output_verification, requirements_for


def gate(spec, work, result, monkeypatch):
    monkeypatch.setenv("AGOS_OUTPUT_VALIDATORS", "1")
    req = requirements_for(spec, work)
    return apply_output_verification(req, bool(result.get("ok")), result)


AR_3STEPS = {"prompt": "أعطني خطة من ثلاث خطوات لإطلاق متجر إلكتروني."}
EN_3WAYS = {"prompt": "List three ways to reduce customer churn."}


# --------------------------------------------------------- must be REJECTED

def test_bulleted_bare_success_claims_are_rejected(monkeypatch):
    ok, res = gate("strategist", AR_3STEPS, {"ok": True, "answer": "- تم\n- تم\n- تم"}, monkeypatch)
    assert ok is False and any("bare success" in r for r in res["verification"]["reasons"]), res


def test_numbered_bare_success_claims_are_rejected(monkeypatch):
    ok, res = gate("strategist", EN_3WAYS, {"ok": True, "answer": "1. done\n2. done\n3. done"}, monkeypatch)
    assert ok is False and any("bare success" in r for r in res["verification"]["reasons"]), res


def test_bulleted_abstentions_are_rejected(monkeypatch):
    ok, res = gate("strategist", AR_3STEPS, {"ok": True, "answer": "- لا أعرف\n- لا أعرف\n- لا أعرف"}, monkeypatch)
    assert ok is False and any("abstention" in r for r in res["verification"]["reasons"]), res


def test_english_bulleted_abstentions_are_rejected(monkeypatch):
    ok, res = gate("strategist", EN_3WAYS, {"ok": True, "answer": "- I don't know\n- I don't know\n- I don't know"}, monkeypatch)
    assert ok is False and any("abstention" in r for r in res["verification"]["reasons"]), res


def test_trailing_punctuation_does_not_defeat_the_check(monkeypatch):
    ok, res = gate("strategist", AR_3STEPS, {"ok": True, "answer": "- تم.\n- تم!\n- تم"}, monkeypatch)
    assert ok is False and any("bare success" in r for r in res["verification"]["reasons"]), res


# --------------------------------------------------------- must stay ACCEPTED

def test_pr_st_3_case_still_accepted(monkeypatch):
    work = {"prompt": "اذكر خمسة مؤشرات أداء لفريق المبيعات."}
    answer = "- عدد الصفقات المغلقة\n- معدل التحويل\n- متوسط قيمة الصفقة\n- طول دورة البيع\n- الإيراد مقارنة بالهدف"
    ok, res = gate("strategist", work, {"ok": True, "answer": answer}, monkeypatch)
    assert ok is True, res


def test_color_lists_still_accepted(monkeypatch):
    ok, res = gate("strategist", {"prompt": "List three colors for the brand."},
                   {"ok": True, "answer": "- red\n- blue\n- green"}, monkeypatch)
    assert ok is True, res


def test_short_english_step_list_still_accepted(monkeypatch):
    ok, res = gate("strategist", EN_3WAYS,
                  {"ok": True, "answer": "- Offer annual discounts\n- Improve onboarding\n- Send win-back emails"},
                  monkeypatch)
    assert ok is True, res


@pytest.mark.parametrize("prompt,answer", [
    ("أعطني خطة من ثلاث خطوات لإطلاق متجر إلكتروني.", "- تم\n- تحسين الدعم الفني\n- تم"),  # mixed: one substantive item
    ("List ten colors for the brand.", "- red\n- blue\n- green\n- teal\n- gold\n- ivory\n- plum\n- sage\n- slate\n- coral"),
])
def test_mixed_or_legitimate_lists_accepted_structurally(prompt, answer, monkeypatch):
    """Only ALL-bare lists are rejected; mixed lists fall to the other checks."""
    ok, res = gate("strategist", {"prompt": prompt}, {"ok": True, "answer": answer}, monkeypatch)
    assert ok is True, res
