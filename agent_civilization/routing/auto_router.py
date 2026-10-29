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
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple


#: R-170 (batch 5): output-verification requirements the router declares per
#: stage, by specialization (core/output_validators.KNOWN_REQUIREMENTS). Only
#: TOOL handlers whose every successful return carries a non-empty
#: ``tool_output`` (measured in core/agents/real_dispatcher.py: _test_runner,
#: _network_client, _validator, _researcher) declare ``tool_output``. Text-only
#: (LLM) stages declare nothing: no machine evidence exists for them, so a
#: requirement would only produce false rejections. Since batch 6 the
#: researcher also declares ``citations`` (validate_citations looks inside
#: ``tool_output`` too); an instant answer without a source URL is rejected.
#: Requirements act while the gate is ON — the default since PR #280; only
#: AGOS_OUTPUT_VALIDATORS=0 disables it (rollback, see OPERATIONS.md).
# R-170 (batch 7): single source of truth — the core default policy (the
# router copies it per stage in _verification_for, never shares it).
from agent_civilization.core.output_validators import DEFAULT_REQUIREMENTS as STAGE_VERIFICATION  # noqa: E402


def _verification_for(specialization: str) -> Optional[Dict[str, Any]]:
    req = STAGE_VERIFICATION.get(specialization)
    return dict(req) if req is not None else None  # a copy per stage


@dataclass
class Stage:
    name: str
    specialization: str
    prompt: str
    reason: str
    # R-170 (batch 5): declared from STAGE_VERIFICATION unless given; absent
    # (None) -> the key is omitted from to_dict (existing stage shape kept).
    verification: Optional[Dict[str, Any]] = field(default=None)

    def __post_init__(self) -> None:
        if self.verification is None:
            self.verification = _verification_for(self.specialization)

    def to_dict(self) -> Dict:
        d = asdict(self)
        if d.get("verification") is None:
            d.pop("verification", None)
        return d


