# سجل التغييرات (CHANGELOG)

## 2026-10-12 — M0: Baseline Audit + Roadmap Bootstrap

### مضاف
- `docs/governance/` — مجموعة الوثائق الحاكمة: PROJECT_CHARTER، PROJECT_STATE، ROADMAP (R-000..R-030)، ARCHITECTURE، DOMAIN، API، DATA_MODEL، OPERATIONS، SECURITY، QUALITY، DECISIONS (D-001..D-007)، RISKS (RISK-001..016)، OPEN_QUESTIONS (Q-001..Q-014)، GOVERNANCE_GATE، README.
- `docs/audit/M0/` — دليل التحقق: EVIDENCE.md + 12 سجل تنفيذ (الأمر، returncode، stdout/stderr المقتطعة، المدة — الاقتطاع موثق في EVIDENCE) + BASELINE_SHA + ENVIRONMENT.

### خط الأساس المقاس
- `pytest tests/`: 34 ناجح، 9 فاشل، 18 خطأ إعداد (Python 3.14.3).
- `import server`: فشل على 3.12.13 و3.14.3 (استيراد `utc` من `datetime` — `server.py:28`).
- `ruff agos-kernel/`: 8,961 خطأ. `ruff` المسارات الحية: 525 خطأ.
- `pytest agos-kernel/ --collect-only`: 4 أخطاء جمع؛ التنفيذ الكامل انتهى بمهلة (تجربة غير مكتملة).
- `npm run build` في `web/`: نجاح (902ms). في الجذر: فشل (rc=1).
- Docker: لم يُنفَّذ (غير متوفر في بيئة التدقيق).

### مُحدَّث (وصل توثيقي)
- `README.md` الجذري — قسم حوكمة جديد يصل بـ`docs/governance/`.
- `SECURITY.md` الجذري — قسم "الوضع الأمني المقاس (M0)" يوثق الفجوة بين التوثيق والقياس ويحيل إلى `docs/governance/SECURITY.md`.

### لم يُغيَّر
- لا تغيير تطبيقي على أي كود أو إعدادات. `git diff` عن `be480820` يحتوي `docs/` + وصل README/SECURITY الجذريين فقط (تغيير توثيقي).
## 2026-10-12 — M1: إصلاح الانحدار الحرج + بوابة الجودة

### مُصلَح
- `server.py:28` — استيراد `utc` المكسور؛ `datetime.utcnow()` → `datetime.now(timezone.utc)` (R-001).
- `CircuitBreaker.__init__` يقبل معاملات الحقول مباشرة بدلاً من `__init_subclass__` (R-003a).
- `timeout<=0` يعني "لا إعادة تعيين تلقائي" (R-003b).
- `get_queue_size()` يرجع `qsize()` بدلاً من إشارة `_sleep_once` (R-003c).
- `error_box()` لا يخفي نوع الاستثناء (R-003d).
- pytest: 61/61 (كان 34/34 ناجح و9 فاشل و18 خطأ).

## 2026-10-12 — M1: بوابة الجودة CI

### مُضاف
- `.github/workflows/ci.yml` موجهة للمسارات الحية فقط (`server.py`, `agent_civilization/` باست `specialists/`, `agos_kernel/`, `tests/`, `web/`): ruff (F,E722) وpytest 61 اختباراً وnpm build.
- 36 خطأ lint قاتل مُصلَح في المسارات الحية (R-002).
- `publish` job يتطلب `[publish]` في رسالة الدمج (R-030، D-011).

## 2026-10-12 — M1: خط الأساس الأمني

### مُصلَح
- `verify_api_key` يُفرض على `/api/tasks`, `/api/chat`, `/api/agents/external*`, `/civilization/*` (R-004).
- CORS: `allow_credentials=False` عند `allowed_origins=["*"]`.
- `/health` يرجع 503 فقط عند `unhealthy` (D-012).

### مُضاف
- `tests/test_security.py` (8 اختبارات)؛ pytest 69/69.

## 2026-10-12 — M2: حسم الاستخدام والملكية (قرارات موثقة، لا حذف)

