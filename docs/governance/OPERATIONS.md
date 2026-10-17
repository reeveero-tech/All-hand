# التشغيل والنشر (OPERATIONS)

> **آخر تحديث**: 2026-10-16 — R-144 (SEC-04): حدود بث SSE `AGOS_SSE_*` ومراقبتها (القسم «حدود بث SSE»). قبلها 2026-10-15 — R-132: `AGOS_POOL_*`، `AGOS_WEB_WAIT_S`. قبلها R-131: `AGOS_LLM_KEYLESS_MAX_WAIT_S`. قبلها 2026-10-12 — بعد دفعة M4/1 (R-040)

## البناء والتشغيل المحلي

### تشغيل الاختبارات محلياً (R-091 — نفس أوامر CI job `test`)
```bash
python3.11 -m venv .venv && . .venv/bin/activate        # CI: 3.11 ثم 3.12 (D-043)
pip install --require-hashes -r requirements.lock         # R-148: الإنتاج مقفول بالتجزئات
pip install --require-hashes -r requirements-dev.lock    # R-148: أدوات الاختبار منفصلة ومقيدة بالإنتاج
pytest tests/ -q                                           # 230 + 3 متخطاة + 3 مستبعدة (network)
pytest -m network -q                                       # يحتاج إنترنت (CI job network)
```
- تبعيات الاختبار ليست في `requirements.txt` (تبعيات التشغيل فقط) — منذ R-148 هي في `requirements-dev.in/lock` منفصلة ومقيدة بإصدارات الإنتاج نفسها. تثبيت `httpx` بدل `httpx2` يعطي `StarletteDeprecationWarning` (logs/82).
- قبل الدفع: جرّب أيضاً بلا `web/dist` (job `test` لا يبني الواجهة) وعلى 3.11 (درسا R-083 في QUALITY).

### الواجهة (`web/`)
- اختبارات عميل API (R-101): `cd web && npm test` (Node ≥22.13، بلا تبعيات إضافية).
```bash
cd web && npm ci --engine-strict && npm run build   # Node >=22.13.0 (R-089/R-148)
```
- مُتحقق: نجاح في 902ms ([log 08](../audit/M0/logs/08_web_build.log)). منذ R-089: `engines.node` في `web/package.json` — Node 20 (منتهي الدعم) يُرفض بـ`EBADENGINE` (logs/74). منذ R-148: الحد المُعلن صُحّح إلى `>=22.13.0` — القياس الفعلي (`npm ci --engine-strict` على 22.12.0 يفشل بـ`notsup` من jsdom 29) أثبت أن الحد الفعلي ^22.13.0 وليس 22.12.0.

### الخادم
```bash
pip install --require-hashes -r requirements.lock    # R-148: التثبيت من القفل المقفل بالتجزئات
python -c "import server"   # ينجح (R-001 أصلح استيراد utc)
uvicorn server:app --host 0.0.0.0 --port 8000
# AGENTS_PER_SPEC=3 uvicorn server:app   # 60 وكيلاً بدل 1000 (R-041)
```

### واجهة الجذر (TanStack)
```bash
npm ci --engine-strict && npm run build   # R-148: تثبيت نظيف من ملف القفل (كان npm install)
```

### تجديد قفل Python (R-148)
- **التعريف مقابل القفل**: `requirements.txt` هو تعريف التبعيات المباشرة (يُحرَّر يدويًا). `requirements.lock` هو المُثبَّت القابل للإنتاج: الإصدارات المحلولة لكل التبعيات المباشرة والانتقالية مع تجزئات SHA-256 (`--require-hashes` يفرضها). `requirements-dev.in` هو تعريف أدوات الاختبار و`requirements-dev.lock` قفلها **مقيد بإصدارات الإنتاج**؛ و`requirements-audit.in/lock` لأداة فحص الثغرات (بيئة مستقلة).
- **التوليد**: أداة التوليد مثبتة الإصدار: `pip-tools==7.6.1` على Python 3.11. **النطاق المثبت**: منصات Linux المستهدفة على Python 3.11 و3.12 فقط (ما أثبتته الفحوص — بيئات 3.11.15 و3.12.13 محليًا و3.11.17 و3.12.15 في CI، كلها Linux x86_64). توليد أولي (يحترم القفل الموجود عند وجوده):
```bash
pip-compile --generate-hashes --no-strip-extras --output-file=requirements.lock requirements.txt
pip-compile --generate-hashes --no-strip-extras --constraint=requirements.lock --output-file=requirements-dev.lock requirements-dev.in
pip-compile --generate-hashes --no-strip-extras --allow-unsafe --output-file=requirements-audit.lock requirements-audit.in
```
(قفل التدقيق بـ`--allow-unsafe` لأن `pip-audit` تعتمد على `pip` نفسها وpip-compile يستبعدها افتراضيًا باعتبارها unsafe — بدونها يبقى القفل ناقصًا ويعتمد على pip الموجودة مسبقًا.)
- **التحديث المقصود** (رفع الإصدارات داخل النطاقات المسموحة — لا يعتمد على وصول عشوائي لحظة البناء):
```bash
pip-compile --generate-hashes --no-strip-extras --upgrade --output-file=requirements.lock requirements.txt
# أو رفع حزمة محددة فقط: --upgrade-package aiohttp (ويكرر للحزم المطلوبة)
pip-compile --generate-hashes --no-strip-extras --constraint=requirements.lock --output-file=requirements-dev.lock requirements-dev.in
```
- **القاعدة**: جدّد `requirements.lock` أولًا ثم `requirements-dev.lock` (القيد `-c` يضمن ألا تعيد أدوات الاختبار حل إصدارات الإنتاج). لا يُعدَّل أي قفل يدويًا. **ملاحظة ترويسة القفل**: قد تظهر `--no-index` في سطر «by the following command» رغم أنها لم تُمرَّر — **أثر عرض مثبت بالتشغيل في pip-tools 7.6.1**: تشغيل توليد معزول بنسخة معدّاة من الأداة أثبت أن `no_index=False` فعليًا وأن الوسائط الممررة للمحلل لا تحوي `--no-index` إطلاقًا (الفهرس الافتراضي PyPI هو المستخدم) بينما تعرضه الترويسة (السجل `268`). الوصفة أعلاه تعمل في بيئة نظيفة كما هي.
- **الاستهلاك**: CI (test/storage/network) وDocker وRender وأوامر التشغيل المحلي كلها تثبّت من الأقفال بـ`--require-hashes`؛ ترقية pip نفسها في CI مثبتة (`pip==26.2.1`)، وأداة الفحص `pip-audit` من قفلها — لا يوجد أي مسار يثبّت بحل عائم.