# Arabic + English intent keywords → specializations
# Order matters — first strong match wins for the *primary* stage.
_INTENT_RULES: List[Tuple[str, str, str]] = [
    # (regex, specialization, human reason)
    # R-115: ACTION verbs are matched before TOPIC nouns. R-113 showed topic
    # words winning over the verb: "explain the difference between REST and
    # GraphQL" went to api_integrator (no URL -> fail), "review this code" to
    # code_generator, "write and run tests" to analyst (tests never ran).
    # --- actions -----------------------------------------------------------
    (r"\b(translate|ترجم)\w*",
     "communicator", "ترجمة"),
    (r"\b(explain|teach|tutorial|beginner|difference between|what is|اشرح|علّم|درس|مبتدئ|ما الفرق)\b",
     "educator", "شرح / تعليم"),
    (r"\b(research|search|find articles|best practices|state of the art|literature|ابحث|بحث|أدبيات|أوراق)\b",
     "researcher", "بحث ويب"),
    (r"\b(draft|email|message|announcement|post|copy|رسالة|إعلان|منشور|صغ)\b",
     "communicator", "كتابة رسائل"),
    (r"\b(run tests?|write tests?|unit tests?|pytest|jest|coverage|tests? for|اختبر|اختبار|تغطية)\b",
     "test_runner", "تشغيل / كتابة اختبارات"),
    # writing NEW code is an action too: "write Python code to compute the
    # average" is a code request even though "average" is an analyst word.
    (r"\b(write|generate|create|implement)\b[^.\n]{0,40}\b(code|function|class|script|program|method)\b"
     r"|(اكتب|أنشئ|ولّد|ولد)[^.\n]{0,30}(كود|دالة|برنامج|سكربت|كلاس)",
     "code_generator", "كتابة كود جديد"),
    (r"\b(bug|bugs|error|exception|traceback|fix|debug|crash|صحح|أصلح|خطأ|استثناء|عطل)\b",
     "surgeon", "تصحيح خطأ / تشخيص"),
    (r"\b(refactor|rewrite|clean ?up|cleaner|modify|rename|update code|change function|عدل|أعد كتابة|حسّن)\b",
     "modifier", "تعديل كود"),
    (r"\b(architecture|architect|design system|scalable|microservice|هندسة|معمارية|نظام)\b",
     "architect", "تصميم معماري"),
    (r"\b(review|audit|code smell|complexity|راجع|جودة الكود|تدقيق)\b",
     "reviewer", "مراجعة / تدقيق"),
    (r"\b(validate|verify schema|json|yaml|xml|parse|تحقق|صحّح صيغة|سكيمة)\b",
     "validator", "تحقق من بنية بيانات"),
    (r"\b(reachable|reachability|ping|is .* up|latency|dns|curl|اتصال|تأخير|متاح)\b",
     "network_client", "فحص اتصال / شبكة"),
    (r"\b(monitor|health check|health|metrics|cpu|memory usage|uptime|راقب|صحة|أداء)\b",
     "monitor", "مراقبة النظام"),
    (r"\b(sum|total|average|mean|median|analy[sz]e|analysis|statistics|trend|حلل|تحليل|إحصاء|متوسط|مجموع)\b",
     "analyst", "تحليل بيانات"),
    # --- topics ------------------------------------------------------------
    (r"\b(database|sql|postgres|mysql|sqlite|table schema|migration|قاعدة بيانات|استعلام)\b",
     "db_manager", "قواعد بيانات / SQL"),
    (r"\b(code|coding|program|python|javascript|typescript|rust|go\b|java\b|c\+\+|function|class|library|package|npm|pip|كود|برمج|دالة|مكتبة|بايثون|جافا)\b",
     "code_generator", "طلب برمجي / كود"),
    (r"\b(build|scaffold|starter|boilerplate|initialize|ابنِ|أنشئ مشروع|هيكل)\b",
     "builder", "بناء / سكافولد"),
    (r"\b(ui|ux|design|color|typography|layout|figma|واجهة|تصميم|ألوان)\b",
     "designer", "تصميم UI/UX"),
    (r"\b(clean data|transform|etl|aggregate|pivot|نظّف|حوّل|جدول)\b",
     "data_processor", "معالجة بيانات"),
    (r"\b(https?://|api|endpoint|integrate|webhook|oauth|graphql|واجهة برمجية|تكامل)\b",
     "api_integrator", "تكامل API"),
    (r"\b(http|proxy|شبكة)\b",
     "network_client", "شبكات / HTTP"),
    (r"\b(strategy|roadmap|market|competitor|okr|kpi|plan|استراتيجية|خارطة طريق|منافس|خطة)\b",
     "strategist", "استراتيجية"),
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


# R-115: an explicit leading analysis verb wins over topic words in the rest of
# the sentence ("حلل أداء النظام" is an analysis, although "أداء" is a monitor word).
_LEADING_RULES = [
    (r"^\s*(analy[sz]e|حلل|حلّل)\b", "analyst", "تحليل (فعل صريح في بداية الطلب)"),
]


def _match_intent(text: str):
    for pattern, spec, r in _LEADING_RULES:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return spec, r
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
            # R-116: the plan stage returns the execution plan that will actually run
            # (strategist); architect stays the primary for architecture requests.
            specialization="strategist",
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
    elif primary == "reviewer" and re.search(r"\btest|اختبر|اختبار", prompt, re.IGNORECASE):
        # "review and test this code" — the review is followed by a real test run
        stages.append(Stage(
            name="test",
            specialization="test_runner",
            prompt="Write and run tests for the reviewed code. Report failures with exact traces.",
            reason="اختبار عملي",
        ))
    elif primary in {"data_processor", "analyst"}:
        stages.append(Stage(
            name="validate",
            specialization="validator",
            prompt="Validate the schema and integrity of the produced analysis / data.",
            reason="التحقق من البيانات",
        ))
    elif primary == "api_integrator":
        stages.append(Stage(
            name="probe",
            specialization="network_client",
            prompt="Perform a real HTTP probe or DNS check on any endpoints mentioned. Report status and latency.",
            reason="فحص حي للاتصال",
        ))
    elif primary in {"strategist", "architect"}:
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
        "goal": prompt.strip(),
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
