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
import os
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

    حد معلن: هذا فحص وجود (presence) لا إثبات صحة (substantiation) —
    التحقق من أن الاستشهادات نفسها حقيقية يحتاج مسارًا آخر (R-170 لاحقًا).
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
    """نجاح مهمة أداة يتطلب مخرجات أداة فعلية غير فارغة.

    مخرجات تحمل علامات فشل صريحة (``exit_code`` رقمي ≠ 0، أو ``ok: False``
    داخلها) ليست دليل نجاح — الرفض مذلل بالسبب.
    """
    tool_output = result.get("tool_output")
    if tool_output in (None, "", [], {}):
        return ["tool_output: empty — success claim has no tool evidence"]
    if isinstance(tool_output, Mapping):
        exit_code = tool_output.get("exit_code")
        if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0:
            return [f"tool_output: failure evidence (exit_code={exit_code})"]
        if tool_output.get("ok") is False:
            return ["tool_output: failure evidence (ok=false inside tool_output)"]
    return []


def validate_checks(result: Mapping[str, Any]) -> List[str]:
    """نجاح يتطلب قائمة فحوصات صريحة ناجحة — **كل** عنصر يجب أن يكون ناجحًا.

    العنصر الناجح المقبول: قاموس يحمل ``ok: True`` صراحة. الاسم المجرد
    (سلسلة نصية) لا يُحتسب فحصًا ناجحًا — النتيجة يجب أن تكون موثقة لا
    مجرد ادعاء اسم فحص (R-170: النموذج لا يقيّم نفسه).
    """
    key, value = _first_present(result, CHECKS_KEYS)
    if not key:
        return ["checks: no explicit checks list"]
    items = value if isinstance(value, (list, tuple)) else []
    if not items:
        return ["checks: checks list present but empty"]
    failed: List[str] = []
    for idx, c in enumerate(items):
        if isinstance(c, Mapping) and c.get("ok") is True:
            continue
        failed.append(f"checks: item #{idx} is not a documented passing check (ok=true)")
    return failed


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


#: المتطلبات المعروفة لبوابة التحقق — أي مفتاح غير معروف يُرفض صراحة
#: (لا يُتجاهل بصمت: متطلب غير مفهوم = تحقق غير منفذ).
KNOWN_REQUIREMENTS: frozenset = frozenset(
    {"json", "citations", "tool_output", "checks"})