## النشر

### Docker (من `Dockerfile`)
- مراحل: `node:22-slim` لبناء `web/` (منذ R-089، D-042؛ كان `node:20-slim`) بـ`npm ci --engine-strict` بلا بديل `npm install` صامت، ثم `python:3.12-slim` للتشغيل (Python 3.12 مختبر في CI منذ R-090)؛ يُخدم `web/dist` عبر `server.py`. منذ R-071: `web/` يستخدم vite 8 (يدعم `^20.19.0 || >=22.12.0`)؛ المشروع يثبّت `>=22.12.0`. **تطبيق الجذر (`src/`، TanStack Start) يتطلب Node `>=22.12.0`** (R-076 — معلن في `package.json` ومفروض في CI بـ`npm ci --engine-strict`)؛ ليس جزءاً من صورة Docker.
- **مستخدم غير root** (R-100، D-044): `USER agos` (uid 10001) — حدود عزل `test_runner` الصلبة لا يرفعها الابن، والتطبيق لا يكتب إلى `/app` وقت التشغيل (فُحص: لا كتابة ملفات في المسار الحي). CI job `docker` يتحقق من `id -u != 0` ومن رفض تنفيذ الكود في الوضع المفتوح على الحاوية المبنية، ومنذ R-139 من أن الحاوية بلا مفاتيح لا تقلع وأن `AGOS_ENV=development` مع `RENDER=true` مرفوض.
- **`.dockerignore`** (جديد في R-040): يمنع دخول `.git` و`node_modules` و`web/dist` والأسرار (`.env*`) في سياق `COPY . .` — كانت تغيب قبله فكان سياق البناء يشمل كل ملفات العمل.
- **التحقق (R-040، ✓ done — دفعة M4/1)**: job `docker` في CI تبني الصورة وتشغلها وتتحقق من `/health` (HTTP 200 + جسم JSON يحوي `status`)، مع جمع السجلات ورفعها كـ artifact. تحققت في تشغيلين: الأول (run 38285632672) على رأس `f02ed61` والثاني/النهائي (run 38285911497) على رأس `13fc37d` — كلاهما نجح. الأدلة مُرتكزة في `docs/audit/M4/logs/05..09`. بيئة التحرير المحلية بلا Docker (دليل `docs/audit/M4/logs/04`).

### Render (من `render.yaml` — محدث R-041)
- `PYTHON_VERSION=3.12.0` (يستهلكه Render لا كود التطبيق — Q-007)، `startCommand=uvicorn server:app`، `healthCheckPath=/api/system/status`.
- `AGOS_AGENT_IMPL` (R-032، D-037): `real` (افتراضي) أو `specialists` — الثاني يبني الوكلاء من أصناف `specialists/`. مقاس عند 1000 وكيل: بناء 0.152s مقابل 0.002s، ذاكرة قصوى 66.7MB مقابل 39.1MB، 1000/1000 نجاح في الحالتين (`docs/audit/M7/logs/08`). التراجع: إزالة المتغير وإعادة التشغيل. قيمة غير صالحة توقف البدء.
- `AGENTS_PER_SPEC=3`: **يُقرأ من الكود منذ R-041** (كان متغيراً ميتاً في الإعدادات). ملاءمة القيمة 3 للموارد لم تُقس — Q-013.
- مدخلا `AGOS_API_KEY` و`AGOS_ALLOWED_ORIGINS` أُضيفا كـ`sync: false` (المشغّل يدخل القيم في لوحة Render؛ لا سر في المستودع).
- `VITE_SUPABASE_*` بدائل خلفية يقرأها `supabase_store.py`؛ الواجهة `web/` لا تقرأها.

### CI (`ci.yml`)
- الوظائف (تحقق R-086 من `ci.yml`؛ كلها على `ubuntu-26.04` — D-041، Actions v7 — R-087): quality (ruff F/E722) + test (pytest الافتراضي، بلا إنترنت — D-040؛ على Python 3.11 ثم 3.12 — D-043) + build (web/ على Node 22 بـ`npm ci --engine-strict` (R-089)؛ الجذر على Node 22: `npm ci --engine-strict` + eslint `--max-warnings=0` + build — R-070/R-076) + docker (R-040) + audit (pip-audit + npm audit لـweb/ والجذر، إنتاج وكامل، مانع — R-045/R-071/R-077) + storage (PostgreSQL + PostgREST حي — R-042) + network (`pytest -m network` على الإنترنت الحقيقي، غير إلزامي — D-040) + publish (معطل إلا بـ`[publish]` — D-011)؛ وسير عمل مستقل `governance-gate.yml` (R-020). الفحوص الإلزامية لحماية `main` (قراءة API): quality، test، build، docker، governance، audit — `storage` خارجها (Q-020) و`network` خارجها عمداً (D-040).
- `publish` يرفع إلى PyPI عند push لـ main مشروطاً بنجاح الوظائف السابقة ووسم `[publish]` في رسالة الدمج (D-011) — راجع RISK-009.

## متغيرات البيئة (المرصودة في الكود)

