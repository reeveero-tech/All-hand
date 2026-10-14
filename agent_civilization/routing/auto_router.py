#!/usr/bin/env python3
"""
Auto-Router — the civilization decides which agents handle a request.

The user submits a natural-language goal. The router:
  1. Classifies the intent (Arabic + English keywords + LLM assist optional).
  2. Decomposes the goal into an ordered pipeline of stages.
  3. Picks a specialization for each stage.
  4. Returns a plan the orchestrator executes.

No manual specialization selection is exposed to the user.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple


@dataclass
class Stage:
    name: str
    specialization: str
    prompt: str
    reason: str

    def to_dict(self) -> Dict:
        return asdict(self)


# Arabic + English intent keywords → specializations
# Order matters — first strong match wins for the *primary* stage.
_INTENT_RULES: List[Tuple[str, str, str]] = [
    # (regex, specialization, human reason)
    (r"\b(code|coding|program|python|javascript|typescript|rust|go\b|java\b|c\+\+|function|class|library|package|npm|pip|كود|برمج|دالة|مكتبة|بايثون|جافا)\b",
     "code_generator", "طلب برمجي / كود"),
    (r"\b(bug|error|exception|traceback|fix|debug|crash|صحح|أصلح|خطأ|استثناء|عطل)\b",
     "surgeon", "تصحيح خطأ / تشخيص"),
    (r"\b(refactor|rewrite|modify|update code|change function|عدل|أعد كتابة|حسّن)\b",
     "modifier", "تعديل كود"),
    (r"\b(review|audit|code smell|complexity|راجع|جودة الكود|تدقيق)\b",
     "reviewer", "مراجعة / تدقيق"),
    (r"\b(test|unit test|pytest|jest|coverage|اختبر|اختبار|تغطية)\b",
     "test_runner", "تشغيل / كتابة اختبارات"),
    (r"\b(validate|schema|json|yaml|xml|parse|تحقق|صحّح صيغة|سكيمة)\b",
     "validator", "تحقق من بنية بيانات"),
    (r"\b(architecture|architect|design system|scalable|microservice|هندسة|معمارية|نظام)\b",
     "architect", "تصميم معماري"),
    (r"\b(build|scaffold|starter|boilerplate|initialize|ابنِ|أنشئ مشروع|هيكل)\b",
     "builder", "بناء / سكافولد"),
    (r"\b(ui|ux|design|color|typography|layout|figma|واجهة|تصميم|ألوان)\b",
     "designer", "تصميم UI/UX"),
    (r"\b(analyze|analysis|statistics|mean|median|trend|حلل|تحليل|إحصاء|متوسط)\b",
     "analyst", "تحليل بيانات"),
    (r"\b(clean data|transform|etl|aggregate|pivot|نظّف|حوّل|جدول)\b",
     "data_processor", "معالجة بيانات"),
    (r"\b(database|sql|postgres|mysql|sqlite|schema|migration|قاعدة بيانات|استعلام)\b",
     "db_manager", "قواعد بيانات / SQL"),
    (r"\b(api|endpoint|integrate|webhook|oauth|rest|graphql|واجهة برمجية|تكامل)\b",
     "api_integrator", "تكامل API"),
    (r"\b(http|curl|dns|proxy|ping|latency|شبكة|اتصال|تأخير)\b",
     "network_client", "شبكات / HTTP"),
    (r"\b(search|research|find articles|paper|state of the art|ابحث|بحث|أدبيات|أوراق)\b",
     "researcher", "بحث ويب"),
    (r"\b(monitor|health check|metrics|cpu|memory|uptime|راقب|صحة|أداء)\b",
     "monitor", "مراقبة النظام"),
    (r"\b(strategy|roadmap|market|competitor|okr|kpi|استراتيجية|خارطة طريق|منافس)\b",
     "strategist", "استراتيجية"),
    (r"\b(explain|teach|tutorial|learn|beginner|اشرح|علّم|درس|مبتدئ)\b",
     "educator", "شرح / تعليم"),
    (r"\b(translate|arabic|english|french|spanish|ترجم|بالعربية|بالإنجليزية)\b",
     "communicator", "ترجمة / تواصل"),
    (r"\b(email|message|announcement|post|copy|رسالة|إعلان|منشور)\b",
     "communicator", "كتابة رسائل"),
    (r"\b(improve myself|self improve|meta|evolve|طور نفسك|تطور ذاتي)\b",
     "self_developer", "تطوير ذاتي"),
]

# Universal early / late stages (added around the primary specialization)


# R-106: payload that follows an instruction (a JSON object, a fenced code
# block, a double-quoted string) is data, not intent.  "تحقق من صحة هذا JSON:
# {"name":"test"}" asks for validation — the word "test" inside the data must
# not route it to test_runner.
_FENCE_RE = re.compile(r"```.*?```", re.S)
_DQUOTE_RE = re.compile(r'"[^"\n]*"|“[^”\n]*”')
_BRACES_RE = re.compile(r"\{[^{}]*\}")


def _instruction_text(text: str) -> str:
    """``text`` with embedded data removed (innermost JSON objects first)."""
    out = _FENCE_RE.sub(" ", text)
    out = _DQUOTE_RE.sub(" ", out)
    for _ in range(10):  # nested objects: strip innermost until none remain
        stripped = _BRACES_RE.sub(" ", out)
        if stripped == out:
            break
        out = stripped
    return out


def _match_intent(text: str):
    for pattern, spec, r in _INTENT_RULES:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return spec, r
    return None
def _decompose(prompt: str, primary: str, reason: str) -> List[Stage]:
    """Break the goal into stages. The civilization always plans, executes, verifies."""
    p_short = prompt.strip()

    stages: List[Stage] = []

    # Stage 1 — understand
    stages.append(Stage(
        name="understand",
        specialization="analyst",
        prompt=f"Analyze this user request and list: (a) explicit goal, (b) implicit constraints, (c) success criteria. Request:\n{p_short}",
        reason="فهم الطلب واستخراج المعايير",
    ))

    # Stage 2 — plan (skip when primary is a very short/atomic task like translation)
    ATOMIC = {"communicator", "educator", "validator", "monitor"}
    if primary not in ATOMIC:
        stages.append(Stage(
            name="plan",
            specialization="architect" if primary in {"code_generator","builder","modifier","surgeon","api_integrator","db_manager"} else "strategist",
            prompt=f"Produce a concrete step-by-step plan to accomplish this goal. Be practical, list files/tools/APIs needed. Goal:\n{p_short}",
            reason="خطة تنفيذ محكمة",
        ))

    # Stage 3 — primary execution
    stages.append(Stage(
        name="execute",
        specialization=primary,
        prompt=p_short,
        reason=reason,
    ))

    # Stage 4 — verify / review
    if primary in {"code_generator", "modifier", "builder", "surgeon"}:
        stages.append(Stage(
            name="review",
            specialization="reviewer",
            prompt="Review the previous execution output. List issues, risks, and concrete fixes. If code, do a static analysis pass.",
            reason="مراجعة النتيجة",
        ))
        stages.append(Stage(
            name="test",
            specialization="test_runner",
            prompt="Write and run tests for the produced code. Report failures with exact traces.",
            reason="اختبار عملي",
        ))
    elif primary in {"data_processor", "analyst"}:
        stages.append(Stage(
            name="validate",
            specialization="validator",
            prompt="Validate the schema and integrity of the produced analysis / data.",
            reason="التحقق من البيانات",
        ))
    elif primary in {"api_integrator", "network_client"}:
        stages.append(Stage(
            name="probe",
            specialization="network_client",
            prompt="Perform a real HTTP probe or DNS check on any endpoints mentioned. Report status and latency.",
            reason="فحص حي للاتصال",
        ))
    elif primary in {"researcher", "strategist", "architect"}:
        stages.append(Stage(
            name="ground",
            specialization="researcher",
            prompt="Perform a live web search to ground and cross-check the previous output. Return sources.",
            reason="تأصيل بمصادر حية",
        ))

    return stages


def route(prompt: str) -> Dict:
    """Classify and decompose a user request into an execution plan."""
    text = prompt.lower()

    # Detect primary intent — first strong match; default = analyst.
    # R-106: match the instruction first; only if it carries no keyword, look
    # inside the data too (e.g. a bare ```python``` block still means code).
    primary = "analyst"
    reason = "طلب عام — يبدأ بالتحليل"
    hit = _match_intent(_instruction_text(text)) or _match_intent(text)
    if hit:
        primary, reason = hit

    stages = _decompose(prompt, primary, reason)
    return {
        "primary": primary,
        "reason": reason,
        "stages": [s.to_dict() for s in stages],
    }


if __name__ == "__main__":
    import json
    for q in [
        "اكتب لي دالة بايثون لحساب فيبوناتشي حتى n",
        "حلل هذه الأرقام: 3, 5, 8, 13, 21",
        "design a scalable multi-tenant SaaS architecture",
        "translate 'مرحبا بالعالم' to English and French",
        "there is a bug in my code, ImportError on numpy",
    ]:
        print("Q:", q)
        print(json.dumps(route(q), indent=2, ensure_ascii=False))
        print()