### مُضاف
- جرد 39 دليلاً غير حي في الجذر في ARCHITECTURE (R-010، D-014، PR #31).
- جرد `specialists/` 1,000 proxy + 21 `__init__.py` في ARCHITECTURE (R-011، D-015، PR #32).
- جدول مقارنة `agos-kernel/` مقابل `agos_kernel/` (R-012، D-016، PR #33).
- استكمال `src/lib/lovable-error-reporting.ts`, `src/lib/error-capture.ts`, `src/lib/error-page.ts` — ثلاث وحدات مفقودة كانت تكسر بناء الجذر (R-013، D-017، PR #34). `.gitignore` يستبعد `.output/`, `.wrangler/`, `src/routeTree.gen.ts`.
- `package-lock.json` للجذر ولـ`web/` (كانت مفقودة).

## 2026-10-12 — M3/1: اختبارات `/civilization/*` + إصلاح تراجع registry

### مُصلَح
- `agent_civilization/civilization_routes.py` — تراجع المسار (دالة داخل دالة بلا return) جعل الاستجابة `null`؛ دُمج المنطق في دالة واحدة (D-018).

### مُضاف
- `tests/test_civilization_routes.py` (6 اختبارات): registry, plan, execute, 400 عند رسالة فارغة، فرض المصادقة. pytest 75/75.

## 2026-10-12 — M3/2: مخطط Supabase SQL

### مُضاف
- `docs/sql/001_init.sql` — `tasks`, `agent_stats`, `events` مع فهارس القراءة (R-022، D-019، PR #36). الأنواع استنتاجية من بايثون ولم تُتحقق ضد قاعدة حية.

## 2026-10-12 — M3/3: بوابة الحوكمة CI (R-020)

### مُضاف
- `.github/workflows/governance-gate.yml` — يفشل PR إذا لم يُحدَّث ROADMAP وPROJECT_STATE وCHANGELOG في نفس الدفعة، وأدلة `docs/audit/` عند تغيير الكود، وAPI.md/DATA_MODEL.md/QUALITY.md حسب نوع التغيير (D-020).
- `docs/audit/M3/logs/gate_test_harness.sh` + سجلات حالة إيجابية وسلبية.

### ملاحظة
البوابة غير مفروضة دون حماية فروع (Q-015). لم يُغيَّر أي إعداد في المستودع.

## 2026-10-12 — M3/4: أمر التسليم واستكمال الخارطة (R-023)

### مُضاف
- `docs/governance/HANDOFF_PROMPT.md` — أمر واحد صالح لكل مراحل المشروع لأي وكيل أو مهندس جديد.
- ROADMAP: M3 (تتمة: R-023, R-024, R-031..R-033)، M4 (R-040..R-045)، M5 (R-050..R-056)، M6 (R-060..R-062)، وترتيب التنفيذ الموصى به. جميعها `proposed/ready/blocked` ولم يُنفَّذ منها شيء بعد.
- OPEN_QUESTIONS Q-016 (تعريف قابل للقياس لـ"الحضارة").

### مُحدَّث
- R-020 → done (PR #37 إيجابي، PR #38 سلبي).
- PROJECT_STATE: ملخص استلام سريع؛ قسم M0 "لا يعمل" وُسم تاريخياً.

### ملاحظة أمانة
قراءة نص خطأ البوابة السلبية (PR #38) تعذرت بسبب تجاوز حد معدل واجهة GitHub؛ المُتحقق فقط أن فحص `governance` فشل بينما quality/test/build نجحت.

## 2026-10-12 — M3/5: أول دفعة حذف في R-031

### مُحذف
13 دليلاً جذرياً صافي التوثيق (D-021): knowledge-base (120 ملفاً، 1.3M)، repository-analysis-report (25)، agos-canon (6)، agos-enterprise، agos-factory، agos-intelligence، aie-intelligence (5)، ari-research (5)، cgp-genome، sdl-loop، spec-formal (2)، sprint-000، execution-program، rpp-pipeline. وسم أمان `pre-R031-docs-batch1`.

### النتيجة
pytest 75/75؛ ruff (F,E722) صفر أخطاء؛ صفر مرجع متبقٍ في إعدادات التشغيل.

## 2026-10-12 — M3/6: إكمال R-031 (حذف 21 دليلاً متبقياً)

### مُحذف
21 دليلاً جذرياً (D-022): civilization, cloud, cognition, cognitive, domain, ecosystem, enterprise, evolution, execution, fabric, foundation, governance, intelligence, meta, observatory, orchestration, products, research, resource, rie, rie-sprint1. وسم أمان `pre-R031-docs-batch2`. بهذا اكتمل جرد R-010 (39 دليلاً).

### الفحص
AST (لا grep): صفر استيراد دقيق في المسارات الحية؛ صفر استيراد بين الدلائل؛ صفر مرجع في إعدادات التشغيل. التطابقات الجزئية كانت إنذارات كاذبة (اسم خدمة، وحدة stdlib `resource`، مسارات API حية).

### النتيجة
pytest 75/75؛ ruff صفر أخطاء؛ بناء الجذر و`web/` ناجح.

## 2026-10-12 — M3/7: تحديث حالة المشروع بعد R-031 + اكتشاف R-031b

### مُحدَّث
- PROJECT_STATE: تحديث شامل (نواة صغيرة نظيفة، كل القياسات الحالية، قائمة صادقة لما لا يعمل، مخاطر حالية، عوائق، الخطوة التالية).
- ROADMAP: R-031 -> done مع إحالة إلى R-031b؛ بند جديد R-031b (المجموعة الفائتة) حالة `ready`.
- ARCHITECTURE: قسم المجموعة الفائتة.

### اكتُشف
9 دلائل + 4 سكربتات جذرية فُوّتت في جرد M0 (AST: صفر استيراد حي). السبب المفترض: جرد M0 حصر الدلائل بحكم الاسم المرصود ولم يفحص سجل git للمجموعات المتأخرة (PRODUCT-000076..080 أُنشئت في commit 31bbcb5).

### النتيجة
لا تغيير كود. pytest وruff غير مطلوبين (تغيير توثيقي صافٍ) لكن الجرد أُعيد للمطابقة مع شجرة العمل الحالية.

## 2026-10-12 — M3/8: تصحيح أعداد R-031 (D-023)

### تصحيح (ليس عمل جيد — بل إفصاح عن ادعاء سابق غير صحيح)
- الدفعة الأولى (M3/5) حذفت **14** دليلاً لا 13 (فحص `git show a88402f` بالأسماء).
- المجموع **35 من 39** لا 39: بقي `consolidation/`, `constitution/`, `sdk/`, `seos/` — لم تُحذف، ولم يُزل صفها من الجرد. R-031 أُعيد إلى `in_progress`.
- `consolidation/`, `constitution/`, `sdk/`, `seos/` كانت ضمن جدول M0 الأصلي؛ ليست اكتشاف R-031b.
- R-031b صُحّح ليصف فقط الإضافات الحقيقية: 5 دلائل `cloud_*` (لا توجد في جدول M0) + 4 سكربتات جذرية معروفة سابقاً (Q-006/D-015) لكن بلا قرار موثق.

### دليل التحقق
`docs/audit/M3/logs/05_r031_correction.log` — الأوامر والنتائج كاملة بالأسماء (لا بالأرقام).

## 2026-10-12 — M3/9: إكمال R-031 (دفعة ثالثة: 4 دلائل)

### مُحذف
`consolidation/`, `constitution/`, `sdk/`, `seos/` (D-024) مع وسم أمان `pre-R031-docs-batch3`. بهذا اكتمل جرد R-010: 14 + 21 + 4 = 39.

### النتيجة
AST صفر استيراد حي؛ pytest 75/75؛ ruff صفر أخطاء؛ بناء الجذر و`web/` ناجح (دليل M3/logs/06).

## 2026-10-12 — M3/10: R-031b — حذف المجموعة الفائتة (cloud_* + سكربتات + agents.yaml)

### مُحذف (D-025)
- 5 دلائل: `cloud_collaboration/`, `cloud_execution/`, `cloud_experience/`, `cloud_session/`, `cloud_workspace/` (مجموعة PRODUCT-000076..080؛ وحدات مستقلة بلا استيرادات متبادلة).
- 4 سكربتات: `agent_manager.py`, `auto_fetch_agents.py`, `generate_real_agents.py`, `generate_agents_catalogue.py` (مولدات كتابة فقط؛ بعضها يحتاج مكتبات ليست في `requirements.txt` فلم يعمل في CI أو Docker قط).
- `agents.yaml` (4,001 سطراً، 1,000 مدخل مولَّد؛ مستهلكاه الوحيدان هما السكربتان المحذوفان).
- وسم أمان: `pre-R031b-cloud-scripts`.

### أُغلق
Q-003، Q-006، D-006 (كامل)، Q-004 (جزئياً — بقي R-032 لمصير ملفات `specialists/` نفسها).

### النتيجة
AST صفر استيراد حي؛ pytest 75/75؛ ruff صفر أخطاء؛ بناء الجذر و`web/` ناجح؛ صفر مرجع متبقٍ في الكود والإعدادات (دليل M3/logs/07).

## 2026-10-12 — M3/11: R-033 — أرشفة `agos-kernel/` ثم حذفه (D-026)

### مُحذف
- `agos-kernel/` (515 ملفاً، 469 py، 5.0MB) من `main`؛ مؤرشف في الفرع `archive/agos-kernel-v1` ووسم `pre-R033-agos-kernel-removal`.
- `push_to_github.sh` (سكربت تاريخي يشير إلى `agos-kernel/finalization` فقط).

### اكتُشف
`agos-kernel/platform/` يحجب وحدة `platform` من stdlib (مُعاد إنتاجه: `import platform` يفشل من داخل الدليل) — عيب بنيوي مسجل في RISKS (تحديث RISK-011).

### النتيجة
pytest 75/75؛ ruff صفر أخطاء؛ بناء الجذر و`web/` ناجح؛ صفر مرجع متبقٍ في الكود والإعدادات (دليل M3/logs/08).

## 2026-10-12 — M3/12: R-031c — حذف روابط رمزية معلقة وتقرير بلا مستهلك (D-027)

### مُحذف
- `execution_program`, `knowledge_base`, `rie_sprint1` (روابط رمزية معلقة، أنشئت في 22a78f4 لأهداف حُذفت في R-031/دفعة 1).
- `agos_system_registry_report.json` (تقرير تدقيق مولد بلا مستهلك).

### النتيجة
AST صفر استيراد حي؛ pytest 75/75 (دليل M3/logs/09).

## 2026-10-12 — M4/1: R-040 — التحقق من Docker داخل CI + `.dockerignore` (D-028)

### مُضاف
- job `docker` في `.github/workflows/ci.yml`: بناء الصورة من `Dockerfile` الفعلي، تشغيل الحاوية (18000→8000)، انتظار بدء محدود (30 ثانية)، تحقق من `/health` (HTTP 200 تحديداً + جسم JSON يحوي `status`)، جمع سجلات البناء والحاوية و`docker inspect` دائماً ورفعها كـ artifact `docker-verification-logs`.
- `.dockerignore`: يمنع دخول `.git` و`node_modules` و`web/dist` و`.output` والأسرار (`.env*`) في سياق `COPY . .`.

### صُحح (تضارب توثيقي، لا كود)
- PROJECT_STATE كان يصف R-031b وR-033 كمعلقتين بينما ROADMAP وDECISIONS توثقهما منجزتين (M3/10 وM3/11) — حُسم التضارب لصالح ROADMAP/DECISIONS ووُحدت الصياغات (أُغلق بند «المجموعة الفائتة» و«قرار agos-kernel/» من قوائم «ما زال مفتوحاً»).
- ترقيم بنود قائمة «ما لا يعمل» كان خارج التسلسل (5، 7، 6) — أُعيد ترتيبه.

### خط الأساس (مُتحقق محلياً قبل الدفعة، SHA 491fda0)
pytest 75/75 (exit 0)؛ ruff صفر أخطاء (exit 0)؛ بناء `web/` ناجح؛ بناء الجذر ناجح؛ CI أخضر على main (تشغيل 38281756588). فشل أولي لـ pytest في بيئة التحرير (17 فاشلاً) كان لغياب `pytest-asyncio` محلياً — فشل بيئة لا مستودع (دليل M4/logs/01 و04).

### التحقق عن بُعد (نُفّذ — تحديث في نفس الفرع قبل الدمج)
تشغيل 38285632672 على PR #48 (job `docker`، pass، 17 ثانية): بناء الصورة نجح (~177MB)، الحاوية عملت، `/health` رد 200 في المحاولة الثانية (حالة `degraded` متوقعة — تخزين الذاكرة في CI، راجع D-012)، و1000 وكيل نشط في الحضارة. الأدلة مُرتكزة من artifact `docker-verification-logs` في `docs/audit/M4/logs/05..09` (بناء/تشغيل/سجلات الحاوية/inspect/ps). بهذا تحقق معيار قبول R-040 بالكامل → الحالة `done`.

## 2026-10-12 — M4/2: تصحيح توثيقي لـ R-040 + ترقية R-041 إلى ready

### صُحح (لا كود — صدق الادعاءات)
- إشارات متبقية بعد دمج PR #48 كانت تصف R-040 كـ`in_progress` (سطر في OPERATIONS، سطر في QUALITY، عنوان في EVIDENCE) — حُدّثت كلها إلى `done` مع رابطي التشغيل الفعليين.
- أكواد الخروج في أدلة M4/1 المحلية (`01..03`) التُقطت بعد pipe؛ أُعيد قياسها بأخذ rc من الفحص نفسه (`logs/11..13` — كلها 0، لا تغيير في النتائج).
- قيم SHA فعلية أُضيفت للأدلة (من GitHub API): التشغيل 38285632672 على `f02ed61` والتشغيل 38285911497 على `13fc37d`.

### رُقّي
- R-041 → `ready` (كان `proposed`) مع توسيع نطاق موثق (D-029): `AGENTS_PER_SPEC` لا يُحل بغير كود فيُضاف `server.py` + اختبار توصيل (عملية مستقلة) + مدخلا المصادقة/CORS في `render.yaml` كـ`sync: false`.

### النتيجة
لا تغيير كود. الفحوص أُعيدت للتأكيد: pytest 75/75 (rc من الفحص مباشرة)، ruff صفر، بناء `web/` ناجح (دليل `logs/11..13`).

## 2026-10-12 — M4/3: R-041 — مطابقة إعدادات النشر مع الكود (D-029)

### الكود
- `server.py`: دالة `_agents_per_spec()` تقرأ `AGENTS_PER_SPEC` (افتراضي 50) مع تحقق صلاحية (رفض صفر/سالب/نص برسالة بدء واضحة) وتُمرر القيمة إلى `create_civilization`. المتغير كان ميتاً في الإعدادات قبلاً (render.yaml يضبط 3 دون أن يقرأه أحد — Q-013).
- `tests/test_agents_per_spec.py` (جديد): 3 اختبارات توصيل بعمليات Python مستقلة (بلا متغير → 1000 وكيل؛ بـ3 → 60؛ قيم غير صالحة → رفض برسالة).

### الإعدادات والتوثيق
- `render.yaml`: تعليقات توضيحية + مدخلا `AGOS_API_KEY`/`AGOS_ALLOWED_ORIGINS` كـ`sync: false` (لا سر في المستودع).
- `.env.example`: أُعيدت كتابته — إزالة تعريفات مكردة، توضيح `VITE_SUPABASE_*` كبديل خلفي (الواجهة لا تقرأها)، إضافة `VITE_API_BASE`، إضافة متغيرات `agos_kernel` المفقودة.
- `DEPLOY.md`: أُعيدت كتابته — بادئة `/api` صحيحة لكل المسارات (كانت بلا بادئة — Q-014)، الافتراضي 50 لا 5، `/docs` مُتحقق فعلياً (200)، جدول متغيرات كامل ومطابق للكود.
- API.md: صُححت أوصاف الحماية القديمة (كانت تصف ما قبل R-004)؛ SECURITY.md: جدول الحماية الفعلي بعد R-004 + مدخلا render.yaml؛ OPEN_QUESTIONS: Q-013 حُل جانبها التقني (بقي جانب السبب — يُقاس في R-053)؛ Q-014 حُل جانبها الخاص بـDEPLOY.md (جانب INTEGRITY-REPORT باقٍ لـR-051/R-056).

### النتيجة (أدلة M4/logs/14..18)
pytest 78/78 (rc من الفحص مباشرة)؛ ruff صفر أخطاء؛ بناء `web/` ناجح؛ برهان توصيل `AGENTS_PER_SPEC=3` → 60 وكيلاً؛ `/docs` يرد 200.

### تحديث مراجعة (قبل الدمج، نفس الفرع)
- عُربت تعليقات الكود والإعدادات (`server.py`، `tests/test_agents_per_spec.py`، `render.yaml`، `.env.example`).
- صُححت فقرات وثائقية باقية من M0: OPERATIONS (وصف `import server` والواجهة — كانت تصف ما قبل R-001/M2-4)، SECURITY (وصف CORS — صُحح لسلوك R-004: credentials معطلة عند `*`)، QUALITY (صياغة Docker المتبقية)، PROJECT_STATE (سطر R-051 مكرر)، ARCHITECTURE (سطر الوكلاء — حُدث لقراءة `AGENTS_PER_SPEC`).
- Q-014: توصيف أدق — جانب DEPLOY حُل (R-041)؛ جانب INTEGRITY-REPORT باقٍ لـR-051/R-056.
- إعادة الفحص بعد كل التعديلات: pytest 78/78 (rc مباشر)، ruff صفر، بناء web ناجح (دليل logs/14 محدث).
