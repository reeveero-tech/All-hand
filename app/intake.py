"""Intake brief: what the request says, what it leaves out, and whether the team can start without guessing.

Built by code from the request text, with no model call, before anything else in a run (planning, research,
agents). Production run 6a03d324e329 is why this exists: the request said only «منتج جديد في السوق السعودي»
with no product and no budget. The planner planned, research read World Bank population pages and unrelated
news, every agent either wrote «غير متاح» or guessed, the reviewer rejected all five tasks, and 66 model replies
were spent on a study no team could do. A human consultant would have asked one question first.

A gap is *blocking* when no honest answer exists without it (a market study of an unnamed product, a financial
report with no budget). The run then waits for the owner: they answer, or say «continue with labelled
assumptions», which the plan carries as assumptions, never as facts the user stated.
"""
from __future__ import annotations

import re

from . import assumptions

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

# A business study: the request asks to study / analyse / plan something in a market.
_STUDY = re.compile(r"ادرس|دراسة|جدوى|حلّ?ل|تحليل|ابحث|سوق|منافس|إطلاق|اطلاق|خطة|تقرير\s*مالي|"
                    r"\bstudy\b|feasibility|market|competitor|launch|business plan", re.I)
_MARKETISH = re.compile(r"سوق|منافس|إطلاق|اطلاق|جدوى|طلب|أسعار|تسعير|عملاء|market|competitor|launch|feasibility|pricing", re.I)
# A financial deliverable built on the owner's money: these need a budget. Mentioning costs is not enough
# («تكاليفه الثابتة 35 ألف ريال» states a fact; it does not ask for a plan to spend a budget).
_FINANCE = re.compile(r"تقرير(?:ًا|ا)?\s*مالي|دراسة\s*(?:مالية|جدوى)|خطة\s*مالية|نموذج(?:ًا|ا)?\s*مالي|جدوى\s*(?:مالية|اقتصادية)|"
                      r"financial\s+(?:report|plan|model|study)|feasibility", re.I)
# A placeholder instead of a named subject: «منتج جديد», «مشروع ما», «فكرة», «a new product».
_GENERIC = re.compile(r"(?:منتج|منتجًا|منتجا|مشروع|مشروعًا|مشروعا|خدمة|فكرة|نشاط|تطبيق)\s+(?:جديد|جديدة|جديدًا|جديدا|ما)\b|"
                      r"\b(?:a|the)\s+new\s+(?:product|business|service|venture|idea)\b", re.I)
# What follows a placeholder and names it: «منتج جديد (قهوة باردة)», «منتج جديد: عطر», «منتج جديد وهو ...».
_NAMED_AFTER = re.compile(r"^\s*(?:[(:\-–—«\"]|وهو|هو|وهي|هي|عبارة\s+عن|اسمه|من\s+نوع|في\s+مجال|لبيع|يقدم|"
                          r"called|named|namely|[:(\-–—])", re.I)
_LABELLED = re.compile(r"(?:^|[\s،,.؛])(?:المنتج|المشروع|الخدمة|الفكرة|النشاط|التطبيق|product|business|service)\s*[:：]\s*\S", re.I)
_MARKET = re.compile(r"السعودي|السعودية|المملكة|الرياض|جدة|مكة|المدينة|الدمام|الخبر|الخليج|الإمارات|دبي|أبوظبي|الكويت|قطر|"
                     r"البحرين|عُمان|عمان|مصر|الأردن|المغرب|العراق|Saudi|KSA|Riyadh|Jeddah|GCC|UAE|Dubai|Egypt|Kuwait|Qatar", re.I)
_HORIZON = re.compile(r"خلال\s+\S+\s+(?:شهر|أشهر|شهرًا|شهرا|سنة|سنوات|أسابيع)|\d+\s*(?:شهر|أشهر|شهرًا|شهرا|سنة|سنوات|أسابيع)|"
                      r"\b\d+\s*(?:months?|years?|weeks?)\b|عام\s*\d{4}", re.I)
_FILE = re.compile(r"ملف|تقرير|مستند|PDF|Word|Excel|عرض\s*تقديمي|report|document|file|deck", re.I)