| المتغير | المكان | الافتراضي |
|---|---|---|
| `AGOS_ENV` (R-139، SEC-03، D-057) | core/tenancy.py | غير مضبوط = `production` (يرفض الإقلاع بلا بيانات اعتماد). القيم: `production`، `development`، `test` فقط؛ غيرها خطأ إقلاع. غير الإنتاج مرفوض مع `RENDER=true` |
| `AGOS_API_KEY` | server.py | `""` — منذ R-138 مفتاح المشغّل: principal `operator`/`admin`؛ منذ R-139 في الإنتاج ≥16 حرفاً وبلا مسافات طرفية |
| `AGOS_TENANT_KEYS` (R-138، SEC-02، D-056) | core/tenancy.py | غير مضبوط. قائمة JSON `[{"tenant","key_sha256","role"}]` (تجزئة SHA-256 للمفتاح، الدور `user` افتراضياً أو `admin`). أي خلل → الخادم لا يقلع (`CredentialsError`). بلا هذا ولا `AGOS_API_KEY`: كل مسارات بيانات المستأجرين 401 |
| `AGOS_ALLOWED_ORIGINS` | server.py | `"*"` |
| `AGOS_RATE_LIMIT_REQUESTS` | server.py | 100 |
| `AGOS_RATE_LIMIT_WINDOW` | server.py | 60 |
| `AGOS_SSE_QUEUE_SIZE` (R-144) | core/sse.py | 100 أحداث لكل اتصال SSE [1–10000] |
| `AGOS_SSE_MAX_CONNECTIONS` (R-144) | core/sse.py | 100 اتصال SSE متزامن للعملية [1–10000] |
| `AGOS_SSE_MAX_PER_TENANT` (R-144) | core/sse.py | 10 [1–10000، ≤ الإجمالي] |
| `AGOS_SSE_MAX_PER_CLIENT` (R-144) | core/sse.py | 5 لكل `request.client.host` [1–10000، ≤ الإجمالي] |
| `AGOS_SSE_OPEN_RATE` / `AGOS_SSE_OPEN_WINDOW` (R-144) | core/sse.py | 10 فتحات خلال 60 ث لكل عميل [1–10000] / [1–3600] |
| `AGOS_SSE_MAX_LIFETIME` (R-144) | core/sse.py | 3600 ث [10–86400] |
| `AGOS_SSE_HEARTBEAT` (R-144) | core/sse.py | 30 ث [1–300] (كما كان ثابتاً) |
| `AGOS_ALLOW_CODE_EXEC` (R-100، D-044) | core/safety.py | غير مضبوط = تنفيذ `test_runner` فقط إن ضُبط `AGOS_API_KEY`؛ `1` يسمح؛ `0` يرفض دائماً |
| `AGOS_MEMORY` (R-103، D-046) | core/experience.py | غير مضبوط/`1` = الذاكرة والتعلم مفعّلان؛ `0` يعطّلهما (لا حقل `experience`) |
| `AGOS_MEMORY_MAX` (R-103) | core/experience.py | 2000 مدخلاً (حد أدنى 10)؛ الأقل أهمية/الأقدم يُحذف |
| `AGOS_MEMORY_FILE` (R-103) | core/experience.py | غير مضبوط = الذاكرة في العملية فقط (تضيع عند إعادة التشغيل)؛ مسار ملف = JSON بكتابة ذرية كل 25 تخزيناً وعند الإيقاف، والتعلم يُعاد بناؤه منه عند أول مهمة. على Render القرص مؤقت ما لم يُربط قرص دائم |
| `AGOS_ALLOW_PRIVATE_NETWORK` (R-100) | core/safety.py | غير مضبوط = يُحظر كل عنوان غير عام في الأدوات والوكلاء المسجلين عبر API |
| `SUPABASE_URL` / `VITE_SUPABASE_URL` | supabase_store.py | `""` |
| `SUPABASE_SERVICE_KEY` (مفضّل — D-039) ثم `SUPABASE_ANON_KEY` / `VITE_SUPABASE_ANON_KEY` (بديل للتوافق) | supabase_store.py | `""` |
| `OPENROUTER_API_KEY` | core/llm.py | `""` |
| `LLM_MODEL` | core/llm.py | `meta-llama/llama-3.3-70b-instruct:free` |
| `AGOS_LLM_KEYLESS` (R-116، D-051؛ **SEC-06/R-145، D-059**) | core/llm.py | **معطّل افتراضياً**: غير مضبوط/`0`/`false`/`no` = لا مزود عام (مع غياب `OPENROUTER_API_KEY` → `provider=none` وفشل صريح للمهام التي تحتاج LLM). `1`/`true` فقط يفعّله، وبعدها **كل طلب يحتاج موافقته الصريحة** (`allow_public_llm=true` — البوابة الثانية في `LLMClient.complete`)؛ غير ذلك من القيم **خطأ إقلاع** (`KeylessConfigError` — لا تفسير ضمنياً كتفعيل). الاختبارات المحلية تعطله افتراضياً (`tests/conftest.py`) |
| `AGOS_LLM_KEYLESS_SPACING_S` / `AGOS_LLM_KEYLESS_BUDGET_S` (R-116) | core/llm.py | 6 ث بين الاستدعاءات / 90 ث ميزانية للاستدعاء (تبدأ عند دوره منذ R-131) مع إعادات 402/429/503 |
| `AGOS_LLM_KEYLESS_MAX_WAIT_S` (R-131، D-054) | core/llm.py | 30 ث أقصى انتظار للدور؛ إن كان المقدَّر أكبر يُرفض الطلب فوراً بـ«keyless LLM busy» (الاحتياطي يعمل فوراً) |
| `AGOS_POOL_LLM` / `AGOS_POOL_WEB` / `AGOS_POOL_NET` / `AGOS_POOL_LOCAL` (R-132، D-055) | core/pools.py | 2 / 4 / 8 / 4 خيوط (1..64) لكل نوع استدعاء حاجب |
| `AGOS_POOL_<NAME>_QUEUE` (R-132) | core/pools.py | 0 = بلا سقف (افتراضي)؛ قيمة > 0 ترفض فوراً بـ«<name> pool busy» حين يمتلئ الطابور |
| `AGOS_WEB_WAIT_S` (R-132) | core/agents/real_dispatcher.py | 20 ث أقصى انتظار (طابور + نداء) لاحتياطي ويب educator وترجمة communicator |
| `AGOS_LLM_KEYLESS_URL` / `AGOS_LLM_KEYLESS_MODEL` (R-116) | core/llm.py | `https://text.pollinations.ai/openai` / `openai` |
| `AGOS_AGENT_IMPL` | agents/real/real_agents.py | غير مضبوط = `real` (الافتراضي)؛ `specialists` يبني الحضارة من أصناف `specialists/` (D-037). (R-104: `process_text()` — دالة وحدة لا صنف — مُفوَّضة إليها من `_data_processor` لمدخل نصي فقط؛ بلا `OPENROUTER_API_KEY` يُنشر فشل LLM صراحةً) |
| `EXTERNAL_AGENTS` | agents/external_adapter.py | `""` — بلا وكيل خارجي (لا هنا ولا عبر `POST /api/agents/external`) ترد مهام `external` بـ409 فوراً (R-140، BUG-01) |
| `REDIS_URL` | agos_kernel/infrastructure/redis_queue.py | `redis://localhost:6379` |
| `METRICS_NAMESPACE` | agos_kernel/infrastructure/metrics.py | `agos` |
| `CIRCUIT_BREAKER_*` (4 متغيرات) | agos_kernel/infrastructure | 5/3/60/3 |

**محدث R-041**: كل متغيرات `.env.example` موثقة ومقروءة في الكود (بما فيها `CIRCUIT_BREAKER_*` و`AGENTS_PER_SPEC` و`REDIS_URL` و`METRICS_NAMESPACE` و`VITE_API_BASE`).

## سياسة الأدوات الحقيقية (R-100، D-044)

