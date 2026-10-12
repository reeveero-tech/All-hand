# دليل التحقق — M0 Baseline Audit

- **مرجع الكود**: `be480820b414b679ca7c5252d49e17257ca6d774` (فرع `main`)
- **بيئة التدقيق**: Python 3.14.3 وPython 3.12.13 (Linux)، Node v20.20.2، pytest 9.1.1، ruff 0.17.0، pytest-asyncio
- **تاريخ التدقيق**: 2026-10-12
- **ملاحظة**: Docker غير متوفر في بيئة التدقيق — بناء الصورة لم يُنفَّذ.

## السجلات (مقتطعة — راجع ملاحظة الاقتطاع داخل القسم)

كل أمر أعيد تشغيله في worktree نظيف عند SHA الأصلي بمشغّل يسجل الأمر وreturncode وstdout وstderr والمدة. **ملاحظة**: المخرجات مقتطعة إلى آخر ~20KB من stdout و~8KB من stderr (ليست كاملة)؛ الأوامر 06 و07 و09 أعيد توليدها بعد ذلك دون أنابيب ليكون returncode المسجل هو للأمر نفسه. السجلات في [`logs/`](logs/):

| السجل | الأمر | returncode |
|---|---|---|
| [01_import_server.log](logs/01_import_server.log) | `python -c "import server"` | 1 (ImportError) |
| [02_pytest_tests.log](logs/02_pytest_tests.log) | `pytest tests/` | 1 (9F/34P/18E) |
| [03_pytest_infra.log](logs/03_pytest_infra.log) | `pytest tests/test_infrastructure.py` | 1 (9F/10P) |
| [04_pytest_kernel_collect.log](logs/04_pytest_kernel_collect.log) | `pytest agos-kernel/ --collect-only` | 2 (145 collected + 4 errors) |
| [05_pytest_kernel_full.log](logs/05_pytest_kernel_full.log) | `pytest agos-kernel/` | **انتهى بمهلة 120 ثانية — تجربة غير مكتملة** |
| [06_ruff_kernel.log](logs/06_ruff_kernel.log) | `ruff check agos-kernel/ --no-cache` (دون أنبوب) | 1 (أخطاء lint) |
| [07_ruff_live.log](logs/07_ruff_live.log) | `ruff check` المسارات الحية (دون أنبوب) | 1 (أخطاء lint) |
| [08_web_build.log](logs/08_web_build.log) | `npm run build` في `web/` | 0 (نجاح، 902ms) |
| [09_root_build.log](logs/09_root_build.log) | `npm run build` في الجذر (دون أنبوب) | **1** (فشل: `lovable-error-reporting` غير موجود) |
| [10_versions.log](logs/10_versions.log) | إصدارات الأدوات | 0 |
| [11_git_inventory.log](logs/11_git_inventory.log) + [11_tracked_files_full.txt](logs/11_tracked_files_full.txt) | `git ls-tree -r --name-only HEAD` (ملخص + قائمة كاملة غير مقتطعة بـ1,981 ملفاً) | 0 |
| [12_entrypoints_deps.log](logs/12_entrypoints_deps.log) | grep متغيرات البيئة + ملفات الاعتماديات (`requirements.txt`, `package.json`) + نقاط الدخول | 0 لكل أمر (انظر السجل) |

## النتائج

### 1. جرد الملفات (عند SHA الأصلي)
- **الملفات المتتبعة**: 1,981 (راجع [11_git_inventory.log](logs/11_git_inventory.log)).
- **عدد الـ commits**: 180. آخر commit بتاريخ 2026-07-23.
- **أسطر Python الكلية**: ~166,053 LOC (منها 61,711 في `agent_civilization/`، معظمها في ملفات `specialists/`).
- **الملفات خارج `agent_civilization/agents/specialists/`**: 29 ملفاً، ~2,670 LOC (هذا عدّ ملفات، لا إثبات أنها كلها مستخدمة وقت التشغيل؛ المثبت هو سلسلة الاستيراد من `server.py`).
- **أسطر Markdown**: ~59,798 LOC (82 ملف `README.md`).
- **مدخلات مستوى الجذر**: 82 مدخلاً (55 دليلاً + 27 ملفاً/رابطاً). من بين الـ55 دليلاً، المسارات المثبتة الحياة: agent_civilization, agos_kernel, web, tests, docs. أكبرها بعدد الملفات المتتبعة: `agent_civilization` 1,050، `agos-kernel` 523، `knowledge-base` 120، `repository-analysis-report` 25 (راجع log 11).

### 2. الاختبارات (الخط الأساس — Python 3.14.3)

`pytest tests/`: **34 ناجح، 9 فاشل، 18 خطأ إعداد** (راجع [02_pytest_tests.log](logs/02_pytest_tests.log)).

**تفصيل الـ 18 خطأ إعداد**: اختبارات `test_api.py` جُمعت بنجاح لكنها فشلت في مرحلة setup عند استيراد `server.py` (خطأ `utc` — راجع النتيجة الحرجة رقم 1). ليست أخطاء جمع.

