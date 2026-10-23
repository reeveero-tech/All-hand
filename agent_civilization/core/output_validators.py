#!/usr/bin/env python3
"""R-170 (دفعة 1): مدققات مخرجات قابلة لإعادة الاستخدام — النموذج لا يقيّم نفسه.

القاعدة الأساسية (خارطة الطريق P4/R-170): لا ``success=true`` دون التحقق المطلوب.
ادعاء النجاح في مخرجات وكيل (``ok=True``) ليس دليلًا؛ يجب أن يستند إلى أدلة قابلة
للفحص آليًا من قِبل مدقق مستقل عن النموذج المنتج.

المدققات المتوفرة هنا (كلها حتمية، بلا استدعاء نموذج):

* ``validate_json``        — حمولة JSON قابلة للتحليل مع المفاتيح المطلوبة.
* ``validate_citations``   — إجابة واقعية يجب أن تحمل استشهادات غير فارغة.
* ``validate_tool_output`` — مخرجات أداة فعلية غير فارغة.
* ``validate_checks``      — قائمة فحوصات صريحة ناجحة (أسماء + نتيجة).
* ``verify_success``       — بوابة تجميعية: كل متطلب تحقق مطلوب في المهمة
                             يجب أن يمر قبل قبول ``ok=True``.

الاستخدام: المهمة تحدد متطلبات التحقق في ``work["verification"]`` (قاموس اختياري)،
ويُطبق ``verify_success`` على ناتج الوكيل قبل اعتماد النجاح. أي حكم رفض
يُعاد كفشل صادق مع سبب آلي — لا يُخفى، ولا يُحوَّل إلى نجاح إداري.

النطاق المعلن (دفعة 1): مكتبة + اختبارات حمراء أولًا. ربط المدققين بمسارات
الإكمال في الخادم دفعة لاحقة موثقة في ROADMAP (R-170).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

#: علامات ادعاء النجاح في نص الإجابة (إنجليزي وعربي، أدنى مجموعة حتمية).
#: الغرض: كشف «النجاح المزوّر» — ادعاء نجاح بلا أي دليل آلي.
SUCCESS_CLAIM_MARKERS: Tuple[str, ...] = (
    "success", "succeeded", "completed successfully", "done successfully",
    "task completed", "نجح", "نجاح", "تم بنجاح", "اكتمل بنجاح", "أنجزت المهمة",
)

#: مفاتيح الاستشهاد المقبولة داخل مخرجات النتيجة (أول موجود يُستخدم).
CITATION_KEYS: Tuple[str, ...] = ("citations", "sources", "references", "الاستشهادات")

#: مفاتيح قائمة الفحوصات الصريحة داخل مخرجات النتيجة.
CHECKS_KEYS: Tuple[str, ...] = ("checks", "checks_passed", "passed_checks", "الفحوصات")


@dataclass(frozen=True)
class Verdict:
    """حكم مدقق واحد أو بوابة التجميع.

    ``ok`` يعني «اجتاز التحقق»؛ أما نتيجة المهمة نفسها (نجاح/فشل) فتقررها
    العملية، والرفض هنا يعني: ادعاء النجاح غير معتمد.
    """
    ok: bool
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    checks_run: Tuple[str, ...] = field(default_factory=tuple)
    unverified: bool = False  # ok=True بلا متطلبات وبلا أدلة (متوافق مع الماضي)

    def as_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "reasons": list(self.reasons),
                "checks_run": list(self.checks_run), "unverified": self.unverified}


def _result_of(outcome: Mapping[str, Any]) -> Mapping[str, Any]:
    """النتيجة الداخلية لمخرج وكيل (شكل AgentOutcome أو HandlerResult أو قاموس)."""
    if isinstance(outcome, Mapping):
        inner = outcome.get("result")
        if isinstance(inner, Mapping):
            return inner
    return outcome if isinstance(outcome, Mapping) else {}


def _first_present(result: Mapping[str, Any], keys: Tuple[str, ...]) -> Tuple[str, Any]:
    for k in keys:
        if k in result and result[k] not in (None, "", [], {}):
            return k, result[k]
    return "", None


def _answer_text(result: Mapping[str, Any]) -> str:
    answer = result.get("answer")
    return answer if isinstance(answer, str) else ""


def _claims_success(text: str) -> bool:
    lowered = text.lower()
    return any(m in lowered for m in SUCCESS_CLAIM_MARKERS)


def validate_json(result: Mapping[str, Any],
                  required_keys: Tuple[str, ...] = ()) -> List[str]:
    """حمولة ``tool_output`` (أو ``payload``) يجب أن تكون JSON صالحًا بمفاتيح مطلوبة.

    يعيد قائمة أسباب الرفض (فارغة = اجتياز).
    """
    reasons: List[str] = []
    raw = result.get("tool_output", result.get("payload"))
    if raw is None or raw == "":
        return ["json: no payload to validate"]
    if isinstance(raw, Mapping):
        data = dict(raw)
    else:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return ["json: payload is not valid JSON"]
        if not isinstance(data, dict):
            return ["json: payload is not a JSON object"]
    for key in required_keys:
        if key not in data:
            reasons.append(f"json: required key missing: {key}")
    return reasons


def validate_citations(result: Mapping[str, Any]) -> List[str]:
    """إجابة تحمل محتوى واقعيًا يجب أن تستشهد مصادر غير فارغة.

    يعيد قائمة أسباب الرفض (فارغة = اجتياز).
    """
    key, value = _first_present(result, CITATION_KEYS)
    if key:
        return []
    answer = _answer_text(result)
    if not answer:
        return ["citations: no answer and no citations"]
    # إجابة بلا كتلة استشهادات: ترفض ما لم تكن امتناعًا صريحًا (بلا ادعاء معرفة).
    if _is_abstention(answer):
        return []
    return ["citations: factual answer without any citations"]


def _is_abstention(answer: str) -> bool:
    lowered = answer.lower()
    markers = ("cannot answer", "not enough information", "insufficient evidence",
               "لا أستطيع", "لا توجد معلومات كافية", "أمتنع عن الإجابة", "لا أدلة كافية")
    return any(m in lowered for m in markers)


def validate_tool_output(result: Mapping[str, Any]) -> List[str]:
    """نجاح مهمة أداة يتطلب مخرجات أداة فعلية غير فارغة."""
    if result.get("tool_output") in (None, "", [], {}):
        return ["tool_output: empty — success claim has no tool evidence"]
    return []


def validate_checks(result: Mapping[str, Any]) -> List[str]:
    """نجاح يتطلب قائمة فحوصات صريحة ناجحة (اسم + نتيجة ناجحة)."""
    key, value = _first_present(result, CHECKS_KEYS)
    if not key:
        return ["checks: no explicit checks list"]
    items = value if isinstance(value, (list, tuple)) else []
    passed = [c for c in items
              if (isinstance(c, str) and c.strip())
              or (isinstance(c, Mapping) and c.get("ok") is True)]
    if not passed:
        return ["checks: checks list present but nothing passed"]
    return []


def has_machine_evidence(result: Mapping[str, Any]) -> bool:
    """هل تحمل النتيجة أي دليل آلي (أداة/فحوصات/استشهادات)؟"""
    return bool(
        validate_tool_output(result) == []
        or validate_checks(result) == []
        or _first_present(result, CITATION_KEYS)[0]
    )


def is_forged_success(outcome: Mapping[str, Any]) -> bool:
    """«نجاح مزوّر»: ``ok=True`` بلا أي دليل آلي مع ادعاء نجاح في النص.

    هذا هو مقياس false-success الأساسي لـR-170 (قبول: حالات إيجابية وسلبية
    + قياس النجاح المزوّر).
    """
    if not bool(outcome.get("ok")):
        return False
    result = _result_of(outcome)
    if has_machine_evidence(result):
        return False
    return _claims_success(_answer_text(result))


def verify_success(outcome: Mapping[str, Any],
                   requirements: Optional[Mapping[str, Any]] = None) -> Verdict:
    """بوابة R-170: لا ``ok=True`` دون التحقق المطلوب.

    * ``ok=False`` (فشل صادق): لا ادعاء نجاح — الحكم ``ok=True`` للبوابة مع
      تسجيل الفحص.
    * ``ok=True``: تُطبق متطلبات المهمة (``requirements``) كلها؛ أي رفض
      يعني ادعاء نجاح غير معتمد (يُعاد كفشل مع السبب).
    * ``ok=True`` بلا متطلبات وبلا أي دليل آلي: يُقبل مع ``unverified=True``
      (توافق خلفي موثق — لا كسر للسلوك القائم في هذه الدفعة)، إلا إذا كان
      نص الإجابة يدّعي النجاح صراحة — عندها يُرفض كنجاح مزوّر.
    """
    if not bool(outcome.get("ok")):
        return Verdict(ok=True, checks_run=("no-success-claim",))
    result = _result_of(outcome)
    reqs = dict(requirements or {})
    checks_run: List[str] = []
    reasons: List[str] = []
    if reqs.get("json"):
        keys = tuple(reqs["json"]) if isinstance(reqs["json"], (list, tuple)) else ()
        reasons += validate_json(result, keys)
        checks_run.append("json")
    if reqs.get("citations"):
        reasons += validate_citations(result)
        checks_run.append("citations")
    if reqs.get("tool_output"):
        reasons += validate_tool_output(result)
        checks_run.append("tool_output")
    if reqs.get("checks"):
        reasons += validate_checks(result)
        checks_run.append("checks")
    if reasons:
        return Verdict(ok=False, reasons=tuple(reasons), checks_run=tuple(checks_run))
    if not checks_run:
        # لا متطلبات محددة: اكشف النجاح المزوّر فقط (حتمي، بلا نموذج).
        if is_forged_success(outcome):
            return Verdict(ok=False, reasons=("forged_success: ok=True with no "
                                              "machine evidence and an explicit "
                                              "success claim",),
                            checks_run=("forged-success",))
        if not has_machine_evidence(result):
            return Verdict(ok=True, unverified=True, checks_run=("no-requirements",))
    return Verdict(ok=True, checks_run=tuple(checks_run) or ("no-requirements",))


def false_success_rate(outcomes: List[Mapping[str, Any]]) -> float:
    """نسبة النجاح المزوّر في مجموعة مخرجات (0.0–1.0) — مقياس R-170.

    كل مخرج يحمل ``ok=True`` بلا دليل آلي مع ادعاء نجاح نصي يُحتسب مزوّرًا.
    """
    if not outcomes:
        return 0.0
    forged = sum(1 for o in outcomes if is_forged_success(o))
    return forged / len(outcomes)


def verification_from_work(work: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """استخراج متطلبات التحقق من حمولة المهمة (``work["verification"]``)."""
    v = work.get("verification") if isinstance(work, Mapping) else None
    return dict(v) if isinstance(v, Mapping) else None


__all__ = [
    "Verdict", "validate_json", "validate_citations", "validate_tool_output",
    "validate_checks", "verify_success", "is_forged_success", "has_machine_evidence",
    "false_success_rate", "verification_from_work", "SUCCESS_CLAIM_MARKERS",
    "CITATION_KEYS", "CHECKS_KEYS",
]