- **R-139 (SEC-03) — الإنتاج لا يقلع بلا بيانات اعتماد (D-057)**: `AGOS_ENV` غير مضبوط أو `production` = إنتاج (render.yaml وDockerfile يضبطانه صراحةً). في الإنتاج يفشل استيراد `server` (فتخرج uvicorn برمز 1 ويفشل فحص الصحة في Render فيبقى الإصدار السابق) إن: غاب `AGOS_TENANT_KEYS` و`AGOS_API_KEY` معاً؛ أو كان أيهما معطوباً (JSON، دور، تجزئة، تكرار، حقل مجهول)؛ أو كان `AGOS_API_KEY` أقصر من 16 حرفاً أو فارغاً بمسافات أو بمسافة/سطر جديد في طرفيه؛ أو كانت قيمة `AGOS_ENV` غير معروفة (`prod`، `Production`، `staging` → خطأ صريح، لا تخمين). اسم متغير مكتوب خطأ (مثل `AGOS_TENANT_KEY`) يُتجاهل فيبقى الإنتاج مغلقاً ولا يقلع. **التطوير المحلي بلا مفاتيح**: `AGOS_ENV=development uvicorn server:app --reload` صراحةً (بيانات المستأجرين تبقى 401 حتى تُضبط مفاتيح)؛ `AGOS_ENV=test` تضبطه `tests/conftest.py`؛ سكربتات المسابر في `scripts/` التي تشغّل الخادم تحتاج أحدهما أو مفاتيح. كلاهما **مرفوض على Render** (`RENDER=true` تضبطه المنصة) — النشر إنتاج دائماً. الرسائل والسجلات لا تحوي مفتاحاً ولا تجزئة (`tests/test_sec03_production_startup.py`). عند الإقلاع يُسجَّل `startup: AGOS_ENV=…, N credential(s) for M tenant(s)`.
- **R-138 (SEC-02) — قبل نشره على بيئة قائمة**: (1) طبّق `docs/sql/002_tenant_isolation.sql` على قاعدة Supabase أولاً (وإلا تفشل كتابات المهام والأحداث — الخادم يستمر بالذاكرة). (2) هيّئ المفاتيح: `AGOS_TENANT_KEYS` لكل مستأجر (`python3 -c "import hashlib,sys;print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" '<key>'` ينتج `key_sha256`) و/أو `AGOS_API_KEY` للمشغّل؛ بلا أي منهما تعرض الواجهة «المصادقة غير مهيأة» ولا تُنشأ مهام. (3) المهام والأحداث المخزنة قبل الترحيل `tenant_id = NULL` تبقى معزولة — يراها مفتاح admin عبر `?all_tenants=true` فقط. (4) الواجهة تقرأ تدفق الأحداث بـfetch مع المفتاح؛ مفتاح user لا يسجّل وكلاء خارجيين (403).
- **قرار المشغّل عند النشر**: لتفعيل `test_runner` اضبط `AGOS_API_KEY` (موصى به — يحمي كل المسارات المنشئة للمهام) أو `AGOS_ALLOW_CODE_EXEC=1` (يسمح بتنفيذ الكود لكل مستأجر مصادَق حتى بلا `AGOS_API_KEY` — غير موصى به؛ منذ R-138 إنشاء المهام يتطلب مفتاحاً صالحاً دائماً، ومنذ R-139 لا يقلع الإنتاج بلا بيانات اعتماد، فـ«الوضع المفتوح» هنا يعني فقط غياب `AGOS_API_KEY` لا غياب المصادقة). بلا أي منهما تُرفض مهام `test_runner` برسالة صريحة وتبقى التخصصات الـ19 الأخرى تعمل.
- **الواجهة مع المفتاح (R-101، مصحَّح في R-139 وفق R-138)**: يظهر في الواجهة شريط «مفتاح API» متى هُيئت مفاتيح (أو بعد أي رد 401/403)؛ يدخل المستخدم مفتاحه فيُحفظ في sessionStorage للتبويب فقط ويُرسل كـ`X-API-Key`. **كل بيانات المستأجر تتطلب المفتاح منذ R-138**: قائمة المهام وتفاصيلها، الأحداث، الذاكرة والتعلّم، وتدفق SSE (تقرؤه الواجهة بـfetch مع المفتاح — `openEventStream`)، والكتابة (مهام، محادثة، `/civilization/execute`)؛ تسجيل الوكلاء الخارجيين لمفتاح admin. بلا مفتاح تبقى فقط اللوحة العامة (`/api/system/status`، `/api/agents`، `/api/specializations`) — لم تعد «القراءة تعمل بلا مفتاح» صحيحة. **لا تضع المفتاح في `VITE_*`** — يصير جزءاً من الحزمة العامة.
- **التحقق على أي commit**: `python scripts/run_r100_safety_probe.py` (rc=0 فقط إذا كانت كل المتجهات آمنة) و`GET /api/system/status` → `security.tool_policy`.
- **قياس معيار D-035 الأول**: `AGOS_ALLOW_CODE_EXEC=1 python scripts/run_r051_audit.py` (20/20)؛ وR-053: `AGOS_ALLOW_CODE_EXEC=1 python scripts/run_r053_scale_audit.py` (1000/1000). بالوضع الافتراضي تظهر مهام `test_runner` كمرفوضة بالسياسة (R-051: `refused_by_policy`؛ R-053: `فشل: test_runner ×50 — code execution is disabled…`).

## الفحوصات

`/health`, `/health/live`, `/health/ready`, `/metrics`, `/api/system/status`. **`/health/ready` يعيد 503 عند أي حالة `not_ready`** (حضارة غير مهيأة، صفر وكلاء، استثناء أثناء الفحص — يُسجَّل `readiness check failed: <نوع الاستثناء>`) و200 فقط عند `ready` (R-142، BUG-03؛ كان 200 دائماً)؛ `/health/live` 200 ما دامت العملية حية (لا يتبع الجاهزية). فحص صحة Render يبقى `/api/system/status` (`render.yaml`) وفحص CI docker يبقى `/health` — لم يتغير أيهما. `/health` يعيد 503 عند `unhealthy` و200 عند `healthy`/`degraded` (أُصلح في R-004؛ اختباران منذ R-068 — صُحح هنا في R-078، كان يقول "200 دائماً"). **R-078**: فحص `storage` يصير `degraded` إذا كان مفتاح Supabase عاماً، و`/api/system/status` يعرض `supabase_key: {kind, source}` (لا القيمة).

## الفجوات