**تفصيل الـ 9 إخفاقات** (راجع [03_pytest_infra.log](logs/03_pytest_infra.log)):

| العدد | السبب الجذري |
|---|---|
| 6 | `CircuitBreaker.__init__() got an unexpected keyword argument 'failure_threshold'` — الاختبارات تمرر `failure_threshold` مباشرة بينما `CircuitBreaker` dataclass يقبل `name` و`config` فقط |
| 1 | `metrics.py:243` — `AttributeError: 'list' object has no attribute 'items'` في `test_metrics_export_prometheus` |
| 1 | `health` — `AssertionError: assert 'ValueError' in 'Error: Test error'` في `test_health_check_exception` |
| 1 | `redis_queue.py:406` — `TypeError: object of type 'Queue' has no len()` في `test_task_priority` |

### 3. مسار CI الحالي
`pytest agos-kernel/`:
- **الجمع**: 145 اختباراً + 4 أخطاء جمع (`test_integration.py`, `test_platform.py`, `test_v2.py`, `test_validation.py`) — راجع [04_pytest_kernel_collect.log](logs/04_pytest_kernel_collect.log).
- **التنفيذ الكامل**: انتهى بمهلة 120 ثانية — **تجربة غير مكتملة**، لا تُعد نتيجة شاملة (راجع [05_pytest_kernel_full.log](logs/05_pytest_kernel_full.log)).

**ملاحظة مهمة**: هذه نتائج إعادة تنفيذ محلية للأهداف المذكورة في `ci.yml`. لم يُتحقق من حالة تشغيل GitHub Actions الفعلي عن بُعد.

### 4. Lint (ruff)
- **`agos-kernel/` (مسار CI)**: 8,961 خطأ (7,200 قابل للإصلاح الآلي) — راجع [06_ruff_kernel.log](logs/06_ruff_kernel.log).
- **المسارات الحية** (`server.py` + `agent_civilization/` (بدون stubs) + `agos_kernel/` + `tests/`): 525 خطأ (395 قابل للإصلاح) — راجع [07_ruff_live.log](logs/07_ruff_live.log).
- `mypy` و`black` و`isort` لم تُشغَّل — مسجلة كفجوة في M0.

### 5. البناء
- **`web/` (واجهة React/Vite — التي يخدمها Docker)**: نجاح. `dist/index.html 0.76kB` + `assets/index-*.css 11.84kB` + `assets/index-*.js 164.19kB` في 902ms — راجع [08_web_build.log](logs/08_web_build.log).
- **الجذر (`src/` TanStack + root `package.json`)**: **فشل** (returncode 1 مباشراً دون أنبوب). `[UNRESOLVED_IMPORT] Could not resolve '../lib/lovable-error-reporting' in src/routes/__root.tsx` — الملف غير موجود في `src/lib/` (الموجود فقط `api.ts`) — راجع [09_root_build.log](logs/09_root_build.log).

### 6. النتيجة الحرجة رقم 1 — `server.py:28`
```python
from datetime import datetime, timedelta, utc
```
- `import server` فشل على Python 3.14.3 وPython 3.12.13 (`ImportError`).
- **التصحيح المنهجي**: `utc` ليس اسماً على مستوى الوحدة في `datetime` في هذه الإصدارات؛ الاسمان الصحيحان هما `datetime.UTC` و`datetime.timezone.utc`. لم نتحقق من سلوك Python 3.11 (مستخدمة في CI).
- **الأثر المُتحقق**: 18 خطأ إعداد في `tests/` + فشل `import server`.
- **الأثر الاستدلالي (غير مُتحقق)**: `uvicorn server:app` (startCommand في render.yaml مع `PYTHON_VERSION=3.12.0`) سيُفشل بدء التشغيل. Dockerfile يستخدم `python:3.12-slim`؛ لم يُنفَّذ بناء صورة (Docker غير متوفر)، لذا فشل `docker run` استدلال وليس قياساً.

### 7. تجربة تشخيصية (ليست الخط الأساس)
جُرّب استبدال مؤقت لسطر الاستيراد بـ `timezone.utc` في نسخة العمل:
- `pytest tests/` أعطت **51 ناجحاً، 10 فاشلة**.
- أعيد الملف إلى حالته الأصلية بعد التجربة (`git checkout -- server.py`).
- **هذه ليست نتيجة الخط الأساس** — سُجلت لبيان أن بقية الإخفاقات (10) مستقلة عن خطأ الاستيراد.

### 8. المصادقة (تحقق من الكود)
- `APIKeyHeader(name="X-API-Key", auto_error=False)` في `server.py:104`.
- `verify_api_key()` مُعرّف في `server.py:107` لكن لا يوجد استدعاء له في أي جسم دالة (بحث شامل).
- `create_task` و`chat` تستقبل `api_key` في التوقيع لكنه لا يُفحص داخل الجسم.
- `register_external_agent` و`remove_external_agent` يفحصان `ENABLE_AUTH` فقط (يرفضان بـ 401 إذا كانت المصادقة معطلة) — **لا يتحققان من قيمة المفتاح** حتى لو كانت المصادقة مفعلة.
- `ENABLE_AUTH = bool(API_KEY)` و`AGOS_API_KEY` فارغ افتراضياً → المصادقة معطلة افتراضياً.
- مسارات `/civilization/*` الثلاثة لا تستقبل `Security()` أصلاً.

