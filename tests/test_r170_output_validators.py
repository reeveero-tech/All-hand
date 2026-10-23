#!/usr/bin/env python3
"""اختبارات R-170 (دفعة 1): مدققات المخرجات — إيجابية وسلبية + قياس النجاح المزوّر.

الأحمر أولًا (بروتوكول المشروع): قبل هذه الدفعة لم يكن يوجد أي مدقق يرفض
``ok=True`` بلا دليل آلي — الاختبارات السلبية أدناه كانت ستفشل (لا وحدة
لاستيرادها). تُنفذ الآن خضراء.
"""
from __future__ import annotations

import pytest

from agent_civilization.core.output_validators import (
    false_success_rate,
    is_forged_success,
    validate_citations,
    validate_checks,
    validate_json,
    validate_tool_output,
    verify_success,
    verification_from_work,
)


# ---------------------------------------------------------------- negative


def test_forged_success_rejected():
    """ok=True + لا دليل آلي + ادعاء نجاح نصي ⇒ رفض (نجاح مزوّر)."""
    outcome = {"ok": True, "agent": "a", "elapsed_s": 0.1,
               "result": {"ok": True, "answer": "The task completed successfully.",
                          "error": None, "tool_output": None}}
    assert is_forged_success(outcome)
    v = verify_success(outcome)
    assert not v.ok
    assert any("forged_success" in r for r in v.reasons)


def test_forged_success_arabic_claim_rejected():
    outcome = {"ok": True,
               "result": {"ok": True, "answer": "تم بنجاح تنفيذ المهمة.",
                          "tool_output": None}}
    assert is_forged_success(outcome)
    assert not verify_success(outcome).ok


def test_success_without_required_tool_output_rejected():
    outcome = {"ok": True, "result": {"ok": True, "answer": "x", "tool_output": None}}
    v = verify_success(outcome, {"tool_output": True})
    assert not v.ok
    assert validate_tool_output(outcome["result"]) != []


def test_success_without_required_checks_rejected():
    outcome = {"ok": True, "result": {"ok": True, "tool_output": {"ran": 1}}}
    v = verify_success(outcome, {"checks": True})
    assert not v.ok


def test_json_validator_negative():
    result = {"ok": True, "tool_output": "not json at all"}
    assert validate_json(result) != []
    result2 = {"ok": True, "tool_output": '{"a": 1}'}
    assert validate_json(result2, required_keys=("b",)) != []


def test_citations_negative():
    result = {"ok": True, "answer": "Paris is the capital of France."}
    assert validate_citations(result) != []


def test_checks_list_with_no_pass_rejected():
    result = {"ok": True, "checks": [{"name": "compile", "ok": False}]}
    assert validate_checks(result) != []


# ---------------------------------------------------------------- positive


def test_success_with_tool_evidence_accepted():
    outcome = {"ok": True, "result": {"ok": True, "answer": "done",
                                      "tool_output": {"exit_code": 0}}}
    v = verify_success(outcome, {"tool_output": True})
    assert v.ok and not v.unverified


def test_success_with_checks_evidence_accepted():
    outcome = {"ok": True, "result": {"ok": True, "checks": ["compile", "tests"]}}
    v = verify_success(outcome, {"checks": True})
    assert v.ok


def test_json_validator_positive():
    result = {"ok": True, "tool_output": '{"answer": 42, "refs": [1]}'}
    assert validate_json(result, required_keys=("answer", "refs")) == []


def test_citations_positive():
    result = {"ok": True, "answer": "Paris is the capital of France.",
              "citations": ["https://example.com/france"]}
    assert validate_citations(result) == []


def test_abstention_without_citations_is_appropriate():
    """الامتناع الصادق ليس فشل مدقق — R-170: امتناع مناسب عند غياب الأدلة."""
    result = {"ok": False, "answer": "لا توجد معلومات كافية للإجابة."}
    assert validate_citations(result) == []


def test_honest_failure_passes_gate():
    """ok=False فشل صادق: لا ادعاء نجاح — البوابة تعيد ok=True بلا ادعاء."""
    outcome = {"ok": False, "result": {"ok": False, "error": "provider_timeout"}}
    v = verify_success(outcome)
    assert v.ok and "no-success-claim" in v.checks_run


def test_no_requirements_no_evidence_no_claim_backward_compatible():
    """ok=True بلا متطلبات وبلا أدلة وبلا ادعاء نجاح: يُقبل مع unverified=True."""
    outcome = {"ok": True, "result": {"ok": True, "answer": "here is the summary",
                                      "tool_output": None}}
    v = verify_success(outcome)
    assert v.ok and v.unverified


# ---------------------------------------------------- false-success metric


def test_false_success_rate_measurement():
    outcomes = [
        {"ok": True, "result": {"ok": True, "answer": "success!", "tool_output": None}},
        {"ok": True, "result": {"ok": True, "answer": "here it is",
                                "tool_output": {"x": 1}}},
        {"ok": True, "result": {"ok": True, "answer": "تم بنجاح", "tool_output": None}},
        {"ok": False, "result": {"ok": False, "error": "e"}},
    ]
    assert false_success_rate(outcomes) == pytest.approx(2 / 4)


def test_verification_requirements_read_from_work():
    work = {"prompt": "summarize", "verification": {"citations": True}}
    assert verification_from_work(work) == {"citations": True}
    assert verification_from_work({"prompt": "x"}) is None


def test_nested_result_shape_supported():
    """الشكل القياسي: outcome.result هو HandlerResult — النتيجة الداخلية تُفحص."""
    outcome = {"ok": True, "result": {"ok": True, "answer": "The task was completed successfully",
                                      "tool_output": None, "checks": None}}
    assert is_forged_success(outcome)
    outcome2 = {"ok": True, "result": {"ok": True, "tool_output": {"o": 1}}}
    assert not is_forged_success(outcome2)
