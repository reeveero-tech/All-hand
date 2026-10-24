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


#: R-170 (batch 9): the JSON Schema subset the gate verifies. Any other
#: keyword is rejected as "not verified" — never silently ignored.
SCHEMA_KEYWORDS = frozenset({"type", "required", "properties", "items", "enum",
                             "additionalProperties", "title", "description"})
_SCHEMA_TYPES = {
    "object": lambda v: isinstance(v, Mapping),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}
SCHEMA_MAX_DEPTH = 16


def validate_schema(data: Any, schema: Any, path: str = "$", depth: int = 0) -> List[str]:
    """Reasons ``data`` violates ``schema`` (the SCHEMA_KEYWORDS subset)."""
    if depth > SCHEMA_MAX_DEPTH:
        return [f"json.schema: {path}: nesting deeper than {SCHEMA_MAX_DEPTH} (not verified)"]
    if not isinstance(schema, Mapping):
        return [f"json.schema: {path}: schema is not an object (not verified)"]
    unsupported = sorted(set(schema) - SCHEMA_KEYWORDS)
    if unsupported:
        return [f"json.schema: {path}: unsupported keyword(s) {', '.join(unsupported)} (not verified)"]
    reasons: List[str] = []
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if any(t not in _SCHEMA_TYPES for t in types):
            return [f"json.schema: {path}: unknown type {schema['type']!r} (not verified)"]
        if not any(_SCHEMA_TYPES[t](data) for t in types):
            return [f"json.schema: {path}: expected type {'/'.join(types)}, got {type(data).__name__}"]
    if "enum" in schema and data not in list(schema["enum"]):
        reasons.append(f"json.schema: {path}: value not in enum")
    if isinstance(data, Mapping):
        for key in schema.get("required", []) or []:
            if key not in data:
                reasons.append(f"json.schema: {path}: required key missing: {key}")
        props = schema.get("properties", {}) or {}
        for key, sub in props.items():
            if key in data:
                reasons += validate_schema(data[key], sub, f"{path}.{key}", depth + 1)
        if schema.get("additionalProperties") is False:
            for key in data:
                if key not in props:
                    reasons.append(f"json.schema: {path}: additional property not allowed: {key}")
    if isinstance(data, list) and "items" in schema:
        for i, item in enumerate(data):
            reasons += validate_schema(item, schema["items"], f"{path}[{i}]", depth + 1)
    return reasons


def _json_payload(result: Mapping[str, Any]) -> Tuple[Any, Optional[str]]:
    raw = result.get("tool_output", result.get("payload"))
    if raw is None or raw == "":
        return None, "json: no payload to validate"
    if isinstance(raw, (Mapping, list)):
        return raw, None
    try:
        return json.loads(raw), None
    except (TypeError, ValueError):
        return None, "json: payload is not valid JSON"


def validate_code(result: Mapping[str, Any]) -> List[str]:
    """``tool_output.code`` must be non-empty Python that COMPILES.

    ``compile(..., "exec")`` only — the code is never executed. The gate
    re-checks independently of the handler's own "verified" claim.
    """
    tool_output = result.get("tool_output")
    code = tool_output.get("code") if isinstance(tool_output, Mapping) else None
    if not isinstance(code, str) or not code.strip():
        if isinstance(tool_output, Mapping) and tool_output.get("code_omitted"):
            return [f"code: not verifiable — {tool_output['code_omitted']}"]
        return ["code: no code text in tool_output to compile"]
    try:
        compile(code, "<r170-gate>", "exec")
    except SyntaxError as e:
        return [f"code: does not compile (SyntaxError line {e.lineno}: {e.msg})"]
    except (ValueError, TypeError) as e:
        return [f"code: does not compile ({type(e).__name__})"]
    return []


#: R-170 (batch 6): keys under which an item carries its URL.
CITATION_URL_KEYS: Tuple[str, ...] = ("url", "href", "link")
_URL_SCHEMES = ("http", "https")


def _valid_citation_url(value: Any) -> bool:
    """http(s) URL with a host and no whitespace (format only, not existence)."""
    if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
        return False
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme.lower() in _URL_SCHEMES and bool(parts.hostname)


def _citation_item_ok(item: Any) -> bool:
    if isinstance(item, str):
        return _valid_citation_url(item)
    if isinstance(item, Mapping):
        return any(_valid_citation_url(item.get(k)) for k in CITATION_URL_KEYS)
    return False