def verify_success(outcome: Mapping[str, Any],
                   requirements: Optional[Mapping[str, Any]] = None) -> Verdict:
    """بوابة R-170: لا ``ok=True`` دون التحقق المطلوب.

    * ``ok=False`` (فشل صادق): لا ادعاء نجاح — الحكم ``ok=True`` للبوابة مع
      تسجيل الفحص.
    * ``ok=True`` مع نتيجة داخلية ``result.ok=False``: تناقض — رفض صريح.
    * ``ok=True``: تُطبق متطلبات المهمة (``requirements``) كلها؛ أي متطلب
      غير معروف يُرفض صراحة (لا تجاهل صامت)؛ أي رفض يعني ادعاء نجاح غير
      معتمد (يُعاد كفشل مع السبب).
    * ``ok=True`` بلا متطلبات وبلا أي دليل آلي: يُقبل مع ``unverified=True``
      (توافق خلفي موثق — لا كسر للسلوك القائم في هذه الدفعة)، إلا إذا كان
      نص الإجابة يدّعي النجاح صراحة — عندها يُرفض كنجاح مزوّر.
    """
    if not bool(outcome.get("ok")):
        return Verdict(ok=True, checks_run=("no-success-claim",))
    result = _result_of(outcome)
    # تناقض الطبقتين: نجاح خارجي مع فشل داخلي موثق = ادعاء غير معتمد.
    if isinstance(result, Mapping) and result.get("ok") is False:
        return Verdict(ok=False, reasons=("contradiction: outer ok=true with "
                                         "inner result ok=false",),
                       checks_run=("inner-consistency",))
    reqs = dict(requirements or {})
    unknown = sorted(set(reqs) - KNOWN_REQUIREMENTS)
    if unknown:
        return Verdict(ok=False, reasons=tuple(
            f"unknown requirement (not verified): {k}" for k in unknown),
            checks_run=("requirements",))
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

    حد معلن: هذا قياس «الكشف الحتمي بالعلامات» (heuristic) وليس حقيقة
    أرضية مستقلة — يُستخدم للمقارنة قبل/بعد على المجموعة نفسها، ولا يعادل
    قياس الرفض المستقل في R-169.
    """
    if not outcomes:
        return 0.0
    forged = sum(1 for o in outcomes if is_forged_success(o))
    return forged / len(outcomes)


def verification_from_work(work: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """استخراج متطلبات التحقق من حمولة المهمة (``work["verification"]``).

    حد معلن: تعيد ``None`` للمتطلبات المشوهة (ليست كائنًا) — مسار
    الإكمال لا يستخدمها بل يمرر القيمة الخام إلى ``apply_output_verification``
    التي ترفض المشوه صراحة (دفعة 3).
    """
    v = work.get("verification") if isinstance(work, Mapping) else None
    return dict(v) if isinstance(v, Mapping) else None


# ------------------------------------------------ completion-path gate (R-170/3)

def output_gate_enabled() -> bool:
    """علم تفعيل بوابة الإكمال: ``AGOS_OUTPUT_VALIDATORS=1`` (الافتراضي معطل).

    توافق خلفي موثق: المعطل = السلوك الحالي كما هو دون أي تغيير.
    """
    return os.getenv("AGOS_OUTPUT_VALIDATORS", "") == "1"


def _result_as_dict(result: Any) -> Optional[Dict[str, Any]]:
    """نسخة قابلة للتسجيل من النتيجة (بلا تعديل الأصل) — ``None`` إن تعذر."""
    if isinstance(result, dict):
        return dict(result)
    if hasattr(result, "model_dump"):  # HandlerResult (pydantic, open)
        return dict(result.model_dump())
    return None


def _record_verdict(result: Any, verdict: Verdict) -> Any:
    d = _result_as_dict(result)
    if d is None:
        return result  # شكل غير قابل للتسجيل — لا يُغير البوابة شكله عند القبول
    d["verification"] = verdict.as_dict()
    return d


def _record_rejection(result: Any, verdict: Verdict) -> Dict[str, Any]:
    d = _result_as_dict(result)
    if d is None:
        d = {"answer": result}
    prior_error = d.get("error")
    d["ok"] = False
    d["error"] = "output_verification_failed"
    v = verdict.as_dict()
    v["original_ok"] = True  # الادعاء الأصلي محفوظ — لا تطميس
    if prior_error:
        v["original_error"] = prior_error
    d["verification"] = v
    return d


def apply_output_verification(requirements: Any, ok: bool, result: Any) -> tuple:
    """بوابة R-170 على مسار الإكمال — تُستدعى قبل اعتماد النجاح وأي أثر جانبي.

    * العلم معطل: السلوك الحالي كما هو (توافق خلفي).
    * ``ok=False`` (فشل صادق): لا يُمس — لا ترقية ولا تطميس (البوابة لا تفحص
      إلا ادعاءات النجاح).
    * متطلبات مشوهة (ليست كائنًا): رفض صريح — لا تحويل صامت إلى «لا متطلبات».
    * تعطل البوابة وهي مفعلة: فشل تحقق واضح — لا نجاح غير مفحوص أبدًا.
    * الرفض: ``ok=False`` خارجيًا وداخليًا معًا، رمز ثابت
      ``output_verification_failed``، الحكم والأسباب والادعاء الأصلي مسجلة في
      ``result["verification"]``.

    ``requirements`` هي القيمة الخام المُلتقطة من ``work["verification"]``
    **قبل** استدعاء المعالج (حتى لا يغيرها أثناء التنفيذ).
    """
    if not output_gate_enabled():
        return ok, result
    if requirements is not None and not isinstance(requirements, Mapping):
        return False, _record_rejection(result, Verdict(
            ok=False,
            reasons=("verification: malformed requirements (not an object)",),
            checks_run=("requirements",)))
    if not ok:
        return ok, result  # فشل صادق — البوابة لا تلمسه
    try:
        verdict = verify_success({"ok": True, "result": result}, requirements)
    except Exception as exc:  # noqa: BLE001 — البوابة لا تُمرر نجاحًا غير مفحوص
        verdict = Verdict(ok=False,
                           reasons=(f"verification gate error: {type(exc).__name__}",),
                           checks_run=("gate",))
    if verdict.ok:
        return True, _record_verdict(result, verdict)
    return False, _record_rejection(result, verdict)


__all__ = [
    "Verdict", "validate_json", "validate_citations", "validate_tool_output",
    "validate_checks", "verify_success", "is_forged_success", "has_machine_evidence",
    "false_success_rate", "verification_from_work", "SUCCESS_CLAIM_MARKERS",
    "CITATION_KEYS", "CHECKS_KEYS", "KNOWN_REQUIREMENTS",
    "output_gate_enabled", "apply_output_verification",
]
