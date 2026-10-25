#!/usr/bin/env python3
"""R-170 batch 13 — hollow-success corpus v2 (NEW, independent of v1).

Written before any batch-13 rule change (pr-st-3 fix). The v1 test split
(t1) is closed and is not re-used for tuning. Every case carries an
INDEPENDENT truth used by the oracle in scripts/r170_hollow_eval_v2.py:

* prose families: ``prose_ok`` — whether the hand-written answer is a correct,
  complete answer to the request (True) or hollow/defective (False), with the
  defect named in ``truth.why``.
* handler families: the request truth (objectives / components / colour /
  defects); the oracle judges the handler's actual OUTPUT against it.

Families (``limit`` = expected structural limit, reported but not in criteria):
  prose_list_ok        correct terse lists (pr-st-3 class), ar+en, numbered/bulleted/inline
  prose_short_ok       correct short non-list answers
  prose_list_bad       wrong count, duplicates, placeholders, wrong language, inline wrong count
  prose_bad            bare claims, refusals, echo, promises, empty-ish
  prose_filler_limit   generic filler that names nothing (limit: structure cannot see meaning)
  st_edge / ar_edge / de_edge / rv_edge  handler edge cases
  st_generative_limit  "add more objectives" (limit: ordering given items is not generating)

Splits by sha256("r170-hollow-v2|"+id): dev 40% / validation 30% / test2 30%.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmarks/r170/hollow_v2/cases.jsonl"


def split_of(cid: str) -> str:
    h = int(hashlib.sha256(("r170-hollow-v2|" + cid).encode()).hexdigest()[:8], 16) % 100
    return "dev" if h < 40 else "validation" if h < 70 else "test2"


C = []


def add(cid, spec, lang, family, work, truth, prose=None):
    C.append({"id": cid, "spec": spec, "lang": lang, "family": family, "work": work, "truth": truth,
              **({"prose_result": {"ok": True, "answer": prose}} if prose is not None else {})})


# ------------------------------------------------ prose_list_ok (pr-st-3 class)
LISTS = [
    ("ar", "اذكر خمسة مؤشرات أداء لفريق خدمة العملاء.",
     ["زمن الرد الأول", "متوسط زمن الحل", "رضا العملاء", "نسبة الحل من أول تواصل", "عدد التذاكر المتراكمة"]),
    ("ar", "اذكر ثلاثة مخاطر لإطلاق تطبيق جديد.", ["ضعف الطلب", "تجاوز الميزانية", "ثغرات أمنية"]),
    ("ar", "اذكر أربعة مؤشرات لقياس أداء متجر إلكتروني.",
     ["معدل التحويل", "متوسط قيمة الطلب", "معدل التخلي عن السلة", "تكلفة اكتساب العميل"]),
    ("ar", "عدّد خمس أدوات لإدارة المشاريع.", ["Jira", "Trello", "Asana", "Monday", "ClickUp"]),
    ("ar", "اذكر ثلاث طرق لخفض تكاليف الشحن.", ["التفاوض مع شركات الشحن", "تجميع الطلبات", "تقليل وزن التغليف"]),
    ("ar", "اذكر ستة مؤشرات مالية لشركة ناشئة.",
     ["الإيراد الشهري المتكرر", "معدل الحرق", "مدة التشغيل المتبقية", "هامش الربح الإجمالي",
      "تكلفة اكتساب العميل", "القيمة الدائمة للعميل"]),
    ("ar", "اذكر أربع أفكار لزيادة تفاعل المتابعين.", ["مسابقات أسبوعية", "بث مباشر", "استطلاعات رأي", "محتوى من العملاء"]),
    ("ar", "أعطني ثلاث خطوات لتجهيز مقابلة عمل.", ["ادرس الشركة", "حضّر أمثلة من خبرتك", "جهّز أسئلتك"]),
    ("ar", "اذكر خمسة مؤشرات أداء لفريق التسويق.",
     ["عدد العملاء المحتملين", "تكلفة العميل المحتمل", "معدل النقر", "العائد على الإنفاق الإعلاني", "معدل التحويل"]),
    ("ar", "اذكر ثلاثة مؤشرات لجودة الشيفرة.", ["تغطية الاختبارات", "عدد الأخطاء المفتوحة", "تعقيد الدوال"]),
    ("ar", "اذكر أربعة مخاطر أمنية في تطبيقات الويب.", ["حقن SQL", "XSS", "سرقة الجلسات", "كلمات مرور ضعيفة"]),
    ("ar", "اذكر خمس قنوات لتسويق مطعم.", ["إنستغرام", "خرائط Google", "تطبيقات التوصيل", "المؤثرون المحليون", "الرسائل النصية"]),
    ("ar", "اذكر ثلاثة مؤشرات أداء لمستودع.", ["دقة المخزون", "زمن تجهيز الطلب", "نسبة الطلبات الكاملة"]),
    ("ar", "اذكر سبعة أيام الأسبوع.", ["السبت", "الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة"]),
    ("ar", "اذكر أربعة مؤشرات لرضا الموظفين.", ["معدل الدوران", "نتيجة استبيان الرضا", "نسبة الغياب", "مؤشر توصية الموظفين"]),
    ("ar", "اذكر ثلاث لغات برمجة للويب.", ["JavaScript", "Python", "PHP"]),
    ("en", "List five KPIs for a sales team.",
     ["Closed deals", "Conversion rate", "Average deal size", "Sales cycle length", "Revenue vs target"]),
    ("en", "Name three risks of a data migration.", ["Data loss", "Downtime", "Schema mismatch"]),
    ("en", "List four metrics for a mobile app.", ["Daily active users", "Retention", "Crash rate", "Session length"]),
    ("en", "Give me three steps to prepare a budget.", ["List income", "Track expenses", "Set limits"]),
    ("en", "List six KPIs for a warehouse.",
     ["Inventory accuracy", "Order cycle time", "Fill rate", "Picking accuracy", "Storage utilisation", "Cost per order"]),
    ("en", "Name four project management tools.", ["Jira", "Trello", "Asana", "Notion"]),
]
LISTS += [  # second block, same session, before any rule change
    ("ar", "اذكر ثلاثة مؤشرات لأداء موقع إلكتروني.", ["زمن التحميل", "معدل الارتداد", "عدد الزيارات"]),
    ("ar", "اذكر أربعة أسباب لتأخر المشاريع.", ["سوء التخطيط", "تغيير المتطلبات", "نقص الموارد", "ضعف التواصل"]),
    ("ar", "اذكر خمس مهارات لمدير المشروع.", ["القيادة", "التواصل", "إدارة المخاطر", "التفاوض", "التخطيط"]),
    ("ar", "اذكر ثلاث فوائد للحوسبة السحابية.", ["خفض التكاليف", "سهولة التوسع", "التوفر العالي"]),
    ("ar", "اذكر أربعة مؤشرات أداء لمركز اتصال.", ["متوسط زمن المكالمة", "نسبة المكالمات المتروكة", "زمن الانتظار", "رضا المتصلين"]),
    ("ar", "اذكر ثلاثة مخاطر في الاستثمار العقاري.", ["تقلب الأسعار", "ضعف السيولة", "تكاليف الصيانة"]),
    ("ar", "اذكر خمسة مؤشرات لنجاح تطبيق جوال.", ["عدد التنزيلات", "المستخدمون النشطون يوميًا", "معدل الاحتفاظ", "تقييم المتجر", "معدل الأعطال"]),
    ("ar", "اذكر أربع خطوات لكتابة خطة عمل.", ["الملخص التنفيذي", "تحليل السوق", "الخطة التشغيلية", "التوقعات المالية"]),
    ("ar", "اذكر ثلاث قواعد بيانات شائعة.", ["PostgreSQL", "MySQL", "MongoDB"]),
    ("ar", "اذكر خمسة مؤشرات أداء لفريق الدعم الفني.", ["زمن الاستجابة", "زمن الحل", "نسبة إعادة فتح التذاكر", "رضا العملاء", "عدد التذاكر لكل موظف"]),
    ("ar", "اذكر أربعة مؤشرات لقياس الإنتاجية.", ["الإنتاج لكل ساعة", "نسبة الإنجاز في الموعد", "معدل الأخطاء", "استغلال الوقت"]),
    ("ar", "اذكر ثلاثة أنواع من الاختبارات البرمجية.", ["اختبار الوحدة", "اختبار التكامل", "اختبار القبول"]),
    ("ar", "اذكر ستة شهور ميلادية.", ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو"]),
    ("ar", "اذكر أربع منصات للتجارة الإلكترونية.", ["Shopify", "سلة", "زد", "WooCommerce"]),
    ("ar", "اذكر ثلاثة مؤشرات لصحة التدفق النقدي.", ["صافي التدفق النقدي", "نسبة السيولة", "أيام التحصيل"]),
    ("ar", "اذكر خمس أفكار لمحتوى تسويقي.", ["دروس قصيرة", "قصص العملاء", "مقارنات المنتجات", "كواليس العمل", "أسئلة شائعة"]),
    ("en", "List three cloud providers.", ["AWS", "Azure", "Google Cloud"]),
    ("en", "Name five KPIs for customer support.", ["First response time", "Resolution time", "CSAT", "Reopen rate", "Backlog"]),
    ("en", "List four types of software tests.", ["Unit", "Integration", "End-to-end", "Acceptance"]),
    ("en", "Give three ways to cut cloud costs.", ["Rightsize instances", "Use reserved capacity", "Delete idle resources"]),
]
STYLES = ["dash", "num", "arabic_num", "bullet", "inline"]
for i, (lang, p, items) in enumerate(LISTS):
    style = STYLES[i % len(STYLES)]
    if style == "inline":
        sep = "، " if lang == "ar" else ", "
        ans = sep.join(items) + ("." if lang == "en" else ".")
    elif style == "num":
        ans = "\n".join(f"{k}. {x}" for k, x in enumerate(items, 1))
    elif style == "arabic_num":
        digits = "٠١٢٣٤٥٦٧٨٩"
        ans = "\n".join(f"{''.join(digits[int(d)] for d in str(k))}. {x}" for k, x in enumerate(items, 1))
    elif style == "bullet":
        ans = "\n".join("• " + x for x in items)
    else:
        ans = "\n".join("- " + x for x in items)
    add(f"pl-ok-{i}", "strategist", lang, "prose_list_ok", {"prompt": p},
        {"prose_ok": True, "style": style, "n": len(items)}, ans)
# a correct list with a one-line lead-in
add("pl-ok-lead-0", "strategist", "ar", "prose_list_ok", {"prompt": "اذكر ثلاثة مؤشرات لنجاح حملة إعلانية."},
    {"prose_ok": True, "style": "lead", "n": 3}, "أهم المؤشرات:\n- عدد النقرات\n- معدل التحويل\n- تكلفة الاكتساب")
add("pl-ok-lead-1", "strategist", "en", "prose_list_ok", {"prompt": "List three KPIs for a blog."},
    {"prose_ok": True, "style": "lead", "n": 3}, "Key KPIs:\n- Monthly visitors\n- Time on page\n- Newsletter sign-ups")

# ------------------------------------------------ prose_short_ok
SHORT = [
    ("architect", "ar", "ما الفرق بين SQL وNoSQL باختصار؟",
     "SQL جداول بمخطط ثابت وعلاقات، وNoSQL مرن المخطط ويتوسع أفقيًا بسهولة."),
    ("architect", "ar", "اشرح في جملة واحدة ما هو الـCDN.",
     "شبكة خوادم موزعة تقدّم المحتوى من أقرب موقع للمستخدم لتسريع التحميل."),
    ("architect", "en", "In one sentence, what is a load balancer?",
     "A load balancer spreads incoming requests across several servers so none is overloaded."),
    ("architect", "en", "Briefly, why use a cache?",
     "A cache keeps hot data in fast memory, cutting latency and database load."),
    ("architect", "ar", "لماذا نستخدم طابور رسائل؟ باختصار.",
     "لفصل المرسل عن المستقبل ومعالجة المهام لاحقًا دون فقدانها عند الضغط."),
    ("designer", "ar", "اقترح خطًا عربيًا للعناوين باختصار.",
     "خط Cairo للعناوين: واضح وعصري ويقرأ جيدًا بالأحجام الكبيرة."),
    ("designer", "en", "Suggest one font for headings, briefly.",
     "Use Montserrat for headings: geometric, bold and readable at large sizes."),
    ("reviewer", "ar", "أعط رأيك باختصار في هذا العنوان: «حلول مبتكرة للجميع».",
     "العنوان عام جدًا: لا يذكر الحل ولا الجمهور؛ حدّد الفائدة والعميل المستهدف."),
    ("reviewer", "en", "Briefly review this slogan: 'Quality for everyone'.",
     "Too generic: it names no product or benefit; say what is better and for whom."),
    ("strategist", "ar", "ما أول خطوة لإطلاق متجر إلكتروني؟ باختصار.",
     "أول خطوة تحديد المنتجات والجمهور المستهدف قبل بناء المتجر."),
    ("strategist", "en", "What is the first step to launch an online store? Briefly.",
     "First, define the products and the target customers before building the store."),
    ("architect", "ar", "متى أستخدم WebSocket بدل HTTP؟ باختصار.",
     "عند الحاجة إلى اتصال دائم ثنائي الاتجاه وتحديثات فورية مثل الدردشة."),
]
SHORT += [
    ("architect", "ar", "ما هي الواجهة البرمجية REST باختصار؟", "أسلوب لتبادل البيانات عبر HTTP باستخدام موارد وعمليات قياسية."),
    ("architect", "ar", "لماذا نستخدم الفهارس في قاعدة البيانات؟ باختصار.", "لتسريع البحث في الجداول بدل مسحها كاملة."),
    ("strategist", "ar", "ما أهم مؤشر لمتجر إلكتروني؟ باختصار.", "معدل التحويل، لأنه يقيس نسبة الزوار الذين يشترون فعلًا."),
    ("designer", "ar", "اقترح لونًا أساسيًا لتطبيق صحي باختصار.", "الأخضر الهادئ، لأنه يوحي بالصحة والطمأنينة."),
    ("reviewer", "ar", "أعط رأيك باختصار في هذا الشعار: «نحن الأفضل».", "ادعاء بلا دليل؛ اذكر ميزة محددة يمكن إثباتها."),
]
for i, (spec, lang, p, ans) in enumerate(SHORT):
    add(f"ps-ok-{i}", spec, lang, "prose_short_ok", {"prompt": p}, {"prose_ok": True}, ans)

# ------------------------------------------------ prose_list_bad (detectable defects)
LBAD = [
    ("ar", "اذكر خمسة مؤشرات أداء لفريق المبيعات.", "- عدد الصفقات\n- معدل التحويل\n- الإيراد", "wrong count"),
    ("ar", "اذكر ثلاثة مخاطر لإطلاق تطبيق.", "- مخاطرة\n- مخاطرة\n- مخاطرة", "duplicate items"),
    ("ar", "اذكر أربعة مؤشرات لمتجر إلكتروني.", "1. ...\n2. ...\n3. ...\n4. ...", "placeholder items"),
    ("ar", "اذكر ثلاث طرق لخفض التكاليف.", "- Negotiate with suppliers\n- Reduce waste\n- Automate tasks", "wrong language"),
    ("ar", "اذكر خمسة مؤشرات مالية.", "الإيراد، الربح.", "inline wrong count"),
    ("ar", "اذكر ثلاثة مؤشرات لجودة الشيفرة.", "- مؤشر 1\n- مؤشر 2\n- مؤشر 3", "placeholder items"),
    ("ar", "اذكر أربع قنوات تسويق.", "- إنستغرام\n- إنستغرام\n- تيك توك\n- تيك توك", "duplicate items"),
    ("ar", "أعطني ثلاث خطوات لتجهيز مقابلة.", "1. خطوة\n2. خطوة\n3. خطوة", "placeholder/duplicate"),
    ("ar", "اذكر ستة مؤشرات لشركة ناشئة.", "- الإيراد\n- الحرق\n- الهامش\n- العملاء\n- النمو\n- الإيراد", "duplicate items"),
    ("en", "List five KPIs for a sales team.", "- Deals\n- Revenue", "wrong count"),
    ("en", "Name three risks of a migration.", "- Risk\n- Risk\n- Risk", "duplicate items"),
    ("en", "List four metrics for an app.", "1. TBD\n2. TBD\n3. TBD\n4. TBD", "placeholder items"),
    ("en", "Give me three steps to prepare a budget.", "- اجمع الدخل\n- تتبع المصروفات\n- ضع حدودًا", "wrong language"),
    ("en", "List six KPIs for a warehouse.", "Accuracy, speed.", "inline wrong count"),
]
LBAD += [
    ("ar", "اذكر أربعة أسباب لتأخر المشاريع.", "- سبب أول\n- سبب ثانٍ\n- سبب ثالث\n- سبب رابع", "placeholder items"),
    ("ar", "اذكر خمس مهارات لمدير المشروع.", "القيادة، التواصل، التخطيط.", "inline wrong count"),
    ("ar", "اذكر ثلاث فوائد للحوسبة السحابية.", "- Lower costs\n- Scalability\n- High availability", "wrong language"),
    ("ar", "اذكر ثلاثة أنواع من الاختبارات.", "- اختبار\n- اختبار\n- اختبار", "duplicate items"),
    ("ar", "اذكر خمسة مؤشرات لنجاح تطبيق.", "- التنزيلات\n- التقييم", "wrong count"),
    ("en", "List three cloud providers.", "- Provider\n- Provider\n- Provider", "duplicate items"),
]
for i, (lang, p, ans, why) in enumerate(LBAD):
    add(f"pl-bad-{i}", "strategist", lang, "prose_list_bad", {"prompt": p}, {"prose_ok": False, "why": why}, ans)

# ------------------------------------------------ prose_bad (detectable)
PBAD = [
    ("architect", "ar", "اشرح طبقات التخزين المؤقت.", "تم.", "bare claim"),
    ("architect", "ar", "ما الفرق بين SQL وNoSQL؟", "لا أستطيع المساعدة في ذلك.", "refusal"),
    ("designer", "ar", "اقترح خطوطًا لموقع مطعم.", "سأقوم بإعداد الاقتراحات لاحقًا.", "promise, no deliverable"),
    ("strategist", "ar", "أعطني خطة من ثلاث خطوات لفتح مقهى.", "أعطني خطة من ثلاث خطوات لفتح مقهى.", "echo"),
    ("reviewer", "ar", "راجع هذه الفقرة: منتجنا هو الأفضل.", "تمت المراجعة بنجاح.", "bare claim"),
    ("architect", "en", "Explain what a CDN is.", "I cannot help with that.", "refusal"),
    ("designer", "en", "Suggest fonts for a bakery website.", "I will prepare suggestions later.", "promise, no deliverable"),
    ("strategist", "en", "Give me a 3-step plan to open a cafe.", "Give me a 3-step plan to open a cafe.", "echo"),
    ("reviewer", "en", "Review this paragraph: Our product is the best.", "Review completed successfully.", "bare claim"),
    ("architect", "ar", "متى أستخدم WebSocket؟", "INSUFFICIENT_EVIDENCE — لا تتوفر مصادر.", "abstention"),
    ("architect", "en", "Explain the CAP theorem briefly.", "The CAP theorem is explained here as requested.", "claim, no content"),
    ("designer", "ar", "اقترح خطًا عربيًا للعناوين.", "إليك الخط المقترح.", "claim, no content"),
]
for i, (spec, lang, p, ans, why) in enumerate(PBAD):
    add(f"pb-{i}", spec, lang, "prose_bad", {"prompt": p}, {"prose_ok": False, "why": why}, ans)

# ------------------------------------------------ prose_filler_limit (generic filler; structural limit)
FILL = [
    ("architect", "ar", "اشرح طبقات التخزين المؤقت.",
     "هذا موضوع مهم جدًا ويحتاج إلى دراسة متأنية من جميع الجوانب قبل اتخاذ أي قرار نهائي بشأنه في المؤسسة."),
    ("designer", "ar", "اقترح خطوطًا مناسبة لموقع مكتب محاماة.",
     "يعتمد الاختيار على عوامل كثيرة ومتعددة، ومن المهم مراعاة جميع الجوانب والاحتياجات المختلفة عند اتخاذ القرار المناسب."),
    ("architect", "en", "Explain the difference between TCP and UDP.",
     "This is an important topic with many aspects to consider, and the right answer depends on your specific needs and context."),
    ("reviewer", "en", "Review my essay about remote work for clarity.",
     "Overall this is a piece of writing that has some strengths and some weaknesses, and it could be improved in various ways."),
    ("strategist", "ar", "ما أول خطوة لإطلاق متجر؟",
     "هناك خطوات كثيرة يجب التفكير فيها جيدًا، وكل مشروع يختلف عن غيره بحسب الظروف والإمكانات المتاحة."),
    ("designer", "en", "Suggest one font for headings.",
     "Choosing a font is a very personal decision that depends on many factors, so think about what suits you best overall."),
]
for i, (spec, lang, p, ans) in enumerate(FILL):
    add(f"pf-{i}", spec, lang, "prose_filler_limit", {"prompt": p}, {"prose_ok": False, "why": "generic filler"}, ans)

# ------------------------------------------------ handler edge cases
# strategist: separators / numbering / data
ST_EDGE = [
    ("ar", "رتّب هذه الأهداف كخارطة طريق: كتابة الدليل، إصلاح خطأ الدخول، إضافة اختبارات",
     ["كتابة الدليل", "إصلاح خطأ الدخول", "إضافة اختبارات"]),
    ("ar", "حوّلها إلى خارطة طريق:\n١. تحليل السوق\n٢. بناء النموذج الأولي\n٣. الإطلاق التجريبي",
     ["تحليل السوق", "بناء النموذج الأولي", "الإطلاق التجريبي"]),
    ("ar", "خارطة طريق بالترتيب المعطى: التوظيف؛ التدريب؛ التشغيل؛ التقييم",
     ["التوظيف", "التدريب", "التشغيل", "التقييم"]),
    ("en", "Roadmap these 3 objectives in order: migrate the database; update the API; retire the old service",
     ["migrate the database", "update the API", "retire the old service"]),
    ("en", "Make a roadmap:\n1) audit costs\n2) cut unused licences\n3) renegotiate contracts",
     ["audit costs", "cut unused licences", "renegotiate contracts"]),
    ("ar", "رتّب كخارطة طريق:\n• جمع المتطلبات\n• التصميم\n• التنفيذ", ["جمع المتطلبات", "التصميم", "التنفيذ"]),
]
for i, (lang, p, objs) in enumerate(ST_EDGE):
    add(f"st-edge-{i}", "strategist", lang, "st_edge", {"prompt": p}, {"objectives": objs})
GEN = [
    ("ar", "أضف هدفين آخرين لهذه القائمة: كتابة الدليل؛ إصلاح الخطأ"),
    ("en", "Suggest two more objectives to add to: write docs; fix the bug"),
    ("ar", "حسّن صياغة هذه الأهداف: كتابة الدليل؛ إصلاح الخطأ؛ إضافة اختبارات"),
    ("en", "Rewrite these objectives to be SMART: grow sales; hire staff; cut costs"),
]
for i, (lang, p) in enumerate(GEN):
    add(f"st-gen-{i}", "strategist", lang, "st_generative_limit", {"prompt": p}, {"generative": True})
# architect
AR_EDGE = [
    ("ar", "صمّم معمارية فيها قاعدة بيانات وذاكرة تخزين مؤقت وواجهة برمجية.", ["database", "cache", "api"]),
    ("ar", "صمّم نظامًا فيه تسجيل دخول المستخدمين ومدفوعات.", ["auth", "payments"]),
    ("en", "Design an architecture with a Postgres database, a Redis cache and background jobs.", ["database", "cache", "queue"]),
    ("en", "Design a system with a web dashboard, user login and file uploads.", ["ui", "auth", "storage"]),
    ("en", "Propose components for an app with websockets and push notifications.", ["realtime", "notifications"]),
    ("ar", "اقترح مكونات لنظام فيه بحث وتحليلات.", ["search", "analytics"]),
]
for i, (lang, p, comps) in enumerate(AR_EDGE):
    add(f"ar-edge-{i}", "architect", lang, "ar_edge", {"prompt": p}, {"components": comps})
add("ar-edge-x0", "architect", "ar", "ar_edge", {"prompt": "ما الفرق بين المعمارية أحادية الكتلة والخدمات المصغرة؟"},
    {"explanation": True})
add("ar-edge-x1", "architect", "en", "ar_edge", {"prompt": "Compare monoliths and microservices."}, {"explanation": True})
# designer
DE_EDGE = [
    ("en", "Create a colour palette based on #1E88E5.", {"key": "#1e88e5"}),
    ("en", "Palette from #08f, please.", {"key": "#0088ff"}),
    ("ar", "لوحة ألوان مبنية على #2E7D32 للوضع الليلي.", {"key": "#2e7d32", "theme": "dark"}),
    ("ar", "لوحة ألوان من اللون الأحمر.", {"key": "red"}),
    ("ar", "اقترح لوحة ألوان من البنفسجي بتباين جيد.", {"key": "purple"}),
    ("en", "Build a dark theme palette from teal.", {"key": "teal", "theme": "dark"}),
    ("en", "Palette from rgb(30, 136, 229).", {"key": "#1e88e5"}),
    ("ar", "صمّم لوحة ألوان من البرتقالي لتطبيق توصيل.", {"key": "orange"}),
]
for i, (lang, p, t) in enumerate(DE_EDGE):
    add(f"de-edge-{i}", "designer", lang, "de_edge", {"prompt": p}, {"colour": t["key"], "theme": t.get("theme", "light")})
add("de-edge-f0", "designer", "ar", "de_edge", {"prompt": "اقترح أيقونات لتطبيق بنكي."}, {"unsupported": "icons"})
add("de-edge-f1", "designer", "en", "de_edge", {"prompt": "Design a logo for a coffee shop."}, {"unsupported": "logo"})
# reviewer
RV = [
    ("ar", "راجع هذه الشيفرة:", "def avg(xs):\n    # متوسط القيم\n    return sum(xs) / len(xs)\n", ["empty_division"]),
    ("en", "Review this code:", "async def first(xs):\n    return xs[0]\n", ["empty_index"]),
    ("en", "Review this code:", "class Repo:\n    def find(self, cur, n):\n        cur.execute(f\"SELECT * FROM t WHERE n = '{n}'\")\n        return cur.fetchall()\n", ["sql_injection"]),
    ("ar", "راجع الشيفرة التالية:", "def safe_avg(xs):\n    if xs:\n        return sum(xs) / len(xs)\n    return 0.0\n", []),
    ("en", "Review this code:", "def ratio(xs):\n    return 1 / (len(xs) or 1)\n", []),
    ("en", "Review this code:", "def by_name(cur, n):\n    q = \"SELECT * FROM t WHERE n = '\" + n + \"'\"\n    cur.execute(q)\n    return cur.fetchall()\n", ["sql_injection"]),
    ("ar", "راجع هذه الشيفرة:", "def tidy(s):\n    \"\"\"تنظيف النص\"\"\"\n    return s.strip().lower()\n", []),
    ("en", "Review this code:", "def run(cmd):\n    exec(cmd)\n", ["eval"]),
]
for i, (lang, p, code, defects) in enumerate(RV):
    add(f"rv-edge-{i}", "reviewer", lang, "rv_edge", {"prompt": p, "data": code}, {"defects": defects})


def main() -> int:
    seen = set()
    for c in C:
        assert c["id"] not in seen, c["id"]
        seen.add(c["id"])
        c["split"] = split_of(c["id"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n" for c in C).encode()
    OUT.write_bytes(data)
    print(OUT, len(C), hashlib.sha256(data).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