def _judge_citation_block(where: str, block: Any) -> List[str]:
    """A block is valid iff it has >= 1 item and EVERY item is a valid source."""
    items = list(block) if isinstance(block, (list, tuple)) else [block]
    if not items:
        return [f"citations: empty citation block in {where}"]
    bad = [i for i, it in enumerate(items) if not _citation_item_ok(it)]
    if bad:
        return [f"citations: {len(bad)}/{len(items)} item(s) in {where} are not valid "
                f"http(s) sources (first bad: #{bad[0]})"]
    return []


def _citation_blocks(result: Mapping[str, Any]) -> List[Tuple[str, Any]]:
    """Declared citation blocks, top level first, then inside ``tool_output``
    (researcher: ``sources`` from web search, ``source`` from instant answer)."""
    key, value = _first_present(result, CITATION_KEYS)
    if key:
        return [(key, value)]
    tool_output = result.get("tool_output")
    if isinstance(tool_output, Mapping):
        tkey, tvalue = _first_present(tool_output, CITATION_KEYS + ("source",))
        if tkey:
            return [(f"tool_output.{tkey}", tvalue)]
    return []


def citations_valid(result: Mapping[str, Any]) -> bool:
    """True iff the result declares a citation block and it is valid."""
    blocks = _citation_blocks(result)
    return bool(blocks) and all(_judge_citation_block(w, b) == [] for w, b in blocks)


def validate_citations(result: Mapping[str, Any]) -> List[str]:
    """إجابة تحمل محتوى واقعيًا يجب أن تستشهد بمصادر صالحة.

    R-170 (دفعة 6): يُبحث في المستوى الأعلى ثم داخل ``tool_output`` (حيث يضع
    الباحث مصادره)؛ والكتلة تُحكم بصيغتها: عنصر واحد على الأقل، وكل عنصر URL
    بمخطط http/https ومضيف (نصًا أو في ``url``/``href``/``link``). عنصر غير
    صالح يرفض الكتلة. كتلة المستوى الأعلى غير الصالحة لا تُنقذ بمصادر الأداة.

    حد معلن: فحص **صيغة** لا إثبات — لا يُتحقق من أن المصدر موجود فعلًا أو
    يدعم الادعاء. يعيد قائمة أسباب الرفض (فارغة = اجتياز).
    """
    blocks = _citation_blocks(result)
    if blocks:
        reasons: List[str] = []
        for where, block in blocks:
            reasons += _judge_citation_block(where, block)
        return reasons
    answer = _answer_text(result)
    if not answer:
        return ["citations: no answer and no citations"]
    # إجابة بلا كتلة استشهادات: ترفض ما لم تكن امتناعًا صريحًا (بلا ادعاء معرفة).
    if _is_abstention(answer):
        return []
    if isinstance(result.get("tool_output"), Mapping):
        return ["citations: factual answer without any citations "
                "(none at top level nor in tool_output)"]
    return ["citations: factual answer without any citations"]


def _is_abstention(answer: str) -> bool:
    lowered = answer.lower()
    markers = ("cannot answer", "not enough information", "insufficient evidence",
               "لا أستطيع", "لا توجد معلومات كافية", "أمتنع عن الإجابة", "لا أدلة كافية")
    return any(m in lowered for m in markers)


#: R-170 (batch 8): numeric failure counters a tool reports about itself.
FAILURE_COUNT_KEYS: Tuple[str, ...] = (
    "failed", "failures", "failed_count", "n_failed", "tests_failed",
    "error_count", "errors_count")
FAILURE_STATUS_VALUES = frozenset({"failed", "failure", "fail", "error", "errored"})
TOOL_OUTPUT_MAX_DEPTH = 4
#: R-170 (batch 10): containers that hold EXTERNAL data a tool fetched or
#: received (an API body, a document, rows), not the tool's report about its
#: own run. A GitLab pipeline list with ``status: "failed"`` inside
#: ``tool_output.sample`` is data the API returned, not a failure of the
#: api_integrator call — descending into it was a false-rejection risk found
#: by the batch-10 gate-risk evaluation design.
PAYLOAD_KEYS = frozenset({"sample", "data", "payload", "body", "response", "content",
                          "raw", "records", "rows", "items", "documents", "json"})