### 9. الأمان (تحقق من الكود)
- **CORS**: `allow_origins=ALLOWED_ORIGINS` مع افتراضي `"*"`، و`allow_credentials=True` (تحذير يُسجل عند بدء التشغيل). لم نتحقق من قابلية الاستغلال.
- **Rate limiting**: `@rate_limit` decorator يخزن `request_counts: Dict[str, List[datetime]]` في الذاكرة (لا مشاركة بين العمليات) — راجع `server.py:70`.
- **الأسرار**: `.env.example` يحتوي قيماً placeholder فقط (`your_openrouter_api_key_here`) و`AGOS_API_KEY=` فارغاً. فحص أولي بأنماط مفاتيح شائعة على الملفات المتتبعة لم يُرجع نتائج — لم يكن فحصاً شاملاً ولا يُعتبر دليلاً قطعياً على خلو المستودع من الأسرار.

### 10. جرد API (تحقق من الكود)
`server.py` يعرّف 17 مساراً صريحاً بالأفعال `@app.get|post|delete` **بما فيها المسار الشامل** `@app.get("/{full_path:path}")`، إضافة إلى 3 مسارات `/civilization/*` مسجلة ديناميكياً (إجمالي 20):

| # | المسار | الفعل | المصادقة |
|---|---|---|---|
| 1 | `/api` | GET | لا |
| 2 | `/health` | GET | لا |
| 3 | `/health/live` | GET | لا |
| 4 | `/health/ready` | GET | لا |
| 5 | `/metrics` | GET | لا |
| 6 | `/api/system/status` | GET | لا |
| 7 | `/api/agents` | GET | لا |
| 8 | `/api/specializations` | GET | لا |
| 9 | `/api/tasks` | POST | غير مُفرضة |
| 10 | `/api/tasks` | GET | لا |
| 11 | `/api/tasks/{task_id}` | GET | لا |
| 12 | `/api/events` | GET | لا |
| 13 | `/api/events/stream` | GET (SSE) | لا |
| 14 | `/api/chat` | POST | غير مُفرضة |
| 15 | `/api/agents/external` | POST | يرفض 401 فقط إذا المصادقة معطلة؛ لا يتحقق من المفتاح |
| 16 | `/api/agents/external/{name}` | **DELETE** | يرفض 401 فقط إذا المصادقة معطلة؛ لا يتحقق من المفتاح |
| 17 | `/{full_path:path}` (catch-all، يخدم `web/dist`) | GET | لا |
| 18 | `/civilization/registry` | GET | لا |
| 19 | `/civilization/plan` | POST | لا |
| 20 | `/civilization/execute` | POST | لا |

**عيب مُتحقق في `/health`**: الدالة تحسب `status_code = 200 if ... else 503` لكنها تُعيد القاموس دون تمرير الرمز — استجابة HTTP ستكون 200 دائماً (راجع `server.py:290`).

### 11. التخزين والبيانات (تحقق من الكود)
- `get_storage()` تُعيد `SupabaseStorage` دائماً (`storage/supabase_store.py:191`).
- العمليات (`insert_task`, `get_tasks`, `insert_event`, ...) **تُعيد False أو `[]` بصمت** عند غياب إعداد Supabase أو فشل الطلب — لا توجد رسائل خطأ للمستخدم. "الرجوع للذاكرة" هو منطق صريح في `server.py` (مثلاً `list_tasks` تفحص `supabase_configured()` وتُرجع من `TASKS` dict إذا فشل الاستعلام).
- **لا توجد ملفات SQL أو migrations في المستودع**؛ مخطط الجداول (`tasks`, `events`, `agent_stats`) غير موثق.
- `OSSAgent` (dataclass في `registry/oss_agents.py`): `id, name, source_url, category, capabilities, license, adapter, description` — ~320 مشروع OSS منسق.

### 12. جرد الاعتماديات ونقاط الدخول
راجع [12_entrypoints_deps.log](logs/12_entrypoints_deps.log).

## ملخص النتائج

| الفحص | الحالة | الدليل |
|---|---|---|
| `pytest tests/` | 34 ناجح، **9 فاشل، 18 خطأ إعداد** | log 02 |
| `pytest agos-kernel/` (هدف CI) | 4 أخطاء جمع + تنفيذ لم يكتمل (مهلة) | logs 04, 05 |
| `ruff` على `agos-kernel/` (هدف CI) | فشل (rc=1، 8,961 خطأ) | log 06 |
| `ruff` على المسارات الحية | فشل (rc=1، 525 خطأ) | log 07 |
| `npm run build` في `web/` | نجاح (902ms) | log 08 |
| `npm run build` في الجذر | **فشل** (rc=1) | log 09 |
| `import server` | **فشل** على 3.12/3.14 | log 01 |
| Docker build | **لم يُنفَّذ** (Docker غير متوفر) | — |
