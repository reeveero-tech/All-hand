# خريطة الملفات

- **الحالة:** `implemented` (وصف للكود الموجود على `main` بعد #399، `e4e4a43b`).
- **التاريخ:** 2026-11-25.
- **الأرشيف:** الأرشيف القديم (`archive/pre-reset-2026-11-24`، 2887 ملفًا) غير مشمول هنا. يُرجع إليه كمصدر أفكار فقط، مثل ملفات `docs/sql/v2/001–007`.

| المسار | المسؤولية | من يستدعيه | ماذا يستدعي | حالته | التغييرات المقترحة |
|---|---|---|---|---|---|
| `app/main.py` | تطبيق FastAPI: `/health`، `/api/roles`، `/api/runs` (إنشاء، قائمة، جلب، إلغاء، أحداث SSE)، `/api/memory`، تقديم `web/` | uvicorn (Dockerfile) | orchestrator، llm، store، memory | يعمل. الملكية عبر `X-Client-Key` منذ #399 | لاحقًا: تقسيم إلى routers |
| `app/orchestrator.py` | `Run` (حالة وأحداث في الذاكرة). التخطيط، وتعقيم الخطة إلى DAG، وتنفيذ المهام، والمدقق حتى 3 محاولات، والتجميع، والفحص النهائي. MAX_DEPTH=2، MAX_TASKS=10 | main | agents، llm، memory، store | يعمل. الحالة تضيع عند إعادة التشغيل | المرحلة 2: حالة دائمة، وخطوات قابلة للاستئناف، ونمط map |
| `app/agents.py` | 9 أدوار وبروتوكول JSON للأفعال (MAX_STEPS=6) | orchestrator | tools، llm | يعمل | سجل أدوات بمستوى خطورة |
| `app/llm.py` | عميل متوافق مع OpenAI. مزوّد مجاني (Pollinations) أو BYOK أو المنصة. حساب الاستهلاك. `extract_json` | orchestrator، agents، memory | httpx | يعمل. المجاني بطيء | إعادة محاولة مع backoff، وحساب التكلفة |
| `app/tools.py` | `web_search` (Tavily ← Brave ← DDG ← Wikipedia)، و`fetch_url` مع فحص المضيف، و`run_python` بحدود موارد | agents | httpx، subprocess | يعمل. **`run_python` عملية فرعية على الخادم نفسه بحدود rlimit فقط، ومفعّل فقط لمزوّد المنصة أو مع `ALLOW_CODE_EXEC=1`**، و`fetch_url` يثبّت العنوان ويفحص كل تحويل (#399) | المرحلة 3: تنفيذ معزول |
| `app/store.py` | حفظ المهام وجلبها من جدول `runs` في Supabase عبر PostgREST | orchestrator، main | httpx | يعمل. **مخطط الجدول غير موجود في المستودع** | `migrations/001_baseline.sql` (#401) |
| `app/memory.py` | ذاكرة بترتيب BM25 مع تطبيع عربي، وجدول `memories`، ومساحة عبر `X-Memory-Space` (تجزئة) | main، orchestrator | llm، httpx | يعمل. يحفظ أحيانًا معلومات تافهة | PR #398 يضيف المصدر. لاحقًا: ربطها بالمالك |
| `app/__init__.py` | حزمة | — | — | فارغ | — |
| `web/index.html` | الصفحة الوحيدة | المتصفح | app.js، style.css | يعمل | المرحلة 1: هيكل التطبيق وثنائية اللغة |
| `web/app.js` | الإعدادات في localStorage، الإنشاء، SSE، العرض، الذاكرة | index.html | API | يعمل | تقسيم إلى وحدات، وتوجيه بالمسارات |
| `web/style.css` | تنسيق RTL بسيط (50 سطرًا) | index.html | — | يعمل | نظام تصميم بمتغيرات CSS |
| `tests/test_core.py` | اختبارات التخطيط والتعقيم والأدوات و`extract_json` | pytest | app.* | ناجحة | — |
| `tests/test_memory.py` | اختبارات الترتيب والاسترجاع والتعقيم | pytest | app.memory | ناجحة | — |
| `tests/test_ownership.py` | العزل حسب المالك، وإعادة التوجيه في `fetch_url` | pytest | app.main | موجود منذ #399 | — |
| `docs/RESET_REPORT.md` | تقرير إعادة الضبط في 2026-11-24 | — | — | تاريخي | ملاحظة: التوجيه الجديد يسمح بمجلد docs موسّع |
| `docs/MEMORY_DESIGN.md` | تصميم الذاكرة ومخطط `memories` | — | — | مطابق للكود | ينقل المخطط إلى migrations |
| `docs/INTAKE_REPORT.md` وملفات `docs/product/*` و`docs/architecture/*` و`docs/roadmap/*` | وثائق المرحلة 0 | — | — | جديدة | — |
| `Dockerfile` | صورة Python وuvicorn | Render | requirements | يعمل | — |
| `.github/workflows/ci.yml` | pytest وبناء Docker | GitHub Actions | — | يعمل | إضافة ruff، واختبار واجهة لاحقًا |
| `requirements.txt` / `requirements-dev.txt` | الاعتماديات | Docker، CI | — | — | — |
| `README.md` | تعريف وتشغيل | — | — | — | قسم الوثائق |
| غير موجود: `render.yaml` | — | — | — | إعدادات Render ليست في المستودع | توثيقها في `docs/operations/DEPLOYMENT.md` |
| غير موجود: `migrations/` | — | — | — | غير موجود على `main`. PR #398 يضيف `002_memory_provenance.sql` | المرحلة 1 |