def _is_count(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _as_structure(tool_output: Any) -> Any:
    """A JSON object/array given as text is inspected as the structure it encodes."""
    if isinstance(tool_output, str):
        text = tool_output.strip()
        if text[:1] in ("{", "["):
            try:
                return json.loads(text)
            except ValueError:
                return tool_output
    return tool_output


def tool_output_failure(tool_output: Any) -> Optional[str]:
    """The failure a tool output reports about ITSELF, or None.

    Top level (unchanged since batch 1): numeric ``exit_code`` != 0, ``ok: false``.
    R-170 (batch 8), at any mapping up to depth 4 (JSON text is parsed): a
    numeric failure counter > 0 (FAILURE_COUNT_KEYS), ``passed + skipped <
    total`` (all numeric), or a textual ``status`` in FAILURE_STATUS_VALUES.
    A forged "ALL CHECKS PASSED" summary does not hide ``failed: 7``.
    Booleans are not counts; a numeric ``status`` (HTTP) is not interpreted.
    """
    data = _as_structure(tool_output)
    if isinstance(data, Mapping):
        exit_code = data.get("exit_code")
        if _is_count(exit_code) and exit_code != 0:
            return f"failure evidence (exit_code={exit_code})"
        if data.get("ok") is False:
            return "failure evidence (ok=false inside tool_output)"

    def walk(node: Any, path: str, depth: int) -> Optional[str]:
        if depth > TOOL_OUTPUT_MAX_DEPTH:
            return None
        if isinstance(node, Mapping):
            for key in FAILURE_COUNT_KEYS:
                v = node.get(key)
                if _is_count(v) and v > 0:
                    return f"failure evidence ({path}{key}={v})"
            total, passed = node.get("total"), node.get("passed")
            if _is_count(total) and _is_count(passed):
                skipped = node.get("skipped", 0)
                skipped = skipped if _is_count(skipped) else 0
                if passed + skipped < total:
                    return (f"failure evidence ({path}passed={passed}"
                            f"{f'+skipped={skipped}' if skipped else ''} < total={total})")
            status = node.get("status")
            if isinstance(status, str) and status.strip().lower() in FAILURE_STATUS_VALUES:
                return f"failure evidence ({path}status={status!r})"
            for k, v in node.items():
                if isinstance(k, str) and k.lower() in PAYLOAD_KEYS:
                    continue            # external data, not a self-report
                if isinstance(v, (Mapping, list, tuple)):
                    found = walk(v, f"{path}{k}.", depth + 1)
                    if found:
                        return found
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                found = walk(v, f"{path}[{i}].", depth + 1)
                if found:
                    return found
        return None

    return walk(data, "", 1)


def validate_tool_output(result: Mapping[str, Any]) -> List[str]:
    """نجاح مهمة أداة يتطلب مخرجات أداة فعلية غير فارغة لا تناقض النجاح.

    مخرجات تحمل علامات فشل صريحة (``exit_code`` رقمي ≠ 0، ``ok: False``، ومنذ
    الدفعة 8: عدّاد فشل > 0 أو ``passed < total`` أو ``status`` فشلي — حتى عمق 4)
    ليست دليل نجاح — الرفض مذلل بالسبب.
    """
    tool_output = result.get("tool_output")
    if tool_output in (None, "", [], {}):
        return ["tool_output: empty — success claim has no tool evidence"]
    failure = tool_output_failure(tool_output)
    if failure:
        return [f"tool_output: {failure}"]
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
        or citations_valid(result)  # R-170 (batch 6): valid sources only
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
    {"json", "citations", "tool_output", "checks", "code"})


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
    # R-170 (batch 8): the tool itself reports failure while ok=true is a
    # contradiction too — rejected with or without declared requirements.
    if isinstance(result, Mapping) and result.get("tool_output") not in (None, "", [], {}):
        failure = tool_output_failure(result["tool_output"])
        if failure:
            return Verdict(ok=False, reasons=(f"contradiction: outer ok=true while tool_output "
                                              f"reports {failure}",),
                           checks_run=("tool-consistency",))
    reqs = dict(requirements or {})
    unknown = sorted(set(reqs) - KNOWN_REQUIREMENTS)
    if unknown:
        return Verdict(ok=False, reasons=tuple(
            f"unknown requirement (not verified): {k}" for k in unknown),
            checks_run=("requirements",))
    checks_run: List[str] = []
    reasons: List[str] = []
    if reqs.get("json"):
        spec = reqs["json"]
        if isinstance(spec, Mapping):
            # R-170 (batch 9): {"schema": {...}} — any other key is not verified.
            extra = sorted(set(spec) - {"schema"})
            if extra or "schema" not in spec:
                reasons.append(f"json: unsupported requirement form {sorted(spec)} (not verified)")
            else:
                data, err = _json_payload(result)
                reasons += [err] if err else validate_schema(data, spec["schema"])
        else:
            keys = tuple(spec) if isinstance(spec, (list, tuple)) else ()
            reasons += validate_json(result, keys)
        checks_run.append("json")
    if reqs.get("code"):
        reasons += validate_code(result)
        checks_run.append("code")
    if reqs.get("citations"):
        fmt = validate_citations(result)
        reasons += fmt
        checks_run.append("citations")
        opts = reqs["citations"]
        # R-170 (batch 10): opt-in evidence levels beyond format (existence,
        # quote presence) — see core/citation_evidence. Never by default.
        if isinstance(opts, Mapping) and (opts.get("exists") or opts.get("quote")) and not fmt:
            from agent_civilization.core import citation_evidence as ce
            ev = ce.check_sources(result, exists=bool(opts.get("exists") or opts.get("quote")),
                                  quote=bool(opts.get("quote")), fetcher=ce.default_fetcher)
            reasons += ev["reasons"]
            checks_run.append("citations.exists")
            if opts.get("quote"):
                checks_run.append("citations.quote")
            n_unv = sum(1 for r in ev["sources"] if r.get("status") == "unverifiable")
            if n_unv:
                checks_run.append(f"citations.unverifiable={n_unv}")
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