1. لا `docker-compose.yml` ولا `Makefile`.
2. `DEPLOY.md` لم يُدقق في M0 — يحتاج مراجعة وتحديثاً.
3. لا مراقبة خارج المقاييس المدمجة.

## استجابة الحوادث (Incident Response) — R-044

### النشر والتراجع (Documented — 2026-10-12)

**النشر (Deploy)**:
1. كل تغيير يمر عبر PR → CI (quality/test/build/docker/governance) → merge إلى main.
2. البنية التحتية النشرية: Render (`render.yaml`) — `startCommand=uvicorn server:app`.
3. للتحقق من صحة النشر: `curl $BASE_URL/health` (200 + `status`) و`curl $BASE_URL/api/system/status`.

**التراجع (Rollback)**:
- Render: من لوحة التحكم → service → **Rollback** (يعيد آخر إصدار ناجح — تُخزن الإصدارات في Render).
- البديل اليدوي (بديل عام — مطلوب صلاحية الكتابة إلى المستودع): 
  1. `git revert <commit>` (لا `reset --hard` — محفوظة كأمر تشغيلي في `docs/governance/DECISIONS.md` D-034).
  2. PR العكسي → CI خضراء → merge → إعادة نشر تلقائية.
- Docker: تبديل الوسم في السجل (image tag) — لا يوجد سجل صور خارجي في CI الحالي (Q-013).

### حالات الفشل الموثقة (Failure Modes)

| الحالة | العَرَض | التشخيص | الاستجابة |
|---|---|---|---|
| `/health` يعيد 503 (unhealthy) | خادم لا يبدأ أو وحدة نسيّة | `docker logs` / `uvicorn` stderr | تراجع فوري — لا تعديل تشغيلي حي |
| `/health` يعيد 200 مع `storage.message` = "public key … rejected by RLS" (R-078) | مفتاح Supabase عام (`sb_publishable_` أو JWT `role=anon` أو `SUPABASE_ANON_KEY`) — كل كتابة تُرفض والخادم يعمل من الذاكرة | `curl $BASE_URL/api/system/status` → `supabase_key.kind`؛ تحذير `PUBLIC key` في سجل البدء | ضع المفتاح السري في `SUPABASE_SERVICE_KEY` وأعد التشغيل ثم `run_r042_storage_verify.py` (22/22) |
| `/health` يعيد 200 مع `storage.message` = "last storage operation failed: …" (R-082) | Supabase ساقطة أو المفتاح أُبطل أو 5xx بعد البدء — البيانات تذهب إلى الذاكرة | `storage.details.last_error` (العملية + `HTTP <code>`/`exception: <Type>` + الوقت)؛ `agos_storage_operations{outcome="failed"}` في `/metrics` | أصلح السبب؛ الحالة تعود `healthy` تلقائياً عند أول عملية ناجحة |
| `/health` يعيد 200 مع degraded | الذاكرة البديلة (Supabase غير مهيأ) | `SUPABASE_URL`/`SUPABASE_SERVICE_KEY` | مقبول مؤقتاً — Q-013؛ إن كانا مضبوطين فابحث في السجل عن `rejected: HTTP` (RISK-013/018) |
| فشل CI في `quality` | أخطاء ruff (استيراد غير مستخدم/استثناء عريض) | `ruff check --select F,E722 <files>` | أصلح في نفس PR — لا force-push (D-034) |
| فشل CI في `test` | فشل اختبار (انظر `docs/audit/M6/logs/11_r055_pytest.log` لنموذج) | `pytest tests/ -q` | أصلح في نفس PR |
| فشل CI في `governance` | دفعة ناقصة ROADMAP/PROJECT_STATE/CHANGELOG/EVIDENCE | راجع `GOVERNANCE_GATE.md` | أضف الناقص في نفس PR قبل الدمج |
| 429 من api.github.com في اختبار api_integrator | تسريب اختبار خارجي | `grep api.github.com tests/` | بُدل إلى `https://example.com` (R-055) |
| اختبار e2e فشل بـ401 | تسريب حالة (اختبار سابق حمّل `server` بوضع auth-on) | راجع `tests/test_e2e_scenarios.py::test_scenario_full_pipeline_stores_result_in_task_store` | fixture يعيد استيراد `server` مع حذف `AGOS_API_KEY` (R-055) |
| القياسات تتوقف | 1000 وكيل/1000 مهمة غير مستقرين | `AGOS_ALLOW_CODE_EXEC=1 python3 scripts/run_r053_scale_audit.py` (يسمّي التخصص الفاشل وخطأه منذ R-100) | راجع R-053 |
| `POST /api/tasks` بـ`external` يرد 409 «no external agent is registered…» | لا وكيل خارجي مسجّل (R-140، BUG-01) | `GET /api/system/status` → `external_agents` | سجّل وكيلاً بمفتاح admin (`POST /api/agents/external`) أو `EXTERNAL_AGENTS` عند الإقلاع؛ لا مهمة ولا أثر نتجا عن الرفض |
| `test_runner` يعيد «code execution is disabled in open mode» | سياسة R-100 الافتراضية (لا `AGOS_API_KEY`) | `GET /api/system/status` → `security.tool_policy.code_execution` | اضبط `AGOS_API_KEY` (أو `AGOS_ALLOW_CODE_EXEC=1` بوعي) — D-044 |
| أداة شبكية تعيد `blocked: blocked non-public address` | الهدف داخلي/خاص (R-100) | العنوان المحلول في الرسالة | مقصود؛ `AGOS_ALLOW_PRIVATE_NETWORK=1` لبيئة داخلية موثوقة |

### حدود المسؤولية (Owner's Line)
- حماية الفرع `main` (R-024) — مطلوب إعداد المالك (Q-015).
- مشروع Supabase دائم للإنتاج + إعداد Render — قرار المالك وموارده. التخزين مُتحقق على محرك Supabase (R-042) وعلى مشروع مستضاف تجريبي (R-042b).

## تحقق التخزين الحي (R-042)

- **CI**: job `storage` (مانع) — حاوية `postgres:17` + PostgREST v12.2.3 (مثبت بـsha256) + `scripts/r042/run_local_stack.sh`؛ السجل يُرفع artifact `r042-storage-verification-log`.
- **محلياً**: PostgreSQL + ملف PostgREST، ثم:
  `ADMIN_URI=postgresql://postgres@HOST/agos AUTH_URI=postgresql://authenticator:authenticator@HOST/agos POSTGREST_BIN=/path/postgrest bash scripts/r042/run_local_stack.sh`
