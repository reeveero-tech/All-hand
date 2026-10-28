#!/usr/bin/env python3
"""سياسة مطابقة اللغة للردود — R-170 (دفعة 16): مرشح «إعادة محدودة عند
اختلاف اللغة» على مسار الاستدعاء الفعلي (llm.py — الفرع المجاني فقط).

هذه وحدة **إنتاج** لا قياس: دوال نقية مستقلة عن المدقق المجمد
(r169_bench_v5.language_match) — لا تستورَد منه ولا تشاركه كودًا؛ الهدف
المشترك (نفس الفكرة) لا يعني الدائرية، لكن أي ادعاء يظل مقتصرًا على
الامتثال لقاعدة الكاشف هذه نفسها.

القاعدة المعلنة (تُجمَّد في PREREGISTRATION_V8 قبل أي تشغيل):
- لغة الهدف تُستخرج من **المدخل الفعلي** (الموجّه الحي) لا من بيانات الاختبار:
  «ar» إذا كانت حروف العربية ≥ 50% من حروف الكتابة في المدخل، «en» إذا كانت
  < 20% مع وجود لاتيني، وما بينهما غامض ⇒ لا إعادة (سياسة محددة سلفًا).
- الرد يُنظف من كتل الشيفرة والشيفرة السطرية والاقتباسات المرقعة والروابط
  ورمز INSUFFICIENT_EVIDENCE قبل العد (عناصر كهذه وحدها لا تُلزم إعادة).
- رد أقل من 3 حروف كتابة بعد التنظيف = غير قابل للحكم ⇒ لا إعادة.
- «ar»: مطابقة ⇔ العربية ≥ 50% من حروف الكتابة. «en»: مطابقة ⇔ العربية < 20%.
"""
from __future__ import annotations

import re

# نطاقات الحروف العربية (بدون علامات التشكيل والرموز)
_AR = re.compile(r"[\u0621-\u063A\u0640-\u064A\u0671-\u06D3]")
_LA = re.compile(r"[A-Za-z]")

# تنظيف الرد: كتل شيفرة، شيفرة سطرية، اقتباسات مرقعة [S1]/【…】، روابط، الرمز
_STRIP = re.compile(
    r"```.*?```|`[^`]*`|[\[【［][^\]】］]*[\]】］]|https?://\S+|INSUFFICIENT_EVIDENCE",
    flags=re.S)

CORRECTION = {
    # نص التصحيح المُلحق بالموجّه عند الإعادة — جزء مجمد من المرشح
    "ar": ("\n\nIMPORTANT LANGUAGE CORRECTION: the question is in ARABIC, so "
           "your ENTIRE reply must be in Arabic. Do not reply in English. If "
           "you use the INSUFFICIENT_EVIDENCE token, the text after it must "
           "be Arabic: \"INSUFFICIENT_EVIDENCE — المصادر المتاحة لا تذكر هذه المعلومة.\"\n"),
    "en": ("\n\nIMPORTANT LANGUAGE CORRECTION: the question is in ENGLISH, so "
           "your ENTIRE reply must be in English. Do not reply in Arabic. If "
           "you use the INSUFFICIENT_EVIDENCE token, the text after it must "
           "be English: \"INSUFFICIENT_EVIDENCE — The available sources do not "
           "mention this fact.\"\n"),
}


def _counts(text: str) -> tuple[int, int]:
    t = _STRIP.sub(" ", text or "")
    return len(_AR.findall(t)), len(_LA.findall(t))


def target_language(prompt: str) -> str | None:
    """لغة الهدف من المدخل الفعلي — None يعني غامضًا/غير قابل للحكم (لا إعادة)."""
    ar, la = _counts(prompt or "")
    total = ar + la
    if total == 0:
        return None
    if ar >= 0.5 * total:
        return "ar"
    if ar < 0.2 * total:
        return "en"
    return None  # النطاق الغامض 20–50% عربية


def reply_matches(content: str, target: str) -> bool | None:
    """هل لغة الرد تطابق الهدف؟ None = رد غير قابل للحكم (لا إعادة بسبب عناصر
    الشيفرة/الرمز/الأرقام وحدها)."""
    ar, la = _counts(content or "")
    if ar + la < 3:
        return None
    if target == "ar":
        return ar >= 0.5 * (ar + la)
    return ar < 0.2 * (ar + la)  # target "en"
