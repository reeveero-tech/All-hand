# التشغيل والنشر (OPERATIONS)

> **آخر تحديث**: 2026-10-12 — بعد دفعة M4/1 (R-040)

## البناء والتشغيل المحلي

### الواجهة (`web/`)
```bash
cd web && npm ci --engine-strict && npm run build   # Node >=22.12.0 (R-089)
```
- مُتحقق: نجاح في 902ms ([log 08](../audit/M0/logs/08_web_build.log)). منذ R-089: `engines.node >=22.12.0` في `web/package.json` — Node 20 (منتهي الدعم) يُرفض بـ`EBADENGINE` (logs/74).

### الخادم
```bash
pip install -r requirements.txt
python -c "import server"   # ينجح (R-001 أصلح استيراد utc)
uvicorn server:app --host 0.0.0.0 --port 8000
# AGENTS_PER_SPEC=3 uvicorn server:app   # 60 وكيلاً بدل 1000 (R-041)
```

### واجهة الجذر (TanStack)
```bash
npm install && npm run build   # ناجح منذ M2/4 (rc=0)
```

## النشر

### Docker (من `Dockerfile`)
- مراحل: `node:22-slim` لبناء `web/` (منذ R-089، D-042؛ كان `node:20-slim`) بـ`npm ci --engine-strict` بلا بديل `npm install` صامت، ثم `python:3.12-slim` للتشغيل (Python 3.12 مختبر في CI منذ R-090)؛ يُخدم `web/dist` عبر `server.py`. منذ R-071: `web/` يستخدم vite 8 (يدعم `^20.19.0 || >=22.12.0`)؛ المشروع يثبّت `>=22.12.0`. **تطبيق الجذر (`src/`، TanStack Start) يتطلب Node `>=22.12.0`** (R-076 — معلن في `package.json` ومفروض في CI بـ`npm ci --engine-strict`)؛ ليس جزءاً من صورة Docker.
- **`.dockerignore`** (جديد في R-040): يمنع دخول `.git` و`node_modules` و`web/dist` والأسرار (`.env*`) في سياق `COPY . .` — كانت تغيب قبله فكان سياق البناء يشمل كل ملفات العمل.
- **التحقق (R-040، ✓ done — دفعة M4/1)**: job `docker` في CI تبني الصورة وتشغلها وتتحقق من `/health` (HTTP 200 + جسم JSON يحوي `status`)، مع جمع السجلات ورفعها كـ artifact. تحققت في تشغيلين: الأول (run 38285632672) على رأس `f02ed61` والثاني/النهائي (run 38285911497) على رأس `13fc37d` — كلاهما نجح. الأدلة مُرتكزة في `docs/audit/M4/logs/05..09`. بيئة التحرير المحلية بلا Docker (دليل `docs/audit/M4/logs/04`).

### Render (من `render.yaml` — محدث R-041)
- `PYTHON_VERSION=3.12.0` (يستهلكه Render لا كود التطبيق — Q-007)، `startCommand=uvicorn server:app`، `healthCheckPath=/api/system/status`.
- `AGOS_AGENT_IMPL` (R-032، D-037): `real` (افتراضي) أو `specialists` — الثاني يبني الوكلاء من أصناف `specialists/`. مقاس عند 1000 وكيل: بناء 0.152s مقابل 0.002s، ذاكرة قصوى 66.7MB مقابل 39.1MB، 1000/1000 نجاح في الحالتين (`docs/audit/M7/logs/08`). التراجع: إزالة المتغير وإعادة التشغيل. قيمة غير صالحة توقف البدء.
- `AGENTS_PER_SPEC=3`: **يُقرأ من الكود منذ R-041** (كان متغيراً ميتاً في الإعدادات). ملاءمة القيمة 3 للموارد لم تُقس — Q-013.
- مدخلا `AGOS_API_KEY` و`AGOS_ALLOWED_ORIGINS` أُضيفا كـ`sync: false` (المشغّل يدخل القيم في لوحة Render؛ لا سر في المستودع).
- `VITE_SUPABASE_*` بدائل خلفية يقرأها `supabase_store.py`؛ الواجهة `web/` لا تقرأها.

### CI (`ci.yml`)
- الوظائف (تحقق R-075 من `ci.yml`): quality (ruff F/E722) + test (pytest الافتراضي، بلا إنترنت — D-040) + build (web/ على Node 22 (R-089)؛ الجذر على Node 22: `npm ci --engine-strict` + eslint `--max-warnings=0` + build — R-070/R-076) + docker (R-040) + audit (pip-audit + npm audit لـweb/ والجذر، إنتاج وكامل، مانع — R-045/R-071/R-077) + storage (PostgreSQL + PostgREST حي — R-042) + network (`pytest -m network` على الإنترنت الحقيقي، غير إلزامي — D-040) + publish (معطل إلا بـ`[publish]` — D-011)؛ وسير عمل مستقل `governance-gate.yml` (R-020). الفحوص الإلزامية لحماية `main` (قراءة API): quality، test، build، docker، governance، audit — `storage` خارجها (Q-020) و`network` خارجها عمداً (D-040).
- `publish` يرفع إلى PyPI عند push لـ main مشروطاً بنجاح الوظائف السابقة ووسم `[publish]` في رسالة الدمج (D-011) — راجع RISK-009.

## متغيرات البيئة (المرصودة في الكود)

| المتغير | المكان | الافتراضي |
|---|---|---|
| `AGOS_API_KEY` | server.py | `""` |
| `AGOS_ALLOWED_ORIGINS` | server.py | `"*"` |
| `AGOS_RATE_LIMIT_REQUESTS` | server.py | 100 |
| `AGOS_RATE_LIMIT_WINDOW` | server.py | 60 |
| `SUPABASE_URL` / `VITE_SUPABASE_URL` | supabase_store.py | `""` |
| `SUPABASE_SERVICE_KEY` (مفضّل — D-039) ثم `SUPABASE_ANON_KEY` / `VITE_SUPABASE_ANON_KEY` (بديل للتوافق) | supabase_store.py | `""` |
| `OPENROUTER_API_KEY` | core/llm.py | `""` |
| `LLM_MODEL` | core/llm.py | `meta-llama/llama-3.3-70b-instruct:free` |
| `EXTERNAL_AGENTS` | agents/external_adapter.py | `""` |
| `REDIS_URL` | agos_kernel/infrastructure/redis_queue.py | `redis://localhost:6379` |
| `METRICS_NAMESPACE` | agos_kernel/infrastructure/metrics.py | `agos` |
| `CIRCUIT_BREAKER_*` (4 متغيرات) | agos_kernel/infrastructure | 5/3/60/3 |

**محدث R-041**: كل متغيرات `.env.example` موثقة ومقروءة في الكود (بما فيها `CIRCUIT_BREAKER_*` و`AGENTS_PER_SPEC` و`REDIS_URL` و`METRICS_NAMESPACE` و`VITE_API_BASE`).

## الفحوصات

`/health`, `/health/live`, `/health/ready`, `/metrics`, `/api/system/status`. `/health` يعيد 503 عند `unhealthy` و200 عند `healthy`/`degraded` (أُصلح في R-004؛ اختباران منذ R-068 — صُحح هنا في R-078، كان يقول "200 دائماً"). **R-078**: فحص `storage` يصير `degraded` إذا كان مفتاح Supabase عاماً، و`/api/system/status` يعرض `supabase_key: {kind, source}` (لا القيمة).

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
| القياسات تتوقف | 1000 وكيل/1000 مهمة غير مستقرين | `python3 scripts/run_r053_scale_audit.py` | راجع R-053 |

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