#: R-170 (batch 7): the ONE per-specialization requirements policy, applied
#: whenever work does not declare its own ``verification`` (``/api/tasks``,
#: the hub path, a DAG stage without requirements) and read by the router
#: (``routing.auto_router.STAGE_VERIFICATION``). Only TOOL handlers whose
#: every successful return carries machine evidence are listed (measured in
#: core/agents/real_dispatcher.py; researcher/network_client also verified
#: live — tests/test_r170_live_tool_evidence.py). Text-only specializations
#: have no machine evidence, so none is required (accepted ``unverified``).
DEFAULT_REQUIREMENTS: Dict[str, Dict[str, Any]] = {
    "test_runner": {"tool_output": True},
    "network_client": {"tool_output": True},
    "validator": {"tool_output": True},
    "researcher": {"tool_output": True, "citations": True},
    # R-170 (batch 10): every successful api_integrator return carries
    # ``tool_output`` (status, content_type, sample); without it a stripped
    # success ("GET ... -> HTTP 200" with no evidence) was accepted unverified.
    "api_integrator": {"tool_output": True},
    # R-170 (batch 10): all three successful code_generator paths (spec, LLM,
    # supplied) now return ``tool_output.code``; the gate compiles it (never runs it).
    "code_generator": {"code": True},
}


def requirements_for(specialization: str, work: Any) -> Optional[Dict[str, Any]]:
    """Requirements for one execution, captured BEFORE the handler runs.

    * work declares ``verification`` (not None) -> that value (a shallow copy
      when it is a dict; a malformed value is passed through so the gate
      rejects it explicitly). ``{}`` = explicit "no requirements" (internal
      callers only — HTTP clients cannot set it).
    * otherwise -> a copy of ``DEFAULT_REQUIREMENTS[specialization]`` or None.
    """
    declared = work.get("verification") if isinstance(work, Mapping) else None
    if declared is not None:
        return dict(declared) if isinstance(declared, Mapping) else declared
    default = DEFAULT_REQUIREMENTS.get(specialization)
    return dict(default) if default is not None else None


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
        # R-170 (batch 4): a pydantic HandlerResult is judged by its FIELDS
        # (a model is not a Mapping, so the envelope would be checked instead
        # — forged success passed and real evidence was missed).
        inner = _result_as_dict(result)
        verdict = verify_success({"ok": True, "result": inner if inner is not None else result},
                                 requirements)
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