- **ضد Supabase الحقيقية** (R-042b ✓ M7/7): طبّق `docs/sql/001_init.sql` في محرر SQL للمشروع (لا تطبّق `scripts/r042/*.sql` هناك — الأدوار موجودة)، ثم `SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python scripts/run_r042_storage_verify.py` (0 = 22/22).
- **متغير المفتاح في الإنتاج** (D-039): `SUPABASE_SERVICE_KEY` = المفتاح السري (`sb_secret_…`) أو `service_role`. إن وُضع المفتاح العام بدلاً منه: كل كتابة تُرفض وتظهر في السجل `insert_task rejected: HTTP 401 … 42501 row-level security` والخادم يعمل من الذاكرة.
- **عَرَض فشل الكتابة**: سطر تحذير `storage` بصيغة `<op> rejected: HTTP <code> <body>` (مثلاً 401 مفتاح خاطئ، 404 جدول غير موجود = المخطط لم يُطبق، 42501 = صلاحيات/RLS).

## مقاييس HTTP (R-083)
- معدل الطلبات والأخطاء: `agos_http_requests_total` بحسب `endpoint` (قالب المسار — عدد السلاسل محدود بعدد المسارات × الطرق × الرموز).
- الكمون: `agos_http_request_duration_seconds_bucket` (الحدود 0.005…10 ثانية). الذاكرة محدودة: تجميعات لكل سلسلة + نافذة خام 1024 قيمة.
- الكلفة المقاسة ~18µs/طلب (TestClient، logs/63).

## حدود بث SSE (R-144، SEC-04)

- **الضبط**: متغيرات `AGOS_SSE_*` (الجدول أعلاه) تُقرأ عند الإقلاع (`SSELimits.from_env`). أي قيمة غير صحيحة العدد أو صفرية أو سالبة أو خارج المدى، أو حد مستأجر/عميل أكبر من الإجمالي → **الخادم لا يقلع** برسالة تسمّي المتغير (لا تجاهل صامت ولا تعطيل للضابط). القيم الافتراضية ابتدائية، **لم تُقَس بحمل حي**؛ تُراجع حسب عدد المستأجرين والعملاء الفعلي.
- **المراقبة** (`/metrics`): `agos_sse_connections` (مقياس: الاتصالات المفتوحة الآن)، `agos_sse_connections_opened_total`، `agos_sse_refused_total` (رفض 429 لمعدل الفتح أو السعة)، `agos_sse_events_dropped_total` (أحداث أُسقطت لمستمعين بطيئين). السجل: `sse: connection refused (<reason>)` و`sse: slow listener, dropped …` (لا مفاتيح ولا معرّفات مستأجرين).
- **سلوك الازدحام**: لا ينتظر الناشر (تنفيذ المهام) أي مستمع؛ الطابور الممتلئ يُسقط أقدم حدث ويُبلغ المستمع بـ`stream_gap`. بلا مستمعين لا تُحفظ أحداث في الذاكرة — `/api/events` (Supabase) هو السجل الدائم.
- **حدود معروفة**: الحدود **لكل عملية** (لا تنسيق بين نسخ متعددة)؛ العميل = `request.client.host` — خلف وكيل عكسي قد يتشارك العملاء عنواناً واحداً (حد العميل يصير حداً للوكيل) — لا يُوثق بـ`X-Forwarded-For` لعدم وجود آلية وكلاء موثوقة محددة. خريطة تتبع معدل الفتح محدودة (`MAX_TRACKED_CLIENTS` = 10000، تُنظَّف المدخلات المنتهية؛ عند امتلائها بعملاء نشطين يُرفض الجديد 429).


## المزود العام بلا مفتاح: بوابتان ومراقبة (R-145، SEC-06، D-059)

- **البوابة الأولى (المشغّل)**: `AGOS_LLM_KEYLESS` — معطّل افتراضياً؛ `1`/`true` يفعّله، غير مضبوط/`0`/`false`/`no` يعطّله، وأي قيمة أخرى **تمنع الإقلاع** (`KeylessConfigError` عند استيراد `server`). `render.yaml` لا يضبطه (آمن افتراضياً)؛ إن أراد المشغّل تفعيله فليدخل `1` في لوحة Render.
- **البوابة الثانية (الطلب)**: حتى بعد تفعيل المشغّل، لا يُرسل أي نص إلى المزود العام إلا بموافقة صريحة في الطلب نفسه (`allow_public_llm: true` — boolean صارم)؛ الحارس مركزي في `LLMClient.complete()` فلا مسار يتجاوزه (لا واجهة ولا مهمة خلفية ولا استدعاء مكتبة مباشر). البوابتان مستقلتان: موافقة المستخدم لا تتجاوز تعطيل المشغّل.
- **عقد الفشل**: مهمة تحتاج LLM بلا موافقة تُقبل (202) وتنتهي `failed` فوراً برسالة صريحة في `result.error` («public LLM provider not used: this request did not consent …»)؛ المهام التي لا تحتاج LLM تنجح كالمعتاد. لا إجابة ملفقة ولا نقطة نهاية بديلة.
- **المراقبة**: `keyless_stats` (`calls/ok/rate_limited/errors/busy`) كما كانت؛ `GET /api/system/status` يفصح `llm_provider`/`public_llm_enabled`/`public_llm_requires_consent` (غير سرية) لتعرض الواجهة تحذيرها بدقة. لا يُسجَّل نص رسالة ولا `prompt` ولا قيمة موافقة في السجلات.
- **تدفق البيانات (للإفصاح للمستخدم)**: عند تفعيل المزود العام وموافقة الطلب، يُرسل نص التعليمة ورسالة النظام إلى نقطة النهاية المضبوطة (`AGOS_LLM_KEYLESS_URL`، الافتراضي Pollinations) — وهي تسلمه بدورها لمزودي النماذج خلفها؛ سياسة كل جهة تخصها (راجع SECURITY.md — مراجعة 2026-10-16).

## حدود الاتصالات الصادرة: تثبيت عناوين DNS المفحوصة (R-146، SEC-05، D-062)

كل الاتصالات الصادرة المحمية (وكلاء الشبكة والبحث والترجمة والوكلاء الخارجيون والمزود العام بلا مفتاح عبر `safe_urlopen`/`ExternalAgentAdapter`) تتصل بعناوين IP جرى حلّها وفحصها **في عملية الاتصال نفسها** — لا حلّ DNS مستقلاً بعد الفحص (منع DNS rebinding):

