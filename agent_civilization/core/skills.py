#!/usr/bin/env python3
"""R-192 — المهارات الإجرائية المقيسة (فوق وحدة الخبرة القائمة).

القاعدة الحاكمة (الخارطة): **لا مساواة حفظ الخبرة باكتساب مهارة**. طبقة
الخبرة (R-167) تسجل ذكريات حلقات المهام وتسترجعها، و D-046 يُبقي المعالجات
حتمية لا تتغير بالتعلم. هذه الطبقة تضيف الخطوة الناقصة: **المهارة
الإجرائية** — إجراء قابل للاستدعاء منسوخ من خبرة موثقة المصدر، قابل
للتنفيذ كخطوات، مقيس الأثر (القياس شرط الادعاء لا الوجود).

التمييز الصريح الذي يفرضه المعيار:
  * حفظ الخبرة = تسجيل ما حدث (الطبقة القائمة — لا تتغير)؛
  * اكتساب المهارة = تحويل فئة فشل موثقة إلى إجراء مشروط قابل للاستدعاء،
    بمصدر قابل للتتبع (معرفات الدخلات الموثقة)، يُستدعى وقت المهمة،
    ويُقاس مخرجه على مهمة مرجعية قبل الاكتساب وبعده.

التصميم (الأمانة والقيود):
  * المصدر دائمًا موثق: ``acquire_from_experience`` يمسح دخلات خبرة
    المستأجر الحقيقية ويتعرف على فئة الفشل المعروفة؛ لا مهارة بلا مصدر
    (يعيد None) — لا اختراع.
  * المهارة مشروطة: محفز (نمط في العمل) + خطوات (تحويل حتمي لنسخة من
    العمل قبل المعالج). المعالج نفسه لا يتغير (D-046 قائم): المهارة
    تحوّل المدخل، لا المعالج.
  * SEC-02: الاكتساب والاستدعاء والعدادات مقيدة بالمستأجر؛ بلا مستأجر
    لا شيء. الأصل لا يُعدل أبدًا (نسخة فقط).
  * القياس: كل استدعاء يُسجل بمخرجه (ok/fail) وعدادات المهارة تتحرك —
    المقارنة قبل/بعد شرط الادعاء.
  * AGOS_SKILLS=0 يعطل الطبقة كلها (سلوك ما قبل R-192 بالضبط).
  * التخزين: كل مهارة دخلة PROCEDURAL في عقد الذاكرة الموحد (R-167)
    بوسم ``layer:skills`` وميتاداتا كاملة — نفس عقد الخبرة، لا مخزن
    منفصل ولا مسار موازٍ.
"""
from __future__ import annotations

import copy
import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from agent_civilization.core import experience, telemetry, tenancy

logger = logging.getLogger("agos.skills")

LAYER_TAG = "layer:skills"

