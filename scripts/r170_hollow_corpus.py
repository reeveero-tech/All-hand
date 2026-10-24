#!/usr/bin/env python3
"""Build the R-170 batch-12 hollow-success corpus (deterministic; sha pinned in the
pre-registration). Every case carries an INDEPENDENT ground truth written with the
case, before any task-fit rule existed:

* expect = "completable": the deterministic handler can do it; ``truth`` says what a
  correct output contains (objectives / components / base colour / code defects).
* expect = "not_completable": the handler cannot do it (generate content, explain,
  fonts/layout/icons, prose without code) — success here is HOLLOW.
* family "prose_correct": a hand-written CORRECT answer as another implementation
  (e.g. an LLM) might return it; it must not be rejected for its form.

Splits by sha256(case id) — dev 40% / validation 25% / test 35% — so a case's split
does not depend on its content order.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmarks/r170/hollow/cases.jsonl"


def split_of(cid: str) -> str:
    h = int(hashlib.sha256(("r170-hollow|" + cid).encode()).hexdigest()[:8], 16) % 100
    return "dev" if h < 40 else "validation" if h < 65 else "test"


C = []


def add(cid, spec, lang, expect, work, truth=None, family=None, prose=None):
    C.append({"id": cid, "spec": spec, "lang": lang, "expect": expect, "family": family or f"{spec}_{expect}",
              "work": work, "truth": truth or {}, **({"prose_result": prose} if prose else {})})


# ---------------- strategist ----------------
OBJ = [
    ("en", ["write the onboarding docs", "fix the login timeout bug", "add integration tests"]),
    ("en", ["migrate the database to Postgres 16", "update the backup scripts", "train support staff"]),
    ("en", ["hire a designer", "redesign the checkout page", "run an A/B test"]),
    ("en", ["audit third-party licences", "remove unused dependencies"]),
    ("en", ["collect customer feedback", "draft the Q3 roadmap", "present it to the board", "publish the summary"]),
    ("ar", ["كتابة دليل المستخدم", "إصلاح خطأ تسجيل الدخول", "إضافة اختبارات تكامل"]),
    ("ar", ["نقل الخوادم إلى مركز البيانات الجديد", "تحديث سياسة النسخ الاحتياطي", "تدريب فريق الدعم"]),
    ("ar", ["مراجعة عقود الموردين", "تقليل تكاليف الشحن"]),
    ("ar", ["إطلاق النسخة التجريبية", "جمع ملاحظات العملاء", "إصلاح الأخطاء الحرجة", "الإطلاق العام"]),
    ("ar", ["تعيين مدير منتج", "تحديد مؤشرات الأداء", "إعداد خطة الربع القادم"]),
]
for i, (lang, objs) in enumerate(OBJ):
    if lang == "en":
        add(f"st-list-{i}", "strategist", lang, "completable",
            {"prompt": "Turn these into an ordered roadmap, in this order: " + "; ".join(objs)},
            {"objectives": objs})
        add(f"st-data-{i}", "strategist", lang, "completable",
            {"prompt": "Make a step-by-step roadmap from the objectives given.", "data": objs},
            {"objectives": objs})
        add(f"st-rank-{i}", "strategist", lang, "not_completable",
            {"prompt": "Rank these by business impact: " + "; ".join(objs)},
            {"why": "ranking by a criterion; handler only keeps the given order"})
    else:
        add(f"st-list-{i}", "strategist", lang, "completable",
            {"prompt": "حوّل هذه إلى خارطة طريق مرتبة بالترتيب نفسه: " + "؛ ".join(objs)},
            {"objectives": objs})
        add(f"st-data-{i}", "strategist", lang, "completable",
            {"prompt": "اجعل الأهداف المعطاة خطة خطوة بخطوة.", "data": objs}, {"objectives": objs})
        add(f"st-rank-{i}", "strategist", lang, "not_completable",
            {"prompt": "رتّب هذه حسب الأهمية للعمل: " + "؛ ".join(objs)},
            {"why": "ranking by a criterion"})
GEN_ST = [
    ("en", "Give me a 3-step plan to launch a mobile app."), ("en", "List five KPIs for a support team."),
    ("en", "What are the main risks of migrating to the cloud?"), ("en", "Suggest ways to reduce customer churn."),
    ("en", "Create a hiring plan for a 10-person startup."), ("en", "Plan a product launch for next month."),
    ("en", "How should we prioritise technical debt?"), ("en", "Outline a marketing strategy for a bakery."),
    ("ar", "أعطني خطة من ثلاث خطوات لإطلاق متجر إلكتروني."), ("ar", "اذكر خمسة مؤشرات أداء لفريق المبيعات."),
    ("ar", "ما أهم مخاطر التوسع إلى سوق جديدة؟"), ("ar", "اقترح طرقًا لزيادة رضا العملاء."),
    ("ar", "ضع خطة تسويق لمقهى صغير."), ("ar", "كيف نرتب أولويات الديون التقنية؟"),
    ("ar", "خطط لحملة إطلاق منتج الشهر القادم."), ("ar", "اقترح استراتيجية لخفض التكاليف التشغيلية."),
]
for i, (lang, p) in enumerate(GEN_ST):
    add(f"st-gen-{i}", "strategist", lang, "not_completable", {"prompt": p}, {"why": "generate new content"})

# ---------------- architect ----------------
ARCH_OK = [
    ("en", "Design an architecture with a web dashboard, a REST API, user login and a Postgres database.",
     ["ui", "api", "auth", "database"]),
    ("en", "Outline the components for a service with background jobs, a Redis cache and file uploads.",
     ["queue", "cache", "storage"]),
    ("en", "Architecture for a SaaS app: multi-tenant, with billing and search.", ["tenancy", "payments", "search"]),
    ("en", "Design a chat backend with websockets, a database and push notifications.",
     ["realtime", "database", "notifications"]),
    ("en", "Propose components for an analytics pipeline with a Kafka queue and a SQL database.",
     ["analytics", "queue", "database"]),
    ("en", "Design an API with rate limiting and a cache.", ["api", "rate_limiting", "cache"]),
    ("ar", "صمّم معمارية فيها واجهة المستخدم وواجهة برمجية ومصادقة وقاعدة بيانات.",
     ["ui", "api", "auth", "database"]),
    ("ar", "اقترح مكونات نظام فيه مهام خلفية وتخزين مؤقت ورفع ملفات.", ["queue", "cache", "storage"]),
    ("ar", "صمم بنية لتطبيق فيه دفع واشتراك وبحث.", ["payments", "search"]),
    ("ar", "حدد مكونات منصة محادثة لحظية مع إشعار وقاعدة بيانات.", ["realtime", "notifications", "database"]),
    ("ar", "صمم مكونات نظام تحليلات مع طابور وقاعدة بيانات.", ["analytics", "queue", "database"]),
    ("ar", "صمم واجهة برمجية مع تحديد المعدل وتخزين مؤقت.", ["api", "rate_limiting", "cache"]),
]
for i, (lang, p, comps) in enumerate(ARCH_OK):
    add(f"ar-ok-{i}", "architect", lang, "completable", {"prompt": p}, {"components": comps})
ARCH_NO = [
    ("en", "Describe an architecture for a URL shortener."), ("en", "Explain caching layers briefly."),
    ("en", "When should a team choose a message queue over HTTP calls?"),
    ("en", "What is the difference between a monolith and microservices?"),
    ("en", "Why do systems use a load balancer?"), ("en", "Compare REST and GraphQL for a mobile client."),
    ("en", "Design the architecture of a recipe-sharing website."),
    ("en", "How does database sharding work?"),
    ("ar", "صف معمارية لخدمة اختصار الروابط."), ("ar", "اشرح طبقات التخزين المؤقت باختصار."),
    ("ar", "متى يختار الفريق طابور رسائل بدل استدعاءات HTTP؟"), ("ar", "ما الفرق بين التطبيق الأحادي والخدمات المصغرة؟"),
    ("ar", "لماذا تستخدم الأنظمة موازن الحمل؟"), ("ar", "قارن بين REST و GraphQL لتطبيق جوال."),
    ("ar", "صمم معمارية موقع لمشاركة الوصفات."), ("ar", "كيف يعمل تقسيم قواعد البيانات؟"),
]
for i, (lang, p) in enumerate(ARCH_NO):
    add(f"ar-no-{i}", "architect", lang, "not_completable", {"prompt": p}, {"why": "explain / no components named"})

# ---------------- designer ----------------
DES_OK = [
    ("en", "Create a colour palette based on #1e88e5 for a dashboard.", {"hex": "#1e88e5"}),
    ("en", "Build a dark mode palette from green.", {"word": "green", "theme": "dark"}),
    ("en", "I need an accessible palette around navy with WCAG contrast.", {"word": "navy"}),
    ("en", "Make a colour scheme from purple for a landing page.", {"word": "purple"}),
    ("en", "Palette based on #ff7043, light theme.", {"hex": "#ff7043"}),
    ("en", "Dark theme colours from teal please.", {"word": "teal", "theme": "dark"}),
    ("ar", "أنشئ لوحة ألوان من الأزرق للوحة تحكم.", {"word": "أزرق"}),
    ("ar", "لوحة ألوان للوضع الليلي من الأخضر.", {"word": "أخضر", "theme": "dark"}),
    ("ar", "ألوان مبنية على #8e24aa مع تباين مناسب.", {"hex": "#8e24aa"}),
    ("ar", "اقترح ألوانًا من البرتقالي لموقع مطعم.", {"word": "برتقالي"}),
    ("ar", "لوحة ألوان من الكحلي بتباين جيد.", {"word": "كحلي"}),
    ("ar", "ألوان داكنة من البنفسجي.", {"word": "بنفسجي", "theme": "dark"}),
]
for i, (lang, p, t) in enumerate(DES_OK):
    add(f"de-ok-{i}", "designer", lang, "completable", {"prompt": p}, t)
DES_NO = [
    ("en", "Suggest a font pairing for a law firm website."), ("en", "Propose a simple layout for a restaurant landing page."),
    ("en", "Design an icon for the settings screen."), ("en", "Suggest a colour palette for a meditation app and say why."),
    ("en", "Pick colours for a children's education app."), ("en", "Sketch a wireframe for a sign-up form."),
    ("en", "Design a logo for a coffee brand."), ("en", "What typography should a news site use?"),
    ("ar", "اقترح خطوطًا مناسبة لموقع مكتب محاماة."), ("ar", "اقترح تخطيطًا بسيطًا لصفحة هبوط لمطعم."),
    ("ar", "صمم أيقونة لشاشة الإعدادات."), ("ar", "اقترح ألوانًا لواجهة تطبيق مصرفي."),
    ("ar", "اختر ألوانًا لتطبيق تعليمي للأطفال."), ("ar", "صمم شعارًا لعلامة قهوة."),
    ("ar", "ارسم هيكلًا مبدئيًا لنموذج التسجيل."), ("ar", "ما نوع الخط المناسب لموقع إخباري؟"),
]
for i, (lang, p) in enumerate(DES_NO):
    add(f"de-no-{i}", "designer", lang, "not_completable", {"prompt": p}, {"why": "not a palette / palette not derivable"})

# ---------------- reviewer ----------------
REV_OK = [  # (lang, prompt prefix, code, expected defect kinds)
    ("en", "Review this code:", "def avg(xs):\n    return sum(xs) / len(xs)\n", ["empty_division"]),
    ("en", "Review and suggest an improvement:", "def first(xs):\n    return xs[0]\n", ["empty_index"]),
    ("en", "Is this SQL safe?", "def find(cur, name):\n    cur.execute(\"SELECT * FROM users WHERE name = '\" + name + \"'\")\n    return cur.fetchall()\n", ["sql_injection"]),
    ("en", "Review:", "def load(path):\n    try:\n        return open(path).read()\n    except:\n        return None\n", ["bare_except"]),
    ("en", "Find bugs in this function:", "def add_item(item, items=[]):\n    items.append(item)\n    return items\n", ["mutable_default"]),
    ("en", "Review this loop:", "def total(xs):\n    t = 0\n    for i in range(len(xs)):\n        t += xs[i]\n    return t\n", ["range_len"]),
    ("en", "Check this code:", "def is_admin(user):\n    if user.admin == True:\n        return True\n    return False\n", ["bool_compare"]),
    ("en", "Is this safe?", "def calc(expr):\n    return eval(expr)\n", ["eval"]),
    ("en", "Review this query helper:", "def by_id(cur, uid):\n    cur.execute(f\"SELECT * FROM t WHERE id = {uid}\")\n    return cur.fetchone()\n", ["sql_injection"]),
    ("en", "Review:", "def check(x):\n    if x == None:\n        return 0\n    return x\n", ["none_compare"]),
    ("ar", "راجع هذه الشيفرة:", "def avg(xs):\n    return sum(xs) / len(xs)\n", ["empty_division"]),
    ("ar", "راجع واقترح تحسينًا:", "def last(xs):\n    return xs[-1]\n", ["empty_index"]),
    ("ar", "هل هذا الاستعلام آمن؟", "def find(cur, name):\n    cur.execute(\"SELECT * FROM users WHERE name = '%s'\" % name)\n    return cur.fetchall()\n", ["sql_injection"]),
    ("ar", "راجع:", "def read(p):\n    try:\n        return open(p).read()\n    except:\n        pass\n", ["bare_except"]),
    ("ar", "ابحث عن الأخطاء:", "def add(x, acc={}):\n    acc[x] = 1\n    return acc\n", ["mutable_default"]),
    ("ar", "راجع هذه الحلقة:", "def s(xs):\n    out = 0\n    for i in range(len(xs)):\n        out += xs[i]\n    return out\n", ["range_len"]),
    ("ar", "حسّن هذه الدالة:", "def ok(flag):\n    if flag == True:\n        return 1\n    return 0\n", ["bool_compare"]),
    ("ar", "هل هذا آمن؟", "def run(code):\n    exec(code)\n", ["eval"]),
]
for i, (lang, pre, code, kinds) in enumerate(REV_OK):
    add(f"rv-ok-{i}", "reviewer", lang, "completable", {"prompt": f"{pre}\n```python\n{code}```"}, {"defects": kinds})
REV_CLEAN = [  # correct code, generic review: "no defects found by these checks" is a correct, completed review
    ("en", "Review this code:", "def add(a, b):\n    return a + b\n"),
    ("en", "Any bugs here?", "def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n"),
    ("en", "Review:", "def safe_avg(xs):\n    if not xs:\n        return 0.0\n    return sum(xs) / len(xs)\n"),
    ("ar", "راجع هذه الشيفرة:", "def mul(a, b):\n    return a * b\n"),
    ("ar", "هل في هذه الدالة أخطاء؟", "def head(xs):\n    if not xs:\n        return None\n    return xs[0]\n"),
    ("ar", "راجع:", "def find(cur, name):\n    cur.execute(\"SELECT * FROM users WHERE name = ?\", (name,))\n    return cur.fetchall()\n"),
]
for i, (lang, pre, code) in enumerate(REV_CLEAN):
    add(f"rv-clean-{i}", "reviewer", lang, "completable", {"prompt": f"{pre}\n```python\n{code}```"}, {"defects": []})
REV_NO = [
    ("en", "Review my essay about climate change for clarity."), ("en", "Is this SQL query safe? I use string building."),
    ("en", "Review the design of our onboarding flow."), ("en", "Give feedback on this paragraph: we deliver value."),
    ("en", "Review the code I sent yesterday."), ("en", "Review our pull request process."),
    ("ar", "راجع مقالي عن التغير المناخي من حيث الوضوح."), ("ar", "هل استعلام SQL الخاص بي آمن؟ أبنيه بدمج النصوص."),
    ("ar", "راجع تصميم رحلة تسجيل المستخدمين."), ("ar", "أعط رأيك في هذه الفقرة: نحن نقدم قيمة."),
    ("ar", "راجع الشيفرة التي أرسلتها أمس."), ("ar", "راجع آلية طلبات الدمج لدينا."),
]
for i, (lang, p) in enumerate(REV_NO):
    add(f"rv-no-{i}", "reviewer", lang, "not_completable", {"prompt": p}, {"why": "no code to review"})

# ---------------- prose_correct (another implementation's correct answer) ----------------
PROSE = [
    ("strategist", "en", "Give me a 3-step plan to launch a mobile app.",
     "1. Validate the idea with 20 target users and define the MVP scope.\n2. Build and beta-test the MVP with a small group, fixing the critical issues.\n3. Publish to the app stores and run a launch campaign, tracking installs and retention."),
    ("strategist", "en", "List five KPIs for a support team.",
     "- First response time\n- Average resolution time\n- Customer satisfaction (CSAT)\n- First-contact resolution rate\n- Ticket backlog size"),
    ("strategist", "ar", "أعطني خطة من ثلاث خطوات لإطلاق متجر إلكتروني.",
     "1. حدّد المنتجات والجمهور المستهدف وجهّز صفحات المتجر.\n2. فعّل الدفع والشحن واختبر الطلبات التجريبية.\n3. أطلق المتجر بحملة تسويقية وتابع المبيعات وملاحظات العملاء."),
    ("strategist", "ar", "اذكر خمسة مؤشرات أداء لفريق المبيعات.",
     "- عدد الصفقات المغلقة\n- معدل التحويل\n- متوسط قيمة الصفقة\n- طول دورة البيع\n- الإيراد مقارنة بالهدف"),
    ("architect", "en", "When should a team choose a message queue over HTTP calls?",
     "Choose a message queue when the caller does not need an immediate answer: long or bursty work, retries without losing requests, and decoupling producers from consumers that may be down. Keep HTTP for synchronous request/response where the caller needs the result now."),
    ("architect", "en", "Explain caching layers briefly.",
     "Caching layers keep copies of data closer to the reader: the browser cache, a CDN at the edge, an application cache such as Redis, and the database's own buffer cache. Each layer trades freshness for speed, so each needs an expiry or invalidation rule."),
    ("architect", "ar", "متى يختار الفريق طابور رسائل بدل استدعاءات HTTP؟",
     "يُختار طابور الرسائل عندما لا يحتاج المرسل إلى رد فوري: الأعمال الطويلة أو المتقطعة، وإعادة المحاولة دون فقدان الطلبات، وفصل المنتج عن المستهلك الذي قد يتوقف. ويبقى HTTP للطلبات المتزامنة التي يحتاج فيها المرسل النتيجة الآن."),
    ("architect", "ar", "اشرح طبقات التخزين المؤقت باختصار.",
     "طبقات التخزين المؤقت تحفظ نسخًا من البيانات قرب القارئ: ذاكرة المتصفح، وشبكة توزيع المحتوى، وذاكرة التطبيق مثل Redis، وذاكرة قاعدة البيانات نفسها. كل طبقة تبادل حداثة البيانات بالسرعة، لذلك تحتاج قاعدة انتهاء أو إبطال."),
    ("designer", "en", "Suggest a font pairing for a law firm website.",
     "Pair a classic serif for headings, such as Libre Baskerville, with a clean sans-serif for body text, such as Source Sans 3. The serif signals tradition and trust; the sans-serif keeps long paragraphs readable on screens."),
    ("designer", "ar", "اقترح خطوطًا مناسبة لموقع مكتب محاماة.",
     "استخدم خطًا مذيلًا كلاسيكيًا للعناوين مثل Amiri، وخطًا بسيطًا غير مذيل للنصوص مثل Noto Sans Arabic. الخط المذيل يوحي بالرصانة والثقة، والخط البسيط يحافظ على سهولة قراءة الفقرات الطويلة."),
    ("designer", "en", "Suggest a colour palette for a meditation app and say why.",
     "Use soft sage green (#9caf88) as the primary colour, warm sand (#e8dcc4) as the background and deep slate (#2f3e46) for text. Muted greens and sand tones read as calm and natural, and the dark slate keeps text contrast high."),
    ("reviewer", "en", "Review my essay about climate change for clarity.",
     "The essay's argument is clear in the introduction, but the second paragraph mixes causes and effects; split it into two paragraphs. Define 'carbon budget' when you first use it, and replace the passive sentences in the conclusion with active ones."),
    ("reviewer", "ar", "أعط رأيك في هذه الفقرة: نحن نقدم قيمة.",
     "الفقرة قصيرة وعامة جدًا: لا تذكر ما القيمة ولا لمن. اذكر الفائدة المحددة والعميل المستهدف ودليلًا واحدًا، مثل: «نختصر وقت إعداد الفواتير للمتاجر الصغيرة إلى النصف»."),
    ("reviewer", "en", "Review this code:\n```python\ndef avg(xs):\n    return sum(xs) / len(xs)\n```",
     "avg() raises ZeroDivisionError when xs is empty (line 2). Guard it: `if not xs: return 0.0` (or raise a clear ValueError) before dividing."),
]
for i, (spec, lang, p, ans) in enumerate(PROSE):
    add(f"pr-{spec[:2]}-{i}", spec, lang, "completable", {"prompt": p}, {"prose": True},
        family="prose_correct", prose={"ok": True, "answer": ans})
# hollow prose: generic filler that does not do the task (must be rejected)
PROSE_HOLLOW = [
    ("strategist", "en", "Give me a 3-step plan to launch a mobile app.", "Task completed successfully."),
    ("strategist", "ar", "اذكر خمسة مؤشرات أداء لفريق المبيعات.", "تم إنجاز المهمة بنجاح."),
    ("architect", "en", "Explain caching layers briefly.", "Done."),
    ("architect", "ar", "اشرح طبقات التخزين المؤقت باختصار.", "INSUFFICIENT_EVIDENCE"),
    ("designer", "en", "Suggest a font pairing for a law firm website.", "Here is the result you asked for."),
    ("designer", "ar", "اقترح خطوطًا مناسبة لموقع مكتب محاماة.", "Here are some fonts: Arial."),
    ("reviewer", "en", "Review my essay about climate change for clarity.", "Reviewed."),
    ("reviewer", "ar", "أعط رأيك في هذه الفقرة: نحن نقدم قيمة.", "تمت المراجعة."),
    ("strategist", "en", "List five KPIs for a support team.", "- First response time\n- CSAT"),
    ("strategist", "ar", "أعطني خطة من ثلاث خطوات لإطلاق متجر إلكتروني.", "Step 1: plan. Step 2: build. Step 3: launch."),
]
for i, (spec, lang, p, ans) in enumerate(PROSE_HOLLOW):
    add(f"ph-{spec[:2]}-{i}", spec, lang, "not_completable", {"prompt": p}, {"why": "hollow/irrelevant prose"},
        family="prose_hollow", prose={"ok": True, "answer": ans})

# ---------------- expansion (same batch, before any rule existed): other input paths ----------------
for i, (lang, objs) in enumerate(OBJ):
    head = "Order these as a roadmap:\n" if lang == "en" else "رتّبها كخارطة طريق بالترتيب المعطى:\n"
    add(f"st-bullets-{i}", "strategist", lang, "completable",
        {"prompt": head + "\n".join("- " + o for o in objs)}, {"objectives": objs})
    crit = "Prioritise these by cost: " if lang == "en" else "رتّب هذه حسب التكلفة: "
    add(f"st-rank2-{i}", "strategist", lang, "not_completable", {"prompt": crit + "; ".join(objs)},
        {"why": "ranking by a criterion"})
for i, (lang, p, comps) in enumerate(ARCH_OK):
    names = {"ui": "web dashboard", "api": "REST API", "auth": "user login", "database": "Postgres database",
             "queue": "background jobs", "cache": "Redis cache", "storage": "file uploads", "tenancy": "multi-tenant",
             "payments": "billing", "search": "search", "realtime": "websockets", "notifications": "push notifications",
             "analytics": "analytics", "rate_limiting": "rate limiting"}
    add(f"ar-data-{i}", "architect", lang, "completable",
        {"prompt": "Design the architecture for these components." if lang == "en" else "صمم المعمارية لهذه المكونات.",
         "data": [names[c] for c in comps]}, {"components": comps})
DES_OK2 = [("en", "Palette from red for an alerts dashboard.", {"word": "red"}),
           ("en", "Colours based on gold, light theme.", {"word": "gold"}),
           ("en", "A brown colour scheme for a café menu site.", {"word": "brown"}),
           ("en", "Pink palette with accessible contrast.", {"word": "pink"}),
           ("en", "Ocean-inspired colour palette, dark mode.", {"word": "ocean", "theme": "dark"}),
           ("en", "Desert colours palette for a travel blog.", {"word": "desert"}),
           ("ar", "لوحة ألوان من الأحمر لتنبيهات النظام.", {"word": "أحمر"}),
           ("ar", "ألوان من الذهبي بثيم فاتح.", {"word": "ذهبي"}),
           ("ar", "لوحة ألوان من البني لموقع مقهى.", {"word": "بني"}),
           ("ar", "ألوان من الوردي بتباين مناسب.", {"word": "وردي"}),
           ("ar", "لوحة ألوان مستوحاة من البحر للوضع الليلي.", {"word": "بحر", "theme": "dark"}),
           ("ar", "ألوان الصحراء لمدونة سفر.", {"word": "صحراء"})]
for i, (lang, p, t) in enumerate(DES_OK2):
    add(f"de-ok2-{i}", "designer", lang, "completable", {"prompt": p}, t)
for i, (lang, pre, code, kinds) in enumerate(REV_OK):
    add(f"rv-data-{i}", "reviewer", lang, "completable", {"prompt": pre, "data": code}, {"defects": kinds})
for i, (lang, pre, code) in enumerate(REV_CLEAN):
    add(f"rv-cdata-{i}", "reviewer", lang, "completable", {"prompt": pre, "data": code}, {"defects": []})
GEN_ST2 = [("en", "Write a 30-60-90 day plan for a new manager."), ("en", "What should our OKRs be this quarter?"),
           ("en", "Brainstorm ideas to grow newsletter subscribers."), ("en", "Propose a pricing strategy for a SaaS tool."),
           ("ar", "اكتب خطة 30-60-90 يومًا لمدير جديد."), ("ar", "ما الأهداف الربعية المناسبة لفريقنا؟"),
           ("ar", "اقترح أفكارًا لزيادة مشتركي النشرة البريدية."), ("ar", "ضع استراتيجية تسعير لأداة برمجية.")]
for i, (lang, p) in enumerate(GEN_ST2):
    add(f"st-gen2-{i}", "strategist", lang, "not_completable", {"prompt": p}, {"why": "generate new content"})
REV_NO2 = [("en", "Review this contract clause for risks."), ("en", "Proofread my cover letter."),
           ("en", "Review our marketing plan."), ("ar", "راجع هذا البند في العقد من حيث المخاطر."),
           ("ar", "دقق خطاب التقديم الخاص بي."), ("ar", "راجع خطة التسويق لدينا.")]
for i, (lang, p) in enumerate(REV_NO2):
    add(f"rv-no2-{i}", "reviewer", lang, "not_completable", {"prompt": p}, {"why": "no code to review"})

for c in C:
    c["split"] = split_of(c["id"])

if __name__ == "__main__":
    OUT.write_text("".join(json.dumps(c, ensure_ascii=False, sort_keys=True) + "\n" for c in C), encoding="utf-8")
    import collections
    print(len(C), collections.Counter((c["split"], c["expect"]) for c in C))
    print(hashlib.sha256(OUT.read_bytes()).hexdigest())