QUESTIONS = {
    "subject": {"question": "ما المنتج أو المشروع المقصود بالتحديد؟",
                "why": "الطلب يذكر «منتجًا جديدًا» دون تسميته، فلا يمكن البحث عن منافسيه ولا أسعاره ولا طلبه دون تخمين.",
                "example": "مثل: قهوة باردة جاهزة معلّبة، أو متجر عطور إلكتروني"},
    "budget": {"question": "ما الميزانية المتاحة (مع العملة)؟",
               "why": "التقرير المالي وخطة التنفيذ يُبنيان على الميزانية؛ بدونها تصبح كل الأرقام افتراضات.",
               "example": "مثل: 200 ألف ريال"},
    "market": {"question": "في أي سوق أو مدينة؟",
               "why": "المنافسون والأسعار والطلب تختلف من سوق إلى آخر.",
               "example": "مثل: السوق السعودي، أو الرياض"},
}


def _subject_named(goal: str) -> bool:
    """False only when the request uses a placeholder («منتج جديد») and never names what it is."""
    if _LABELLED.search(goal):
        return True
    generic = list(_GENERIC.finditer(goal))
    if not generic:
        return True  # no placeholder: the request names its subject in its own words
    for m in generic:
        clause = re.split(r"[،,.؛;\n]", goal[m.end():m.end() + 80])[0]  # the rest of the clause that holds the placeholder
        if _NAMED_AFTER.match(clause) or re.search(r"[(:«]", clause):  # «منتج جديد في السوق السعودي (قهوة باردة)»
            return True
    return False


def brief(goal: str) -> dict:
    """The request as the team will receive it: kind, what is stated, what is missing, and what blocks a start."""
    g = (goal or "").translate(_AR_DIGITS)
    study = bool(_STUDY.search(g)) and bool(_MARKETISH.search(g))
    finance = bool(_FINANCE.search(g))
    budget = (assumptions.amounts(g) or [None])[0]
    market = _MARKET.search(g)
    horizon = _HORIZON.search(g)
    stated = {}
    if budget:
        stated["budget"] = {"value": budget["value"], "unit": budget["unit"], "text": budget["text"]}
    if market:
        stated["market"] = market.group(0)
    if horizon:
        stated["horizon"] = horizon.group(0)
    missing = []
    if study and not _subject_named(g):
        missing.append({"key": "subject", "blocking": True, **QUESTIONS["subject"]})
    if study and finance and not budget:
        missing.append({"key": "budget", "blocking": True, **QUESTIONS["budget"]})
    if study and not market and missing:  # only asked alongside a blocking question, never on its own
        missing.append({"key": "market", "blocking": False, **QUESTIONS["market"]})
    return {"kind": "business_study" if study else ("financial" if finance else "general"),
            "stated": stated, "wants_file": bool(_FILE.search(g)), "missing": missing,
            "blocking": [m["key"] for m in missing if m["blocking"]]}


LABEL = {"subject": "المنتج أو المشروع", "budget": "الميزانية", "market": "السوق"}


def apply_answers(goal: str, b: dict, answers: dict, assume: bool) -> tuple[str, dict]:
    """The request the team will work on after the owner's reply, and the record kept with the run.
    Answers are the owner's own words (tagged «من المستخدم»). What they chose not to answer is an assumption the
    team must choose, justify and label; it is never written as something the user said."""
    clean = {k: re.sub(r"\s+", " ", str(v or "")).strip()[:300] for k, v in (answers or {}).items()
             if k in QUESTIONS and str(v or "").strip()}
    asked = [m["key"] for m in b.get("missing") or []]
    open_ = [k for k in asked if k not in clean]
    lines = []
    if clean:
        lines.append("معطيات أكدها المستخدم قبل التخطيط (من المستخدم):")
        lines += [f"- {LABEL[k]}: {v}" for k, v in clean.items()]
    assumed = [k for k in open_ if assume]
    if assumed:
        lines.append("لم يحدد المستخدم ما يلي وطلب المتابعة بافتراضات: اختر لكل منها قيمة معقولة، واذكر سبب اختيارها، "
                     "ووسمها (افتراض) في كل موضع، ولا تنسبها إلى المستخدم:")
        lines += [f"- {LABEL[k]}" for k in assumed]
    new_goal = goal.rstrip() + ("\n\n" + "\n".join(lines) if lines else "")
    return new_goal, {"answers": clean, "assumed": assumed, "unanswered": [k for k in open_ if not assume]}