# المحفز: رقم مفصول بفواصل التجميع (١-٣ أرقام ثم مجموعات ,ddd) — تنسيق
# الألوف الشائع؛ القيد «٣ أرقام بالضبط لكل مجموعة» يجعل المطابقة العرضية
# نادرة (لا تطابق قوائمًا مفصولة بفواصل-مسافات).
_GROUPED_ITEM = re.compile(r"^-?\d{1,3}(,\d{3})+(\.\d+)?$")
_GROUPED_IN_TEXT = re.compile(r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b")

# فئة الفشل الموثقة التي تتعرف عليها قاعدة التقطير (فئة واحدة — معيار
# القبول: مهارة واحدة): محلل استقبل أرقامًا مجمعة ولم يحللها.
_FAILURE_MARKER = "no numeric samples"


def enabled() -> bool:
    return os.getenv("AGOS_SKILLS", "1").strip() != "0"


def _active_tenant() -> Optional[str]:
    try:
        return tenancy.current_tenant()
    except Exception:  # noqa: BLE001 — no ambient tenant is handled below
        return None


# ---------------------------------------------------------------------------
# الاستدعاء: خطوات المهارة تحوّل نسخة من العمل قبل المعالج
# ---------------------------------------------------------------------------
async def maybe_apply(specialization: str, work: Dict[str, Any],
                      tenant_id: Optional[str] = None
                      ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """إن وُجدت مهارة نشطة للمستأجر تطابق هذا العمل، تُنفَّذ خطواتها:
    تحويل حتمي لنسخة من العمل. الأصل لا يُعدل أبدًا؛ بلا تطابق يُعاد
    العمل نفسه بلا استدعاء.

    Returns ``(work, invocation)`` — invocation تسجل لاحقًا بمخرجه
    (``record_outcome``)؛ القياس شرط الادعاء لا الوجود."""
    tid = tenant_id if tenant_id is not None else _active_tenant()
    if not enabled() or tid is None or not isinstance(work, dict):
        return work, None
    try:
        skill = _find_matching(tid, specialization, work)
        if skill is None:
            return work, None
        original_prompt = experience._work_text(work)[:experience._TEXT_LIMIT]
        transformed = copy.deepcopy(work)
        applied = _run_steps(skill, transformed)
        if not applied:
            return work, None
        invocation: Dict[str, Any] = {
            "skill_id": skill["skill_id"],
            "tenant_id": tid,
            "specialization": specialization,
            "steps_applied": [s["action"] for s in skill["steps"]],
            "source_memory_ids": list(skill.get("source_memory_ids") or []),
            "outcome_ok": None,
            "original_prompt": original_prompt,
        }
        with telemetry.span("workflow", f"skill.{skill['skill_id']}",
                           tenant_id=tid) as sp:
            sp.set(applied=True)
        return transformed, invocation
    except Exception as exc:  # noqa: BLE001 — the skill layer must never break a task
        logger.warning("skill apply failed: %s", type(exc).__name__)
        return work, None


def _find_matching(tenant_id: str, specialization: str,
                   work: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for skill in _skills_of(tenant_id):
        if skill.get("status") != "active":
            continue
        if skill.get("specialization") != specialization:
            continue
        if _trigger_matches(skill, work):
            return skill
    return None


def _trigger_matches(skill: Dict[str, Any], work: Dict[str, Any]) -> bool:
    """المحفز: نمط الأرقام المجمعة موجود في عناصر data أو في نص العمل."""
    data = work.get("data")
    if isinstance(data, list) and any(
            isinstance(x, str) and _GROUPED_ITEM.match(x.strip()) for x in data):
        return True
    text = experience._work_text(work)
    return bool(_GROUPED_IN_TEXT.search(text))


def _run_steps(skill: Dict[str, Any], work: Dict[str, Any]) -> bool:
    """تنفيذ الخطوات (إجراء واحد لكل فئة المهارة الحالية) — تحويل حتمي."""
    applied = False
    for step in skill.get("steps") or []:
        if step.get("action") == "normalize_grouped_numbers":
            applied = _normalize_grouped_numbers(work) or applied
    return applied


def _normalize_grouped_numbers(work: Dict[str, Any]) -> bool:
    """إزالة فواصل التجميع من الأرقام: عناصر data النصية المطابقة
    بالكامل، وكل نمط مجمّع داخل الحقول النصية."""
    changed = False
    data = work.get("data")
    if isinstance(data, list):
        for i, x in enumerate(data):
            if isinstance(x, str) and _GROUPED_ITEM.match(x.strip()):
                data[i] = x.strip().replace(",", "")
                changed = True
    for key in ("prompt", "text", "query", "goal", "message"):
        v = work.get(key)
        if isinstance(v, str) and _GROUPED_IN_TEXT.search(v):
            work[key] = _GROUPED_IN_TEXT.sub(lambda m: m.group(0).replace(",", ""), v)
            changed = True
    return changed


def record_outcome(invocation: Dict[str, Any], result: Dict[str, Any]) -> None:
    """تسجيل مخرج الاستدعاء على عدادات المهارة (القياس شرط الادعاء)."""
    if not invocation:
        return
    ok = bool(isinstance(result, dict) and result.get("ok"))
    invocation["outcome_ok"] = ok
    tid = invocation.get("tenant_id")
    with _STATS_LOCK:
        row = _STATS.setdefault(tid, {}).setdefault(
            invocation["skill_id"], {"invocations": 0, "ok": 0, "fail": 0})
        row["invocations"] += 1
        row["ok" if ok else "fail"] += 1
    with telemetry.span("workflow", f"skill.{invocation['skill_id']}.outcome") as sp:
        sp.set(outcome="ok" if ok else "fail")


# ---------------------------------------------------------------------------
# الاكتساب: من الخبرة الموثقة إلى مهارة إجرائية (بلا اختراع)
# ---------------------------------------------------------------------------
async def acquire_from_experience(tenant_id: str,
                                  specialization: str) -> Optional[Dict[str, Any]]:
    """تقطير مهارة من دخلات خبرة المستأجر الموثقة.

    قاعدة التقطير (موثقة، فئة واحدة — معيار القبول مهارة واحدة): دخلات
    المحلل التي يحمل مدخلها الموثق أرقامًا مجمعة وتحمل إجابته الموثقة
    وسم السقوط الصامت («no numeric samples») — أي الأرقام وُجدت في المدخل
    ولم تُحلَّل. المهارة المستخلصة: تحويل حتمي يزيل فواصل التجميع قبل
    المعالج (يجعل المدخل بالشكل الذي يحلله المحلل).

    المصدر قابل للتتبع: معرفات الدخلات تُحفظ في المهارة (source_memory_ids).
    بلا دخلات من هذه الفئة → None (لا مهارة بلا مصدر موثق — لا اختراع)."""
    if not enabled():
        return None
    tenancy.check_scope(tenant_id)
    try:
        st = experience.mem_store()
    except Exception:  # noqa: BLE001 — no store: no acquisition
        return None
    source_ids: List[str] = []
    for e in st.entries():
        m = e.metadata or {}
        if m.get("layer") != "experience" or m.get("tenant_id") != tenant_id:
            continue
        if m.get("specialization") != specialization:
            continue
        prompt = " ".join(str(m.get(k) or "") for k in ("prompt", "goal", "data", "text"))
        answer = str(m.get("answer") or "")
        if _GROUPED_IN_TEXT.search(prompt) and _FAILURE_MARKER in answer:
            source_ids.append(e.memory_id)
    if not source_ids:
        return None
    skill: Dict[str, Any] = {
        "skill_id": f"{specialization}.normalize_grouped_numbers",
        "specialization": specialization,
        "status": "active",
        "trigger": "grouped-thousands numbers in data/text with no numeric analysis",
        "steps": [{"action": "normalize_grouped_numbers",
                   "fields": ["data", "prompt", "text", "query", "goal", "message"]}],
        "source_memory_ids": source_ids,
        "failure_marker": _FAILURE_MARKER,
        "acquired_from": "documented experience entries (R-167 store)",
    }
    # صف عدادات صفري فور الاكتساب (يظهر في stats قبل أول استدعاء)
    _ensure_stats_row(tenant_id, skill["skill_id"])
    # التخزين: دخلة PROCEDURAL في نفس عقد الذاكرة الموحد — لا مخزن موازٍ
    content = (f"skill {skill['skill_id']}: normalize grouped-thousands numbers "
               f"before {specialization} analysis (from {len(source_ids)} "
               f"documented failures)")
    try:
        await st.write(content=content,
                       memory_type=experience.MemoryType.PROCEDURAL,
                       tags={LAYER_TAG, f"spec:{specialization}",
                             f"tenant:{tenant_id}", "skill"},
                       metadata={"layer": "skills", "tenant_id": tenant_id,
                                 "skill": skill},
                       importance=0.9)
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort here
        logger.warning("skill persist failed: %s", type(exc).__name__)
    return skill


# ---------------------------------------------------------------------------
# القراءة والعدادات (القياس)
# ---------------------------------------------------------------------------
def _skills_of(tenant_id: str) -> List[Dict[str, Any]]:
    try:
        st = experience.mem_store()
    except Exception:  # noqa: BLE001
        return []
    out: List[Dict[str, Any]] = []
    for e in st.entries():
        m = e.metadata or {}
        if m.get("layer") == "skills" and m.get("tenant_id") == tenant_id \
                and isinstance(m.get("skill"), dict):
            out.append(m["skill"])
    return out


async def list_skills(tenant_id: str) -> List[Dict[str, Any]]:
    tenancy.check_scope(tenant_id)
    return _skills_of(tenant_id)


_STATS_LOCK = threading.Lock()
_STATS: Dict[str, Dict[str, Dict[str, int]]] = {}


def _ensure_stats_row(tenant_id: str, skill_id: str) -> None:
    """صف عدادات صفري عند الاكتساب — المهارة المكتسبة تظهر في العدادات
    فورًا (بصفر استدعاءات) قبل أول استدعاء (القياس يشمل الحالة الصفرية)."""
    with _STATS_LOCK:
        _STATS.setdefault(tenant_id, {}).setdefault(
            skill_id, {"invocations": 0, "ok": 0, "fail": 0})

_INVOCATIONS: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}


def invocations_of(tenant_id: str, skill_id: str) -> List[Dict[str, Any]]:
    """استدعاءات المهارة المسجلة بمخرجها (القياس القابل للتحقق)."""
    return list(_INVOCATIONS.get(tenant_id, {}).get(skill_id, []))


def stats() -> Dict[str, Dict[str, Dict[str, int]]]:
    """عدادات كل مهارة لكل مستأجر (invocations/ok/fail) — نسخة عميقة."""
    with _STATS_LOCK:
        return {t: {s: dict(row) for s, row in skills.items()}
                for t, skills in _STATS.items()}


def reset() -> None:
    """الاختبارات: تصفير العدادات والاستدعاءات (المخزن الموحد يُدار
    بعقد الخبرة نفسه — experience.reset)."""
    with _STATS_LOCK:
        _STATS.clear()
    _INVOCATIONS.clear()


def note_invocation(invocation: Dict[str, Any]) -> None:
    """تسجيل الاستدعاء (يستدعيه مسار الخبرة عند تفعيل المهارة) —
    يُلحق بالمخرج لاحقًا عبر record_outcome."""
    if not invocation:
        return
    tid = invocation.get("tenant_id")
    sid = invocation.get("skill_id")
    if not tid or not sid:
        return
    _INVOCATIONS.setdefault(tid, {}).setdefault(sid, []).append(invocation)