- **السلوك**: حل واحد لكل اتصال → فحص **كل** عناوين الإجابة (إجابة مختلطة فيها عنوان داخلي تُرفض كاملة؛ وكل مدخل يجب أن يُفسر كعنوان IP — مدخل غير قابل للاستخدام يُرفض حتى مع `allow_private=True`) → الـsocket يفتح إلى sockaddr مفحوص. فشل حل DNS أو إجابة فارغة = رفض مغلق للطلب (رسالة ثابتة، لا تكرار للاستثناء الخام).
- **وكيل صريح على الطلب**: `Request.set_proxy` يُرفض قبل أي اتصال («explicit proxies are not supported») — لا يوجد دعم وكيل موثوق؛ النشر الذي يحتاج proxy مؤسسي عليك أن يفتح بند خارطة لتصميم يثبت الضمان.
- **عناوين IPv6 ذات النطاق (R-146b)**: مسار الوكلاء الخارجيين (aiohttp) **يرفض** أي إجابة DNS أو عنوان حرفي يحمل نطاقاً (scope) — حتى مع `AGOS_ALLOW_PRIVATE_NETWORK=1` — بسبب ثابت («not representable by the agent transport»): النقل يعيد بناء sockaddr من الحرفي، والحرفي ذو النطاق يسقط في حل غير مقيّد داخل مسار الاتصال. مسار `safe_urlopen` (urllib) يحفظ sockaddr الرقمي المفحوص ويعمل معها. أثر تشغيلي: وكيل خارجي على شبكة IPv6 بعناوين محلية النطاق (link-local مع scope) لن يعمل عبر مسار المحوّل — يُرجَّح عنوان قابل للتوجيه العام (أو allow_proxy مستقبلي بتصميم يثبت الضمان).
- **لا كاش DNS في مسار aiohttp**: كل اتصال جديد يمر عبر المحلل المقيد؛ إعادة استخدام اتصال قائم داخل الجلسة يعيد استخدام اتصال مفحوص سابقاً.
- **TLS بلا تغيير**: اسم المضيف الأصلي يبقى في ترويسة Host وSNI والتحقق من الشهادة — الاتصال بعنوان مفحوص لا يضعف التحقق من الهوية.
- **أثر تشغيلي — الوكلاء من البيئة**: اكتشاف proxies من متغيرات البيئة (`http_proxy`/`https_proxy`/`all_proxy` وأحرفها الكبيرة) **معطّل** لهذه الطلبات المحمية: وكيل يحلّ الوجهة بنفسه يبطل ضمان العنوان المفحوص. نشر يعتمد على proxy مؤسسي (خروج عبر بوابة إلزامية) لن تمرّ طلبات هذه المسارات عبره — ستفشل إذا كان الوصول للإنترنت ممكناً عبر الوكيل فقط. لا يوجد بديل «وكيل موثوق» مدعوم في هذه الدفعة (يتطلب تصميماً يثبت الضمان؛ إن لزم فليُفتح بند خارطة).
- **الإعدادات**: لا متغيرات بيئة جديدة؛ `AGOS_ALLOW_PRIVATE_NETWORK=1` واستثناء `allow_private=True` للوكلاء المسجلين إدارياً كما هما (الاستثناء يوسّع سياسة العناوين فقط — لا يتخطى فحص URL أو التحويلات).

## M8 — اختبارات الحمل والإنتاج (D-063، R-151)
- **لا اختبارات حمل عالية أو مكلفة أو مدمرة ضد الإنتاج العام أو مزودين خارجيين عامين** (مثل مزود LLM بلا مفتاح أو DuckDuckGo). سلالم P6 (R-176..R-181) تُنفذ في بيئة معزولة فقط وبتوقف آمن؛ البيئة والميزانية بقرار المالك (Q-023). اختبار القبول الحي R-182 على staging مماثلة للإنتاج لا على الإنتاج.
- لا تغيير تشغيلي في R-151 (توثيق فقط).

## الرصد: تشغيل وإيقاف (R-159، D-071)
- **الافتراضي: مُفعّل**. يكتب logger ‏`agos.telemetry` سطر JSON لكل span بمستوى INFO (المخطط `agos.telemetry/1`)، و`/metrics` يعرض `agos_ops_total` و`agos_op_duration_seconds`.
- **الإيقاف الفوري**: `AGOS_TELEMETRY=off` (أو 0/false/no) ثم إعادة تشغيل. يتوقف إصدار السجلات والمقاييس، والكلفة المتبقية بحجم تذبذب القياس (`102`).
- **إيقاف السجلات مع إبقاء المقاييس**: رفع مستوى logger ‏`agos.telemetry` إلى WARNING في إعداد السجلات. السجل لا يُبنى إلا إذا كان INFO مفعّلاً.
- **اختياري**: `AGOS_TELEMETRY_TENANT_KEY` يجعل الاسم المستعار للمستأجر ثابتاً عبر العمليات والإصدارات. إن غاب، فالاسم ثابت داخل العملية فقط. ليس سراً إلزامياً، ولم يُضبط في Render (قرار المالك).
- **الحجم والكلفة** (مقيسة محلياً، لا في الإنتاج):
  - نحو 2.0 كيلوبايت و6 سجلات لكل طلب `/api/chat` بسيط.
  - على مسار داخلي زمنه نحو 3.1 مللي ثانية: +0.36 مللي ثانية للـspans والمقاييس، و+0.57 مللي ثانية مع كتابة السجل.
- **ما لا يُسجل أبداً**: النص والـprompt والنتائج، والمفاتيح ورؤوس المصادقة، ونص الاستثناء، وعناوين URL بقيمها، ومعرّف المستأجر الخام.

## R-160 — ما يلاحظه المشغّل (D-072، 2026-10-17)
- `/health`: رسالة المكوّن الفاشل صارت `Error: <نوع الاستثناء>` (كانت نص الاستثناء). التشخيص التفصيلي من سجلات الخادم بنوع الخطأ و`/metrics`، لا من جسم `/health`.
- سجلات الطابور تطبع هدف Redis بصيغة `scheme://host:port` فقط (كان العنوان كاملاً، وقد يحمل كلمة مرور، عند كل `/health`).
- سجلات الاستثناءات في القوالب والوكلاء والطابور بنوع الاستثناء فقط. فحص محلي: `python3 scripts/scan_raw_exception_logs.py` (rc=1 عند موقع جديد غير مراجَع).
- إيقاف الخادم أثناء مهام جارية: سجلاتها في الذاكرة تنتهي `failed` بسبب «task cancelled before completion»؛ صفوف التخزين المقابلة قد تبقى `running` (G-160-2).
- فحص العقود محلياً: `python3 scripts/export_contract_schemas.py --check`؛ بعد تغيير مقصود: `python3 scripts/export_contract_schemas.py` ثم مراجعة الفرق ورفع الإصدار إن كان BREAKING.

## R-161 — التكامل الاختياري: محلي وحي (D-073، 2026-10-17)
المشغل: `python3 scripts/r161_live_integration.py --targets storage,llm,external,mcp --report r161_report.json`. بلا إعداد: كل هدف BLOCKED ورمز الخروج 2، وبلا `--targets` ‏NOT_RUN ورمز 3. لا يُحتسب أي منهما نجاحاً.

- **رموز الخروج**: 0 = كل المطلوب PASS؛ 1 = FAIL (فشل المزود، أو المهلة، أو الرفض، أو الاستجابة المشوهة، أو الإلغاء، أو فشل التنظيف)؛ 2 = BLOCKED (إعداد ناقص أو مرفوض)؛ 3 = لم يُشغَّل شيء؛ 64 = هدف غير معروف.
- **المتغيرات** (القيم لا تُطبع أبداً):

| المتغير | المعنى |
|---|---|
| `R161_STORAGE_URL` / `R161_STORAGE_KEY` | مشروع PostgREST/Supabase **للاختبار فقط** ومفتاح خدمته |
| `R161_LLM_URL` / `R161_LLM_API_KEY` / `R161_LLM_MODEL` | نقطة OpenAI-compatible (الافتراضي OpenRouter) ومفتاح اختبار |
| `R161_EXTERNAL_URL` / `R161_EXTERNAL_API_KEY` / `R161_EXTERNAL_MODEL` | أداة/وكيل خارجي OpenAI-compatible |
| `R161_LIVE=1` | يسمح بأي نقطة غير loopback (وإلا BLOCKED) |
| `R161_ALLOWED_HOSTS` | قائمة مضيفين مطابقة حرفياً، مفصولة بفواصل |
| `R161_CREDENTIAL_EXPIRES_AT` | وقت انتهاء بيانات الاعتماد بصيغة ISO-8601 مع منطقة، خلال 7 أيام |
| `R161_TIMEOUT_S` | مهلة كل هدف (1..300، افتراضي 30) |

- **محلياً (local_integration)**: `bash scripts/r042/run_local_stack.sh` (يتطلب `ADMIN_URI` و`AUTH_URI` و`POSTGREST_BIN`) يشغّل في نهايته المشغل على المخزن المحلي نفسه، ويكتب التقرير في `$R161_REPORT` (افتراضياً `/tmp/r161_storage_local.json`). في CI يُرفع ضمن artifact ‏`r042-storage-verification-log`.
- **حياً (live_integration) — إجراء المالك، لا يُنفذه الوكيل**:
  1. **البيئة (R-161b، D-074)**: المالك وحده يُعدّها. الخطوات، في Settings ← Environments ← New environment ← `r161-staging`:
     - Required reviewers: مراجع واحد على الأقل، ويفضل تفعيل Prevent self-review.
     - Deployment branches: «Selected branches» بقاعدة فرع واحدة `main` فقط.
     - بعد التحقق الفعلي من الإعداد: Settings ← Variables ← Repository variables ← `R161_STAGING_READY` = `owner-verified`.
     هذا المتغير **إقرار من المالك** لا إثبات بحد ذاته. لذلك وظيفة `gate` (بلا بيئة ولا أسرار) تتحقق أيضاً عبر API، قراءةً فقط، من وجود البيئة وأن فيها مراجعين وأن سياسة فرعها `main` فقط، ومن أن التشغيل على main.
     أي نقص يعني بوابة CLOSED: يفشل التشغيل ظاهراً بسبب مكتوب، وتُتخطى وظيفة `live`، ولا تُذكر البيئة في أي وظيفة. افحص محلياً: `GATE_REF=refs/heads/main GATE_READY=owner-verified GITHUB_REPOSITORY=reeveero-tech/All-hand python3 scripts/r161_staging_gate.py`.
     حد معروف (G-161-5): من يملك صلاحية الكتابة يستطيع تشغيل نسخة معدلة من الـworkflow من فرع آخر. سياسة فرع البيئة (`main` فقط) تمنع تلك النسخة من أسرار البيئة متى كانت البيئة موجودة.
  2. **بيانات اعتماد مؤقتة بصلاحية دنيا**: مشروع Supabase **مخصص للاختبار** (لا الإنتاج، ولا مشروع فيه بيانات حقيقية) بجدولي `tasks` و`events` من `docs/sql/`؛ ومفتاح LLM بحد إنفاق صغير وانتهاء قصير لدى المزود إن أتاحه. المفتاح لا يصير مؤقتاً بتسميته: الضمان هو انتهاؤه لدى المزود أو إبطاله يدوياً بعد التشغيل.
  3. **الأسرار** في البيئة: `R161_STORAGE_KEY`، و`R161_LLM_API_KEY`، و`R161_EXTERNAL_API_KEY`. **المتغيرات** (vars): `R161_ALLOWED_HOSTS`، و`R161_STORAGE_URL`، و`R161_LLM_URL`، و`R161_LLM_MODEL`، و`R161_EXTERNAL_URL`، و`R161_EXTERNAL_MODEL`.
  4. **التشغيل**: Actions ← «R-161 Live Integration (manual)» ← Run workflow، مع الأهداف ووقت الانتهاء الفعلي. الـjob يفشل ما لم يكن كل المطلوب PASS، والتقرير المنقح يُرفع artifact ‏`r161-integration-report` لمدة 30 يوماً.
  5. **التنظيف**: المشغل يحذف صفوف مستأجر التشغيل الاصطناعي فقط ويتحقق منه، وفشله يظهر FAIL. بعد التشغيل: إبطال المفاتيح أو تركها تنتهي، وحذف الأسرار من البيئة، وحذف مشروع الاختبار إن أُنشئ لهذا الغرض.
- **MCP**: دائماً BLOCKED — لا عميل MCP في المستودع (G-161-2).
- **تثبيت DNS (R-161b، G-161-3)**: المشغل يفتح كل اتصالات storage وLLM والتنظيف عبر `guarded_session_kwargs`: حلّ واحد مفحوص لكل اتصال، ولا ذاكرة DNS، وTLS بالاسم الأصلي، ولا تحويلات. نقطة تُرجع تحويلاً (3xx) تُسجل FAIL، فاستخدم الـURL النهائي للمزود مباشرة.
- **حالة R-161**: `blocked` بـQ-025 حتى يجتاز تشغيل يدوي فعلي بنتائج live_integration.
